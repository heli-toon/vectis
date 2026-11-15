"""Tests for agent engine: context assembly, response parser, delegation, and state machine DLQ."""

import pytest
from unittest.mock import patch
import agent_engine


def test_build_memento_context_org_and_budget(mem_db):
    """Verify memento context reflects hierarchy and budget."""
    ceo = agent_engine.get_agent(mem_db, "ceo")
    dev = agent_engine.get_agent(mem_db, "dev")

    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Test Ticket", "A test assignment", "ceo", "human"),
    )
    ticket_id = cursor.lastrowid
    ticket = agent_engine.get_ticket(mem_db, ticket_id)

    # CEO context
    messages_ceo = agent_engine.build_memento_context(mem_db, ceo, ticket)
    prompt_ceo = messages_ceo[0]["content"]
    assert "You are Chief Executive" in prompt_ceo
    assert "You report to no one" in prompt_ceo
    assert "dev" in prompt_ceo
    assert "Limit: $10.00" in prompt_ceo

    # Dev context
    dev_cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Dev Task", "Code task", "dev", "ceo"),
    )
    dev_ticket = agent_engine.get_ticket(mem_db, dev_cursor.lastrowid)
    messages_dev = agent_engine.build_memento_context(mem_db, dev, dev_ticket)
    prompt_dev = messages_dev[0]["content"]
    assert "You report to: Chief Executive" in prompt_dev
    assert "You have no direct subordinates" in prompt_dev


def test_build_memento_context_subtask_failure_injection(mem_db):
    """Verify failed sub-tasks inject explicit error guidance into parent context."""
    ceo = agent_engine.get_agent(mem_db, "ceo")

    # Parent ticket
    parent_cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES (?, ?, ?, ?, 'REVIEW')",
        ("Master Project", "Parent task", "ceo", "human"),
    )
    parent_id = parent_cursor.lastrowid

    # Child ticket 1: DONE
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id, status, result) "
        "VALUES (?, ?, ?, ?, ?, 'DONE', ?)",
        ("Subtask 1", "Work 1", "dev", "ceo", parent_id, "Completed feature A"),
    )

    # Child ticket 2: FAILED with error
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id, status, result, error_message) "
        "VALUES (?, ?, ?, ?, ?, 'FAILED', ?, ?)",
        ("Subtask 2", "Work 2", "copywriter", "ceo", parent_id, "Syntax error", "API connection timeout after 3 retries"),
    )
    mem_db.commit()

    parent_ticket = agent_engine.get_ticket(mem_db, parent_id)
    messages = agent_engine.build_memento_context(mem_db, ceo, parent_ticket)
    prompt = messages[0]["content"]

    assert "## Sub-Task Results" in prompt
    assert "Subtask 1" in prompt
    assert "[FAILED]: Subtask 2" in prompt
    assert "API connection timeout after 3 retries" in prompt
    assert "Decide whether to re-assign, modify parameters, or fail the parent task." in prompt


def test_build_memento_context_execution_history(mem_db):
    """Verify execution history formats latency and tokens."""
    dev = agent_engine.get_agent(mem_db, "dev")
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Task with logs", "Desc", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid
    ticket = agent_engine.get_ticket(mem_db, ticket_id)

    agent_engine.log_execution(mem_db, ticket_id, "dev", 500, 150, 0.0012, execution_time_ms=320)

    messages = agent_engine.build_memento_context(mem_db, dev, ticket)
    prompt = messages[0]["content"]

    assert "## Execution History" in prompt
    assert "500 input + 150 output tokens" in prompt
    assert "$0.0012, 320ms" in prompt


def test_parse_agent_response_valid():
    """Verify standard clean JSON parsing."""
    raw = '{"status": "DONE", "result": "All good", "new_tickets": []}'
    parsed = agent_engine.parse_agent_response(raw)
    assert parsed["status"] == "DONE"
    assert parsed["result"] == "All good"


