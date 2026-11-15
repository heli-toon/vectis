"""Tests for CLI operations: budget topup, ticket retry/cancel, logs, and status."""

import sys
import io
import pytest
from unittest.mock import patch
import config
import main


def run_cli_args(args_list, monkeypatch, capsys):
    """Helper to invoke main.py CLI with captured output."""
    monkeypatch.setattr(sys, "argv", ["main.py"] + args_list)
    main.main()
    return capsys.readouterr()


def test_cli_budget_topup(tmp_path, monkeypatch, capsys):
    """Verify budget topup increases agent budget limit."""
    db_file = str(tmp_path / "cli_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    # Initialize db
    run_cli_args(["init"], monkeypatch, capsys)

    # Check initial limit for dev is 5.00
    conn = config.get_db_connection()
    dev = conn.execute("SELECT * FROM agents WHERE id = 'dev'").fetchone()
    assert dev["budget_limit"] == 5.00
    conn.close()

    # Topup dev by 15.00
    out = run_cli_args(["budget", "topup", "dev", "15.00"], monkeypatch, capsys)
    assert "Topped up budget for agent 'dev' by $15.00" in out.out
    assert "New limit: $20.00" in out.out

    # Verify db updated
    conn = config.get_db_connection()
    dev = conn.execute("SELECT * FROM agents WHERE id = 'dev'").fetchone()
    assert dev["budget_limit"] == 20.00
    conn.close()


def test_cli_budget_topup_resumes_paused_agent(tmp_path, monkeypatch, capsys):
    """Verify budget topup restores PAUSED_BUDGET agent and tickets."""
    db_file = str(tmp_path / "cli_resume_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    run_cli_args(["init"], monkeypatch, capsys)

    conn = config.get_db_connection()
    # Mark dev agent as PAUSED_BUDGET with limit exceeded
    conn.execute("UPDATE agents SET current_spend = 5.00, budget_limit = 5.00, status = 'PAUSED_BUDGET' WHERE id = 'dev'")
    # Create ticket in PAUSED_BUDGET
    conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Blocked Task', 'Waiting for funds', 'dev', 'ceo', 'PAUSED_BUDGET')"
    )
    conn.commit()
    conn.close()

    # Topup by $10
    out = run_cli_args(["budget", "topup", "dev", "10.00"], monkeypatch, capsys)
    assert "restored to ACTIVE" in out.out

    conn = config.get_db_connection()
    dev = conn.execute("SELECT * FROM agents WHERE id = 'dev'").fetchone()
    assert dev["status"] == "ACTIVE"
    ticket = conn.execute("SELECT * FROM tickets WHERE assigned_to = 'dev'").fetchone()
    assert ticket["status"] == "OPEN"
    conn.close()


def test_cli_tickets_retry(tmp_path, monkeypatch, capsys):
    """Verify tickets retry resets retry count, error message, and sets status to OPEN."""
    db_file = str(tmp_path / "cli_retry_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    run_cli_args(["init"], monkeypatch, capsys)

    conn = config.get_db_connection()
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status, retry_count, error_message) "
        "VALUES ('Failed Task', 'Needs retry', 'dev', 'ceo', 'FAILED', 3, 'Timeout')"
    )
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()

    out = run_cli_args(["tickets", "retry", str(ticket_id)], monkeypatch, capsys)
    assert f"Ticket #{ticket_id} reset to OPEN with retry_count=0" in out.out

    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert ticket["status"] == "OPEN"
    assert ticket["retry_count"] == 0
    assert ticket["error_message"] is None
    conn.close()


def test_cli_tickets_cancel(tmp_path, monkeypatch, capsys):
    """Verify tickets cancel sets status to FAILED."""
    db_file = str(tmp_path / "cli_cancel_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    run_cli_args(["init"], monkeypatch, capsys)

    conn = config.get_db_connection()
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) "
        "VALUES ('Stalled Task', 'Cancel me', 'dev', 'ceo', 'OPEN')"
    )
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()

    out = run_cli_args(["tickets", "cancel", str(ticket_id)], monkeypatch, capsys)
    assert f"Ticket #{ticket_id} cancelled (status: FAILED)" in out.out

    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert ticket["status"] == "FAILED"
    assert "Cancelled by operator" in ticket["error_message"]
    conn.close()


def test_cli_logs_display(tmp_path, monkeypatch, capsys):
    """Verify logs command prints ticket details and execution logs."""
    db_file = str(tmp_path / "cli_logs_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    run_cli_args(["init"], monkeypatch, capsys)

    conn = config.get_db_connection()
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) "
        "VALUES ('Logged Task', 'Log test', 'dev', 'ceo', 'DONE')"
    )
    ticket_id = cursor.lastrowid

    conn.execute(
        "INSERT INTO execution_logs (ticket_id, agent_id, prompt_tokens, completion_tokens, estimated_cost, execution_time_ms) "
        "VALUES (?, 'dev', 250, 75, 0.0005, 120)",
        (ticket_id,),
    )
    conn.commit()
    conn.close()

    out = run_cli_args(["logs", str(ticket_id)], monkeypatch, capsys)
    assert f"Execution Logs for Ticket #{ticket_id}: Logged Task" in out.out
    assert "dev" in out.out
    assert "250" in out.out
    assert "75" in out.out
    assert "120ms" in out.out
    assert "TOTAL" in out.out


def test_cli_workspace_display(tmp_path, monkeypatch, capsys):
    """Verify workspace CLI displays path and files."""
    import os
    import tools
    db_file = str(tmp_path / "cli_ws_test.db")
    ws_dir = str(tmp_path / "workspaces")
    monkeypatch.setattr(config, "DB_PATH", db_file)
    monkeypatch.setattr(config, "WORKSPACES_DIR", ws_dir)

    run_cli_args(["init"], monkeypatch, capsys)

    conn = config.get_db_connection()
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES ('WS Task', 'desc', 'dev', 'ceo')"
    )
    ticket_id = cursor.lastrowid
    conn.commit()

    # Pre-create a file in ticket workspace
    ticket_ws = tools.get_ticket_workspace(conn, ticket_id, create=True)
    tools.file_write(ticket_ws, "deliverable.py", "print('hello')\n")
    conn.close()

    out = run_cli_args(["workspace", str(ticket_id)], monkeypatch, capsys)
    assert f"Workspace for Ticket #{ticket_id}" in out.out
    assert ticket_ws in out.out
    assert "deliverable.py" in out.out


def test_cli_logs_with_tool_calls(tmp_path, monkeypatch, capsys):
    """Verify logs CLI prints tool execution summaries."""
    import json
    db_file = str(tmp_path / "cli_tool_logs_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    run_cli_args(["init"], monkeypatch, capsys)

    conn = config.get_db_connection()
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) "
        "VALUES ('Tool Task', 'Tools used', 'dev', 'ceo', 'DONE')"
    )
    ticket_id = cursor.lastrowid

    tool_data = json.dumps([
        {"tool": "file_write", "success": True},
        {"tool": "execute_command", "success": True},
    ])
    conn.execute(
        "INSERT INTO execution_logs (ticket_id, agent_id, prompt_tokens, completion_tokens, estimated_cost, execution_time_ms, tool_calls) "
        "VALUES (?, 'dev', 300, 100, 0.001, 150, ?)",
        (ticket_id, tool_data),
    )
    conn.commit()
    conn.close()

    out = run_cli_args(["logs", str(ticket_id)], monkeypatch, capsys)
    assert "tools: file_write:ok, execute_command:ok" in out.out

