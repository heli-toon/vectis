"""Tests for Phase 6: Human-in-the-Loop (HITL) Guardrails & Tool RBAC."""

import json
import sqlite3
import pytest
from unittest.mock import MagicMock, patch
import config
from heartbeat import find_actionable_tickets
from agent_engine import process_ticket
from tools import (
    check_agent_tool_permission,
    dispatch_tool,
    execute_tool_calls,
    http_request,
)
from web_ui import VectisRequestHandler


def test_awaiting_approval_lifecycle(mem_db):
    """Verify tickets with requires_approval=1 enter AWAITING_APPROVAL and are held by heartbeat."""
    # Seed ticket with approval required
    cur = mem_db.execute(
        """INSERT INTO tickets (title, description, assigned_to, created_by, status, requires_approval)
           VALUES (?, ?, ?, ?, 'AWAITING_APPROVAL', 1)""",
        ("Deploy Production Release", "Deploy v0.3.0 to AWS cluster", "dev", "human"),
    )
    mem_db.commit()
    ticket_id = cur.lastrowid

    # Heartbeat must ignore AWAITING_APPROVAL ticket
    actionable = find_actionable_tickets(mem_db)
    actionable_ids = [t["id"] for t in actionable]
    assert ticket_id not in actionable_ids

    # process_ticket must skip if invoked directly
    process_ticket(mem_db, ticket_id)
    t = mem_db.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert t["status"] == "AWAITING_APPROVAL"