def test_parse_agent_response_markdown_blocks():
    """Verify JSON wrapped inside markdown code blocks."""
    raw_with_tag = '```json\n{"status": "DONE", "result": "Wrapped in tag"}\n```'
    assert agent_engine.parse_agent_response(raw_with_tag)["result"] == "Wrapped in tag"

    raw_no_tag = '```\n{"status": "DONE", "result": "No tag"}\n```'
    assert agent_engine.parse_agent_response(raw_no_tag)["result"] == "No tag"

    raw_surrounded = (
        'Here is the analysis you requested:\n\n'
        '```json\n{"status": "DONE", "result": "Surrounded"}\n```\n\n'
        'Let me know if you need changes.'
    )
    assert agent_engine.parse_agent_response(raw_surrounded)["result"] == "Surrounded"


def test_parse_agent_response_trailing_commas():
    """Verify trailing commas in objects and arrays are handled."""
    raw_trailing = """
    {
        "status": "DONE",
        "result": "Trailing commas",
        "new_tickets": [
            {"title": "Sub", "assigned_to": "dev",},
        ],
    }
    """
    parsed = agent_engine.parse_agent_response(raw_trailing)
    assert parsed["status"] == "DONE"
    assert len(parsed["new_tickets"]) == 1


def test_parse_agent_response_invalid_raises():
    """Verify invalid payloads raise ValueError."""
    with pytest.raises(ValueError):
        agent_engine.parse_agent_response("")

    with pytest.raises(ValueError):
        agent_engine.parse_agent_response("This is not JSON at all.")


def test_spawn_sub_tickets_validation(mem_db):
    """Verify sub-tickets can only be assigned to direct subordinates."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("CEO task", "Delegation test", "ceo", "human"),
    )
    parent_id = cursor.lastrowid

    # dev is a valid subordinate of ceo
    # random_agent is not a subordinate
    new_tickets = [
        {"title": "Valid Sub", "description": "Desc", "assigned_to": "dev"},
        {"title": "Invalid Sub", "description": "Desc", "assigned_to": "random_agent"},
    ]
    spawned = agent_engine.spawn_sub_tickets(mem_db, parent_id, "ceo", new_tickets)
    assert spawned == 1

    children = agent_engine.get_child_tickets(mem_db, parent_id)
    assert len(children) == 1
    assert children[0]["title"] == "Valid Sub"

    # Dev has no subordinates, attempting delegation fails
    dev_spawned = agent_engine.spawn_sub_tickets(
        mem_db, children[0]["id"], "dev", [{"title": "Dev sub", "assigned_to": "copywriter"}]
    )
    assert dev_spawned == 0


def test_process_ticket_success(mem_db, make_mock_response):
    """Verify successful ticket execution transitions to DONE."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Dev Task", "Implement feature", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid

    mock_resp = make_mock_response('{"status": "DONE", "result": "Code implemented"}', 200, 100)

    with patch("agent_engine.call_llm", return_value=mock_resp):
        agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    assert ticket["status"] == "DONE"
    assert ticket["result"] == "Code implemented"
    assert ticket["retry_count"] == 0
    assert ticket["error_message"] is None

    # Check execution log
    logs = agent_engine.get_execution_logs(mem_db, ticket_id)
    assert len(logs) == 1
    assert logs[0]["prompt_tokens"] == 200
    assert logs[0]["completion_tokens"] == 100
    assert logs[0]["execution_time_ms"] >= 0


