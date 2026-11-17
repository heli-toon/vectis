"""Tests for Vectis embedded Web UI and REST API."""

import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import pytest

import config
from init_db import init_database
from web_ui import create_server


@pytest.fixture(scope="module")
def web_server(tmp_path_factory):
    """Spin up a test instance of Vectis HTTP Server on an ephemeral port."""
    tmp_dir = tmp_path_factory.mktemp("web_ui_test")
    db_file = str(tmp_dir / "test_web.db")

    # Initialize SQLite database with default agents
    orig_db = config.DB_PATH
    config.DB_PATH = db_file
    init_database()

    # Create some initial seed tickets and logs
    conn = config.get_db_connection(db_file)
    c1 = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) "
        "VALUES ('Root Architecture Task', 'Design the modular orchestrator', 'ceo', 'operator', 'REVIEW')"
    )
    root_id = c1.lastrowid

    c2 = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id, status) "
        "VALUES ('Implement Core Module', 'Code the engine routines', 'dev', 'ceo', ?, 'DONE')",
        (root_id,),
    )
    child_id = c2.lastrowid

    c3 = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id, status, retry_count, error_message) "
        "VALUES ('Faulty Subtask', 'Fails under test', 'dev', 'ceo', ?, 'FAILED', 3, 'Timeout in runner')",
        (root_id,),
    )
    failed_id = c3.lastrowid

    # Create workspace file for testing
    from tools import get_ticket_workspace, file_write
    ws_dir = get_ticket_workspace(conn, root_id, create=True)
    file_write(ws_dir, "solution.py", "print('hello from workspace')")

    # Insert an execution log with tool calls
    conn.execute(
        "INSERT INTO execution_logs (ticket_id, agent_id, prompt_tokens, completion_tokens, estimated_cost, execution_time_ms, tool_calls) "
        "VALUES (?, 'dev', 100, 50, 0.00025, 320, ?)",
        (child_id, json.dumps([{"tool": "file_write", "success": True, "result": {"bytes": 28}}])),
    )
    conn.commit()
    conn.close()

    # Find open port and start server
    server = create_server("127.0.0.1", 0, db_path=db_file)
    host, port = server.server_address
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://{host}:{port}"
    yield {
        "url": base_url,
        "db_path": db_file,
        "root_id": root_id,
        "child_id": child_id,
        "failed_id": failed_id,
    }

    server.shutdown()
    server.server_close()
    config.DB_PATH = orig_db


def request_json(url, method="GET", data=None):
    """Helper to perform HTTP JSON request."""
    headers = {"Content-Type": "application/json"} if data is not None else {}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            content = resp.read()
            return status, json.loads(content.decode("utf-8"))
    except urllib.error.HTTPError as e:
        content = e.read()
        try:
            parsed = json.loads(content.decode("utf-8"))
        except Exception:
            parsed = {"raw": content.decode("utf-8")}
        return e.code, parsed


def test_serve_dashboard(web_server):
    """Verify GET / returns 200 and the HTML dashboard."""
    req = urllib.request.Request(web_server["url"] + "/")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers["Content-Type"]
        content = resp.read().decode("utf-8")
        assert "Vectis" in content
        assert "Agent Organizational Hierarchy" in content


def test_api_status(web_server):
    """Verify GET /api/status returns system configuration and ticket counts."""
    status_code, data = request_json(web_server["url"] + "/api/status")
    assert status_code == 200
    assert data["total_tickets"] >= 3
    assert "status_counts" in data
    assert data["status_counts"].get("REVIEW") >= 1
    assert data["status_counts"].get("DONE") >= 1
    assert data["status_counts"].get("FAILED") >= 1
    assert data["total_agents"] == 4
    assert data["active_agents"] == 4
    assert "config" in data
    assert data["config"]["max_workers"] == config.MAX_WORKERS


def test_api_agents(web_server):
    """Verify GET /api/agents returns agent details with budget math."""
    status_code, data = request_json(web_server["url"] + "/api/agents")
    assert status_code == 200
    assert len(data) == 4
    agent_ids = [a["id"] for a in data]
    assert "ceo" in agent_ids
    assert "dev" in agent_ids

    dev = next(a for a in data if a["id"] == "dev")
    assert dev["budget_limit"] == 5.0
    assert "remaining_budget" in dev
    assert "spend_pct" in dev


def test_api_tickets_and_filtering(web_server):
    """Verify GET /api/tickets supports query filtering."""
    # All tickets
    status_code, data = request_json(web_server["url"] + "/api/tickets")
    assert status_code == 200
    assert len(data) >= 3

    # Filter by status = DONE
    status_code, done_tickets = request_json(web_server["url"] + "/api/tickets?status=DONE")
    assert status_code == 200
    assert all(t["status"] == "DONE" for t in done_tickets)
    assert any(t["id"] == web_server["child_id"] for t in done_tickets)

    # Filter by parent_ticket_id = null
    status_code, root_tickets = request_json(web_server["url"] + "/api/tickets?parent_ticket_id=null")
    assert status_code == 200
    assert all(t["parent_ticket_id"] is None for t in root_tickets)