def test_cli_approve_and_reject(tmp_path, monkeypatch, capsys):
    """Verify CLI approve and reject commands."""
    import sys
    import main

    db_file = str(tmp_path / "cli_hitl_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    # Initialize db
    monkeypatch.setattr(sys, "argv", ["main.py", "init"])
    main.main()
    capsys.readouterr()

    # Seed 2 tickets awaiting approval
    monkeypatch.setattr(sys, "argv", ["main.py", "seed", "Deploy v0.3.0", "--desc", "Production release", "--approval"])
    main.main()
    out = capsys.readouterr().out
    assert "AWAITING_APPROVAL" in out

    monkeypatch.setattr(sys, "argv", ["main.py", "seed", "Drop Table", "--desc", "Dangerous task", "--approval"])
    main.main()
    capsys.readouterr()

    # Test approve ticket 1
    monkeypatch.setattr(sys, "argv", ["main.py", "tickets", "approve", "1"])
    main.main()
    approve_out = capsys.readouterr().out
    assert "approved -> status set to OPEN" in approve_out

    # Test reject ticket 2
    monkeypatch.setattr(sys, "argv", ["main.py", "tickets", "reject", "2", "--reason", "Security review failed"])
    main.main()
    reject_out = capsys.readouterr().out
    assert "rejected -> status set to FAILED" in reject_out

    # Verify state in database
    conn = config.get_db_connection()
    t1 = conn.execute("SELECT status FROM tickets WHERE id = 1").fetchone()
    assert t1["status"] == "OPEN"

    t2 = conn.execute("SELECT status, error_message FROM tickets WHERE id = 2").fetchone()
    assert t2["status"] == "FAILED"
    assert "Security review failed" in t2["error_message"]
    conn.close()


def test_rbac_agent_tool_permissions(mem_db):
    """Verify check_agent_tool_permission enforces agent allowed_tools ACLs."""
    # ceo only allowed 'search_knowledge'
    mem_db.execute("UPDATE agents SET allowed_tools = 'search_knowledge' WHERE id = 'ceo'")
    # dev allowed all tools
    mem_db.execute(
        "UPDATE agents SET allowed_tools = 'file_write,file_read,directory_list,execute_command,search_knowledge,http_request' WHERE id = 'dev'"
    )
    mem_db.commit()

    # CEO checks
    allowed_ceo, _ = check_agent_tool_permission(mem_db, "ceo", "search_knowledge")
    assert allowed_ceo is True

    denied_ceo, err = check_agent_tool_permission(mem_db, "ceo", "execute_command")
    assert denied_ceo is False
    assert "Permission denied" in err
    assert "ceo" in err

    # DEV checks
    allowed_dev, _ = check_agent_tool_permission(mem_db, "dev", "execute_command")
    assert allowed_dev is True
    allowed_dev_http, _ = check_agent_tool_permission(mem_db, "dev", "http_request")
    assert allowed_dev_http is True


def test_dispatch_tool_enforces_agent_rbac(mem_db, tmp_path):
    """Verify dispatch_tool blocks unauthorized tool calls and executes authorized ones."""
    mem_db.execute("UPDATE agents SET allowed_tools = 'file_read,search_knowledge' WHERE id = 'reviewer'")
    mem_db.commit()

    ws_dir = str(tmp_path)

    # Attempt execute_command as reviewer -> must be denied
    denied_res = dispatch_tool(
        ws_dir,
        "execute_command",
        {"command": "echo test"},
        conn=mem_db,
        agent_id="reviewer",
    )
    assert denied_res["success"] is False
    assert "Permission denied" in denied_res["error"]

    # Attempt search_knowledge as reviewer -> authorized
    allowed_res = dispatch_tool(
        ws_dir,
        "search_knowledge",
        {"query": "database"},
        conn=mem_db,
        agent_id="reviewer",
    )
    assert allowed_res["success"] is True

    # Standalone call with no conn or agent_id -> allowed for backwards compatibility
    compat_res = dispatch_tool(
        ws_dir,
        "directory_list",
        {},
    )
    assert compat_res["success"] is True


def test_web_ui_approve_and_reject_endpoints(mem_db):
    """Verify REST API endpoints for approving and rejecting tickets."""
    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status, requires_approval) VALUES ('HITL API Task', 'Needs approval', 'dev', 'ui', 'AWAITING_APPROVAL', 1)"
    )
    mem_db.commit()
    ticket_id = cur.lastrowid

    # Create dummy handler with mocked response sending
    handler = VectisRequestHandler.__new__(VectisRequestHandler)
    captured = {}

    def mock_send_json(data, status=200):
        captured["data"] = data
        captured["status"] = status

    handler._send_json = mock_send_json
    handler._send_error = lambda msg, status=400: mock_send_json({"error": msg}, status)

    # Test approve
    handler.handle_post_tickets_approve(mem_db, {"ticket_id": ticket_id})
    assert captured["status"] == 200
    assert captured["data"]["success"] is True

    t = mem_db.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert t["status"] == "OPEN"

    # Test reject
    handler.handle_post_tickets_reject(mem_db, {"ticket_id": ticket_id, "reason": "Operator rejected via dashboard"})
    assert captured["status"] == 200
    assert captured["data"]["success"] is True

    t_rej = mem_db.execute("SELECT status, error_message FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert t_rej["status"] == "FAILED"
    assert "Operator rejected" in t_rej["error_message"]


def test_http_request_tool(tmp_path):
    """Verify zero-dependency http_request tool behavior and error bounds."""
    ws_dir = str(tmp_path)

    # Invalid URL scheme
    inv = http_request(ws_dir, "ftp://example.com/file")
    assert inv["success"] is False
    assert "URL must start with http" in inv["error"]

    # Unsupported HTTP method
    unsupported = http_request(ws_dir, "https://httpbin.org/get", method="DELETE")
    assert unsupported["success"] is False
    assert "Unsupported HTTP method" in unsupported["error"]

    # Mocked successful GET request
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "ok", "message": "hello"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = http_request(ws_dir, "https://api.example.com/status")
        assert res["success"] is True
        assert res["status_code"] == 200
        assert "hello" in res["content"]
        assert res["truncated"] is False

    # Mocked truncation when response exceeds limit
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"A" * 100
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res_trunc = http_request(ws_dir, "https://api.example.com/large", max_bytes=50)
        assert res_trunc["success"] is True
        assert res_trunc["truncated"] is True
        assert len(res_trunc["content"]) == 50