def test_process_ticket_delegation_review(mem_db, make_mock_response):
    """Verify ticket delegating subtasks transitions to REVIEW."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("CEO Orchestration", "Delegate project", "ceo", "human"),
    )
    ticket_id = cursor.lastrowid

    payload = """
    {
        "status": "REVIEW",
        "result": "Delegated implementation to dev",
        "new_tickets": [
            {"title": "Implement core", "description": "Write core module", "assigned_to": "dev"}
        ]
    }
    """
    mock_resp = make_mock_response(payload)

    with patch("agent_engine.call_llm", return_value=mock_resp):
        agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    assert ticket["status"] == "REVIEW"

    children = agent_engine.get_child_tickets(mem_db, ticket_id)
    assert len(children) == 1
    assert children[0]["title"] == "Implement core"
    assert children[0]["assigned_to"] == "dev"


def test_process_ticket_retry_increment_on_error(mem_db):
    """Verify LLM failure increments retry_count and returns to OPEN when under max_retries."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, retry_count, max_retries) VALUES (?, ?, ?, ?, 0, 3)",
        ("Flaky Task", "Will fail", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid

    with patch("agent_engine.call_llm", side_effect=RuntimeError("LiteLLM connection error")):
        agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    assert ticket["status"] == "OPEN"
    assert ticket["retry_count"] == 1
    assert "LiteLLM connection error" in ticket["error_message"]


def test_process_ticket_dlq_transition(mem_db):
    """Verify ticket transitions to FAILED when retry_count reaches max_retries (DLQ)."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, retry_count, max_retries) VALUES (?, ?, ?, ?, 2, 3)",
        ("Doomed Task", "Will fail permanently", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid

    with patch("agent_engine.call_llm", side_effect=ValueError("Persistent API error")):
        agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    assert ticket["status"] == "FAILED"
    assert ticket["retry_count"] == 3
    assert "Persistent API error" in ticket["error_message"]


def test_process_ticket_json_parse_failure_dlq(mem_db, make_mock_response):
    """Verify parse failure increments retries and fails on max."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, retry_count, max_retries) VALUES (?, ?, ?, ?, 2, 3)",
        ("Parse Fail Task", "Will output garbage", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid

    mock_resp = make_mock_response("Not valid json at all")

    with patch("agent_engine.call_llm", return_value=mock_resp):
        agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    assert ticket["status"] == "FAILED"
    assert ticket["retry_count"] == 3
    assert "JSON parse failed" in ticket["error_message"]


def test_process_ticket_budget_cutoff(mem_db):
    """Verify ticket and agent pause when budget limit is reached."""
    # Set dev agent to already at budget limit
    mem_db.execute("UPDATE agents SET current_spend = 5.00, budget_limit = 5.00 WHERE id = 'dev'")
    mem_db.commit()

    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Over Budget Task", "Should be paused", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid

    agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    agent = agent_engine.get_agent(mem_db, "dev")

    assert ticket["status"] == "PAUSED_BUDGET"
    assert agent["status"] == "PAUSED_BUDGET"


def test_process_ticket_with_tool_calls(mem_db, make_mock_response, tmp_path, monkeypatch):
    """Verify tool calls in LLM response are executed in ticket workspace and logged."""
    import json
    import os
    import config
    import tools
    monkeypatch.setattr(config, "WORKSPACES_DIR", str(tmp_path / "workspaces"))

    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Dev Build Task", "Write module", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid

    payload = json.dumps({
        "status": "DONE",
        "result": "Created app.py and verified syntax",
        "tool_calls": [
            {"tool": "file_write", "args": {"path": "app.py", "content": "print('hello from engine')\n"}},
            {"tool": "execute_command", "args": {"command": "python3 app.py"}},
        ],
    })
    mock_resp = make_mock_response(payload)

    with patch("agent_engine.call_llm", return_value=mock_resp):
        agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    assert ticket["status"] == "DONE"
    assert ticket["result"] == "Created app.py and verified syntax"

    # Verify file actually created in workspace
    ws_dir = tools.get_ticket_workspace(mem_db, ticket_id)
    created_file = os.path.join(ws_dir, "app.py")
    assert os.path.exists(created_file)
    with open(created_file) as f:
        assert "hello from engine" in f.read()

    # Check execution logs contain tool_calls
    logs = agent_engine.get_execution_logs(mem_db, ticket_id)
    assert len(logs) == 1
    assert logs[0]["tool_calls"] is not None
    tc_data = json.loads(logs[0]["tool_calls"])
    assert len(tc_data) == 2
    assert tc_data[0]["tool"] == "file_write"
    assert tc_data[0]["success"] is True
    assert tc_data[1]["tool"] == "execute_command"
    assert tc_data[1]["success"] is True
    assert "hello from engine" in tc_data[1]["stdout"]


def test_process_ticket_in_progress_state(mem_db, make_mock_response, tmp_path, monkeypatch):
    """Verify status IN_PROGRESS re-queues ticket to OPEN for multi-tick continuation."""
    import json
    import config
    monkeypatch.setattr(config, "WORKSPACES_DIR", str(tmp_path / "workspaces"))

    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        ("Multi-tick Task", "Draft then test", "dev", "ceo"),
    )
    ticket_id = cursor.lastrowid

    payload = json.dumps({
        "status": "IN_PROGRESS",
        "result": "Drafted implementation, testing next tick",
        "tool_calls": [
            {"tool": "file_write", "args": {"path": "module.py", "content": "VAL = 42\n"}},
        ],
    })
    mock_resp = make_mock_response(payload)

    with patch("agent_engine.call_llm", return_value=mock_resp):
        agent_engine.process_ticket(mem_db, ticket_id)

    ticket = agent_engine.get_ticket(mem_db, ticket_id)
    # Re-queued to OPEN so next heartbeat tick picks it up
    assert ticket["status"] == "OPEN"
    assert ticket["result"] == "Drafted implementation, testing next tick"
    assert ticket["retry_count"] == 0
    assert ticket["error_message"] is None


def test_build_memento_context_includes_workspace_and_tools(mem_db, tmp_path, monkeypatch):
    """Verify workspace information, existing files, and tools are injected into memento context."""
    import config
    import tools
    monkeypatch.setattr(config, "WORKSPACES_DIR", str(tmp_path / "workspaces"))

    dev = agent_engine.get_agent(mem_db, "dev")
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, result) VALUES (?, ?, ?, ?, ?)",
        ("Dev Task", "Work in workspace", "dev", "ceo", "Step 1 complete"),
    )
    ticket_id = cursor.lastrowid
    ticket = agent_engine.get_ticket(mem_db, ticket_id)

    # Pre-create a file in the workspace
    ws_dir = tools.get_ticket_workspace(mem_db, ticket_id)
    tools.file_write(ws_dir, "lib.py", "X = 1\n")

    messages = agent_engine.build_memento_context(mem_db, dev, ticket)
    prompt = messages[0]["content"]

    assert "## Workspace" in prompt
    assert f"ticket_{ticket_id}" in prompt
    assert "lib.py" in prompt
    assert "## Available Tools" in prompt
    assert "file_write" in prompt
    assert "file_read" in prompt
    assert "execute_command" in prompt
    assert "directory_list" in prompt
    assert '"tool_calls"' in prompt
    assert "Current progress: Step 1 complete" in prompt