def test_api_tree(web_server):
    """Verify GET /api/tree returns nested hierarchy."""
    status_code, data = request_json(web_server["url"] + "/api/tree")
    assert status_code == 200
    assert isinstance(data, list)
    root = next((r for r in data if r["id"] == web_server["root_id"]), None)
    assert root is not None
    assert len(root["children"]) >= 2
    child_ids = [c["id"] for c in root["children"]]
    assert web_server["child_id"] in child_ids
    assert web_server["failed_id"] in child_ids


def test_api_logs(web_server):
    """Verify GET /api/logs returns execution audit trail."""
    status_code, data = request_json(web_server["url"] + "/api/logs")
    assert status_code == 200
    assert len(data) >= 1
    log = data[0]
    assert "total_tokens" in log
    assert log["total_tokens"] == log["prompt_tokens"] + log["completion_tokens"]
    assert isinstance(log["tool_calls"], list)
    assert len(log["tool_calls"]) > 0
    assert log["tool_calls"][0]["tool"] == "file_write"


def test_api_workspaces(web_server):
    """Verify workspace directory listing and file inspection."""
    root_id = web_server["root_id"]
    status_code, data = request_json(web_server["url"] + f"/api/workspaces/{root_id}")
    assert status_code == 200
    assert data["exists"] is True
    assert any(e["path"] == "solution.py" for e in data["entries"])

    # Read the file
    status_code, file_data = request_json(web_server["url"] + f"/api/workspaces/{root_id}/file?path=solution.py")
    assert status_code == 200
    assert file_data["success"] is True
    assert "print('hello from workspace')" in file_data["content"]


def test_api_tickets_seed(web_server):
    """Verify POST /api/tickets/seed creates new root ticket."""
    payload = {
        "title": "Seed from Web API",
        "description": "Created via automated test",
        "assigned_to": "ceo",
    }
    status_code, data = request_json(web_server["url"] + "/api/tickets/seed", method="POST", data=payload)
    assert status_code == 200
    assert data["success"] is True
    new_id = data["ticket_id"]

    # Verify in tickets list
    status_code, tickets = request_json(web_server["url"] + f"/api/tickets?status=OPEN")
    assert any(t["id"] == new_id for t in tickets)


def test_api_tickets_retry_and_cancel(web_server):
    """Verify POST /api/tickets/retry and /api/tickets/cancel."""
    failed_id = web_server["failed_id"]

    # Retry the failed ticket
    status_code, data = request_json(
        web_server["url"] + "/api/tickets/retry", method="POST", data={"ticket_id": failed_id}
    )
    assert status_code == 200
    assert data["success"] is True

    # Check that status is now OPEN
    conn = config.get_db_connection(web_server["db_path"])
    t = conn.execute("SELECT * FROM tickets WHERE id = ?", (failed_id,)).fetchone()
    assert t["status"] == "OPEN"
    assert t["retry_count"] == 0
    assert t["error_message"] is None
    conn.close()

    # Cancel ticket
    status_code, data = request_json(
        web_server["url"] + "/api/tickets/cancel", method="POST", data={"ticket_id": failed_id}
    )
    assert status_code == 200
    assert data["success"] is True

    conn = config.get_db_connection(web_server["db_path"])
    t = conn.execute("SELECT * FROM tickets WHERE id = ?", (failed_id,)).fetchone()
    assert t["status"] == "FAILED"
    assert "Cancelled" in t["error_message"]
    conn.close()


def test_api_budget_topup_and_agent_toggle(web_server):
    """Verify budget topup and agent status toggling."""
    # Topup dev
    status_code, data = request_json(
        web_server["url"] + "/api/budget/topup", method="POST", data={"agent_id": "dev", "amount": 12.50}
    )
    assert status_code == 200
    assert data["success"] is True
    assert data["new_limit"] == 17.50

    # Toggle agent to DISABLED
    status_code, data = request_json(
        web_server["url"] + "/api/agents/toggle", method="POST", data={"agent_id": "dev", "status": "DISABLED"}
    )
    assert status_code == 200
    assert data["success"] is True
    assert data["status"] == "DISABLED"

    # Re-enable agent to ACTIVE
    status_code, data = request_json(
        web_server["url"] + "/api/agents/toggle", method="POST", data={"agent_id": "dev", "status": "ACTIVE"}
    )
    assert status_code == 200
    assert data["status"] == "ACTIVE"


def test_api_error_handling(web_server):
    """Verify 400, 404, and malformed request handling."""
    # 404 for unknown endpoint
    status_code, data = request_json(web_server["url"] + "/api/nonexistent")
    assert status_code == 404
    assert data["success"] is False

    # 400 for seed with missing title
    status_code, data = request_json(
        web_server["url"] + "/api/tickets/seed", method="POST", data={"assigned_to": "ceo"}
    )
    assert status_code == 400
    assert "required" in data["error"]

    # 400 for topup with negative amount
    status_code, data = request_json(
        web_server["url"] + "/api/budget/topup", method="POST", data={"agent_id": "dev", "amount": -5}
    )
    assert status_code == 400

    # 404 for non-existent ticket workspace
    status_code, data = request_json(web_server["url"] + "/api/workspaces/999999")
    assert status_code == 404
