"""Tests for Phase 3: High-Efficiency Concurrency, Worker Pool, and Thread Safety."""

import os
import sys
import time
import sqlite3
import threading
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock
import pytest

import config
import heartbeat
from agent_engine import claim_ticket, process_ticket
import main


@pytest.fixture
def file_db(tmp_path):
    """Create a temporary file-backed SQLite database with schema and default agents."""
    db_file = str(tmp_path / "vectis_concurrency.db")
    schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")

    conn = config.get_db_connection(db_file)
    with open(schema_path) as f:
        conn.executescript(f.read())

    from tests.conftest import DEFAULT_TEST_AGENTS
    for agent in DEFAULT_TEST_AGENTS:
        conn.execute(
            """INSERT INTO agents (id, name, role, system_prompt, model_name, manager_id, budget_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                agent["id"],
                agent["name"],
                agent["role"],
                agent["system_prompt"],
                agent["model_name"],
                agent["manager_id"],
                agent["budget_limit"],
            ),
        )
    conn.commit()
    conn.close()
    return db_file


def test_thread_local_connections(file_db):
    """Verify that worker threads acquire distinct thread-local SQLite connections."""
    connections = {}
    thread_ids = []

    def worker(worker_id):
        conn = config.get_thread_connection(file_db)
        connections[worker_id] = conn
        thread_ids.append(threading.get_ident())
        # Re-fetching on the same thread should return the exact same connection object
        conn_same = config.get_thread_connection(file_db)
        assert conn is conn_same
        # Verify connection is functional
        row = conn.execute("SELECT COUNT(*) as count FROM agents").fetchone()
        assert row["count"] == 4
        config.close_thread_connections()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify that different threads had distinct connection objects
    conn_list = list(connections.values())
    assert len(conn_list) == 4
    for i in range(len(conn_list)):
        for j in range(i + 1, len(conn_list)):
            assert conn_list[i] is not conn_list[j]


def test_claim_ticket_atomic_race(file_db):
    """Verify atomic ticket claiming prevents race conditions when multiple workers collide."""
    conn = config.get_db_connection(file_db)
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) "
        "VALUES ('Contended Task', 'Race test', 'dev', 'ceo', 'OPEN')"
    )
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()

    results = []
    barrier = threading.Barrier(8)

    def worker():
        thread_conn = config.get_db_connection(file_db)
        barrier.wait()  # Synchronize all threads to claim simultaneously
        claimed = claim_ticket(thread_conn, ticket_id)
        results.append(claimed)
        thread_conn.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread must have claimed the ticket
    assert results.count(True) == 1
    assert results.count(False) == 7

    # Verify ticket state is IN_PROGRESS
    verify_conn = config.get_db_connection(file_db)
    ticket = verify_conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert ticket["status"] == "IN_PROGRESS"
    verify_conn.close()


def test_select_ticket_batch_agent_affinity(mem_db):
    """Verify batch selection limits to at most 1 ticket per agent per tick."""
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Dev Task 1', 'desc', 'dev', 'ceo', 'OPEN'),"
        "('Dev Task 2', 'desc', 'dev', 'ceo', 'OPEN'),"
        "('Copy Task', 'desc', 'copywriter', 'ceo', 'OPEN'),"
        "('QA Task', 'desc', 'reviewer', 'ceo', 'OPEN')"
    )
    mem_db.commit()

    actionable = heartbeat.find_actionable_tickets(mem_db)
    assert len(actionable) == 4

    batch = heartbeat.select_ticket_batch(actionable, max_workers=4)
    # Batch should pick 3 tickets (1 dev, 1 copywriter, 1 reviewer); Dev Task 2 waits
    assert len(batch) == 3
    assignees = [t["assigned_to"] for t in batch]
    assert assignees.count("dev") == 1
    assert "copywriter" in assignees
    assert "reviewer" in assignees


def test_find_actionable_tickets_ignores_in_progress_agents(mem_db):
    """Verify agents with IN_PROGRESS tickets are not scheduled additional tickets."""
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Dev Active', 'desc', 'dev', 'ceo', 'IN_PROGRESS'),"
        "('Dev Queued', 'desc', 'dev', 'ceo', 'OPEN'),"
        "('Copy Task', 'desc', 'copywriter', 'ceo', 'OPEN')"
    )
    mem_db.commit()

    actionable = heartbeat.find_actionable_tickets(mem_db)
    # dev is busy with an IN_PROGRESS ticket, so only copywriter task is actionable
    assert len(actionable) == 1
    assert actionable[0]["assigned_to"] == "copywriter"


def test_run_heartbeat_concurrent_workers(file_db, make_mock_response):
    """Verify run_heartbeat executes independent tickets concurrently across worker threads."""
    conn = config.get_db_connection(file_db)
    conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Dev Work', 'desc', 'dev', 'ceo', 'OPEN'),"
        "('Copy Work', 'desc', 'copywriter', 'ceo', 'OPEN'),"
        "('QA Work', 'desc', 'reviewer', 'ceo', 'OPEN')"
    )
    conn.commit()
    conn.close()

    thread_ids = set()
    lock = threading.Lock()

    def mock_completion(**kwargs):
        with lock:
            thread_ids.add(threading.get_ident())
        time.sleep(0.05)  # Simulate LLM latency
        return make_mock_response('{"status": "DONE", "result": "Work finished"}')

    with patch("agent_engine.litellm.completion", side_effect=mock_completion):
        processed = heartbeat.run_heartbeat(max_workers=3, db_path=file_db)

    assert processed == 3
    # Multiple distinct threads must have executed the tasks
    assert len(thread_ids) >= 2

    # Verify all tickets reached DONE
    verify_conn = config.get_db_connection(file_db)
    statuses = [
        row["status"]
        for row in verify_conn.execute("SELECT status FROM tickets ORDER BY id").fetchall()
    ]
    assert statuses == ["DONE", "DONE", "DONE"]

    # Verify execution logs recorded for all 3
    logs = verify_conn.execute("SELECT COUNT(*) as count FROM execution_logs").fetchone()
    assert logs["count"] == 3
    verify_conn.close()


def test_worker_exception_resilience(file_db, make_mock_response):
    """Verify that an exception in one worker thread does not crash the heartbeat or other tasks."""
    conn = config.get_db_connection(file_db)
    conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Failing Task', 'desc', 'dev', 'ceo', 'OPEN'),"
        "('Succeeding Task', 'desc', 'copywriter', 'ceo', 'OPEN')"
    )
    conn.commit()
    conn.close()

    def mock_completion(**kwargs):
        model = kwargs.get("model", "")
        # dev agent uses groq/llama-3.3-70b
        if "groq" in model or "llama" in model:
            raise RuntimeError("Simulated API failure for dev")
        return make_mock_response('{"status": "DONE", "result": "Documentation complete"}')

    with patch("agent_engine.litellm.completion", side_effect=mock_completion):
        processed = heartbeat.run_heartbeat(max_workers=2, db_path=file_db)

    assert processed == 2

    verify_conn = config.get_db_connection(file_db)
    tickets = {
        row["title"]: row["status"]
        for row in verify_conn.execute("SELECT title, status FROM tickets").fetchall()
    }
    # Failing task was caught by retry handler and reverted to OPEN (attempt 1/3)
    assert tickets["Failing Task"] == "OPEN"
    # Succeeding task completed cleanly
    assert tickets["Succeeding Task"] == "DONE"
    verify_conn.close()


def test_cli_run_workers_argument(tmp_path, monkeypatch, capsys):
    """Verify CLI --workers flag is forwarded to heartbeat runner."""
    db_file = str(tmp_path / "cli_workers.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    # Initialize db
    monkeypatch.setattr(sys, "argv", ["main.py", "init"])
    main.main()
    capsys.readouterr()

    # Test run --once --workers 3
    with patch("heartbeat.run_single_heartbeat") as mock_single:
        monkeypatch.setattr(sys, "argv", ["main.py", "run", "--once", "--workers", "3"])
        main.main()
        mock_single.assert_called_once_with(max_workers=3)


def test_cli_status_shows_concurrency(tmp_path, monkeypatch, capsys):
    """Verify CLI status displays engine worker concurrency and configuration."""
    db_file = str(tmp_path / "cli_status_conf.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    monkeypatch.setattr(sys, "argv", ["main.py", "init"])
    main.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["main.py", "status"])
    main.main()
    out = capsys.readouterr().out

    assert "Engine Configuration:" in out
    assert f"Workers: {config.MAX_WORKERS}" in out
    assert f"Poll Interval: {config.POLL_INTERVAL_SECONDS}s" in out


def test_memory_overhead_under_ceiling(file_db, make_mock_response):
    """Verify memory overhead during concurrent multi-agent execution stays well under 65 MB."""
    conn = config.get_db_connection(file_db)
    conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Task 1', 'desc', 'dev', 'ceo', 'OPEN'),"
        "('Task 2', 'desc', 'copywriter', 'ceo', 'OPEN'),"
        "('Task 3', 'desc', 'reviewer', 'ceo', 'OPEN')"
    )
    conn.commit()
    conn.close()

    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    def mock_completion(**kwargs):
        time.sleep(0.02)
        return make_mock_response('{"status": "DONE", "result": "Done"}')

    with patch("agent_engine.litellm.completion", side_effect=mock_completion):
        heartbeat.run_heartbeat(max_workers=4, db_path=file_db)

    snapshot_after = tracemalloc.take_snapshot()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak / (1024 * 1024)
    # Peak traced memory for standard library execution should be < 15 MB, well below 65 MB
    assert peak_mb < 65.0, f"Peak memory {peak_mb:.2f} MB exceeded 65 MB ceiling"