def test_subticket_workspace_inheritance(mem_db, tmp_path, monkeypatch):
    """Verify child ticket inherits root ticket workspace and can access its artifacts."""
    import config
    import tools
    monkeypatch.setattr(config, "WORKSPACES_DIR", str(tmp_path / "workspaces"))

    # Root ticket by CEO
    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES ('Project Root', 'desc', 'ceo', 'human')"
    )
    root_id = cur.lastrowid

    # Subtask assigned to Dev
    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id) VALUES ('Dev Work', 'desc', 'dev', 'ceo', ?)",
        (root_id,),
    )
    dev_ticket_id = cur.lastrowid

    # Reviewer subtask
    cur = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id) VALUES ('QA Review', 'desc', 'reviewer', 'ceo', ?)",
        (root_id,),
    )
    qa_ticket_id = cur.lastrowid

    dev_ws = tools.get_ticket_workspace(mem_db, dev_ticket_id)
    qa_ws = tools.get_ticket_workspace(mem_db, qa_ticket_id)

    assert dev_ws == qa_ws

    # Dev writes deliverable
    tools.file_write(dev_ws, "deliverable.md", "# Product Deliverable\n")

    # QA reviewer reads deliverable
    qa_read = tools.file_read(qa_ws, "deliverable.md")
    assert qa_read["success"] is True
    assert "# Product Deliverable" in qa_read["content"]

