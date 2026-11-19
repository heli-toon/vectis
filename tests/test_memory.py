"""Tests for Phase 6: Institutional Memory and SQLite FTS5 RAG."""

import sqlite3
import pytest
from unittest.mock import MagicMock
import config
from memory import (
    sanitize_fts5_query,
    record_memory,
    search_memories,
    list_memories,
    get_memory,
)
from tools import search_knowledge, dispatch_tool
from agent_engine import build_memento_context, process_ticket


def test_sanitize_fts5_query():
    """Verify FTS5 query token sanitization and prefix formatting."""
    assert sanitize_fts5_query("") == ""
    assert sanitize_fts5_query("   ") == ""
    assert sanitize_fts5_query("!@#$%^&*()") == ""

    q = sanitize_fts5_query("FastAPI setup and router")
    assert '"FastAPI"*' in q
    assert '"setup"*' in q
    assert '"router"*' in q
    assert " OR " in q


def test_record_and_search_memories(mem_db):
    """Verify storing knowledge and querying with FTS5 BM25 ranking."""
    # Invalid inputs
    with pytest.raises(ValueError):
        record_memory(mem_db, "", "Some content")
    with pytest.raises(ValueError):
        record_memory(mem_db, "Title", "")

    # Seed valid tickets for FK relations
    cur1 = mem_db.execute("INSERT INTO tickets (title, description, assigned_to, created_by) VALUES ('T1', 'D1', 'dev', 'ceo')")
    cur2 = mem_db.execute("INSERT INTO tickets (title, description, assigned_to, created_by) VALUES ('T2', 'D2', 'dev', 'ceo')")
    mem_db.commit()
    t1_id, t2_id = cur1.lastrowid, cur2.lastrowid

    m1 = record_memory(
        mem_db,
        title="SQLite WAL Mode Concurrency",
        content="Enable PRAGMA journal_mode = WAL and busy_timeout = 5000 for thread-safe concurrent writes.",
        tags="database,performance,sqlite",
        agent_id="dev",
        ticket_id=t1_id,
    )
    assert m1 > 0

    m2 = record_memory(
        mem_db,
        title="Docker Multi-Stage Build Optimization",
        content="Use python:3.12-alpine to keep memory footprint under 65MB and avoid bloated dependencies.",
        tags="docker,alpine,devops",
        agent_id="dev",
        ticket_id=t2_id,
    )
    assert m2 > 0

    # Search for SQLite concurrency
    results = search_memories(mem_db, "SQLite concurrency WAL", limit=5)
    assert len(results) >= 1
    assert results[0]["id"] == m1
    assert "WAL" in results[0]["content"]

    # Search for Docker
    dock_results = search_memories(mem_db, "Docker alpine container", limit=5)
    assert len(dock_results) >= 1
    assert dock_results[0]["id"] == m2

    # Search with no matches
    nomatch = search_memories(mem_db, "Kubernetes helm chart ingress", limit=5)
    assert len(nomatch) == 0


def test_list_and_get_memory(mem_db):
    """Verify list_memories and get_memory lookups."""
    id1 = record_memory(mem_db, "First Note", "Content A", tags="alpha")
    id2 = record_memory(mem_db, "Second Note", "Content B", tags="beta")

    memories = list_memories(mem_db, limit=10)
    assert len(memories) >= 2
    assert memories[0]["id"] == id2  # newest first
    assert memories[1]["id"] == id1

    single = get_memory(mem_db, id1)
    assert single is not None
    assert single["title"] == "First Note"
    assert single["content"] == "Content A"

    assert get_memory(mem_db, 99999) is None


def test_fts5_triggers_sync(mem_db):
    """Verify FTS5 virtual table stays in sync with update and delete triggers."""
    m_id = record_memory(mem_db, "Initial Title", "Original text content", tags="test")

    # Verify searchable
    res = search_memories(mem_db, "Original", limit=1)
    assert len(res) == 1

    # Update memory
    mem_db.execute(
        "UPDATE memories SET title = 'Refactored Title', content = 'Completely revised text' WHERE id = ?",
        (m_id,),
    )
    mem_db.commit()

    # Old text should no longer match; new text should match
    assert len(search_memories(mem_db, "Original", limit=1)) == 0
    updated_res = search_memories(mem_db, "revised", limit=1)
    assert len(updated_res) == 1
    assert updated_res[0]["id"] == m_id

    # Delete memory
    mem_db.execute("DELETE FROM memories WHERE id = ?", (m_id,))
    mem_db.commit()
    assert len(search_memories(mem_db, "revised", limit=1)) == 0


def test_search_knowledge_tool(mem_db, tmp_path):
    """Verify search_knowledge tool execution."""
    record_memory(mem_db, "Architecture Rule", "Always maintain idle RAM under 35 MB.", tags="standards")

    ws_dir = str(tmp_path)
    res = search_knowledge(ws_dir, "RAM idle limit", limit=2, conn=mem_db)
    assert res["success"] is True
    assert res["count"] >= 1
    assert "35 MB" in res["memories"][0]["content"]

    # Dispatch via dispatch_tool
    dispatched = dispatch_tool(ws_dir, "search_knowledge", {"query": "RAM", "limit": 2}, conn=mem_db)
    assert dispatched["success"] is True
    assert dispatched["tool"] == "search_knowledge"


def test_memento_injects_institutional_knowledge(mem_db, tmp_path, monkeypatch):
    """Verify build_memento_context injects relevant past institutional memory."""
    monkeypatch.setattr(config, "WORKSPACES_DIR", str(tmp_path))

    record_memory(
        mem_db,
        title="Flask Blueprint Architecture",
        content="Organize routes with Flask Blueprints to avoid circular imports.",
        tags="architecture,flask",
    )

    ticket_row = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Implement Flask REST API", "Create blueprint structure for endpoints", "dev", "ceo"),
    )
    mem_db.commit()
    ticket_id = ticket_row.lastrowid

    ticket = mem_db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    agent = mem_db.execute("SELECT * FROM agents WHERE id = 'dev'").fetchone()

    messages = build_memento_context(mem_db, agent, ticket)
    prompt_text = messages[0]["content"]

    assert "## Relevant Institutional Knowledge" in prompt_text
    assert "Flask Blueprint Architecture" in prompt_text


def test_process_ticket_auto_distills_memory(mem_db, tmp_path, monkeypatch, make_mock_response):
    """Verify completed ticket results are automatically recorded into institutional memory."""
    monkeypatch.setattr(config, "WORKSPACES_DIR", str(tmp_path))

    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Design Database Caching", "Determine redis or sqlite caching pattern", "dev", "ceo"),
    )
    mem_db.commit()
    ticket_id = cursor.lastrowid

    # Mock LLM response marking ticket DONE with actionable result
    mock_llm_json = (
        '{"status": "DONE", "result": "Adopted SQLite in-memory cache with LRU strategy."}'
    )
    mock_resp = make_mock_response(mock_llm_json)

    with monkeypatch.context() as m:
        m.setattr("agent_engine.call_llm", lambda agent, msgs: mock_resp)
        process_ticket(mem_db, ticket_id)

    # Verify ticket state is DONE
    t = mem_db.execute("SELECT status, result FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert t["status"] == "DONE"

    # Verify institutional memory was created
    mems = search_memories(mem_db, "SQLite LRU caching", limit=5)
    assert len(mems) >= 1
    assert "Adopted SQLite in-memory cache" in mems[0]["content"]
    assert mems[0]["ticket_id"] == ticket_id
