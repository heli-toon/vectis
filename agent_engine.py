"""Vectis agent engine: memento context builder, LiteLLM wrapper, and state machine."""

import json
import os
import re
import time
import litellm
import config
from tools import get_ticket_workspace, directory_list, execute_tool_calls

litellm.set_verbose = False
litellm.telemetry = False
litellm.suppress_debug_info = True


def get_agent(conn, agent_id):
    return conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()


def get_ticket(conn, ticket_id):
    return conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()


def get_subordinates(conn, agent_id):
    return conn.execute(
        "SELECT id, name, role FROM agents WHERE manager_id = ? AND status = 'ACTIVE'",
        (agent_id,),
    ).fetchall()


def get_manager(conn, agent_id):
    agent = get_agent(conn, agent_id)
    if not agent or not agent["manager_id"]:
        return None
    return get_agent(conn, agent["manager_id"])


def get_child_tickets(conn, ticket_id):
    return conn.execute(
        "SELECT id, title, status, result, error_message, assigned_to FROM tickets WHERE parent_ticket_id = ? ORDER BY id",
        (ticket_id,),
    ).fetchall()


def get_execution_logs(conn, ticket_id):
    return conn.execute(
        "SELECT * FROM execution_logs WHERE ticket_id = ? ORDER BY executed_at",
        (ticket_id,),
    ).fetchall()


def build_memento_context(conn, agent, ticket):
    """Assemble the memento prompt from database state (Memento Pattern)."""
    sections = []

    # Identity
    sections.append(f"You are {agent['name']}, a {agent['role']} in the Vectis organization.")
    sections.append(f"\n## Your Instructions\n{agent['system_prompt']}")

    # Org position
    manager = get_manager(conn, agent["id"])
    subordinates = get_subordinates(conn, agent["id"])

    org_lines = []
    if manager:
        org_lines.append(f"- You report to: {manager['name']} ({manager['role']})")
    else:
        org_lines.append("- You are the top-level executive. You report to no one.")

    if subordinates:
        sub_names = ", ".join(f"{s['id']} ({s['role']})" for s in subordinates)
        org_lines.append(f"- Your team members (for delegation): {sub_names}")
    else:
        org_lines.append("- You have no direct subordinates. Complete all work yourself.")

    sections.append("\n## Organizational Position\n" + "\n".join(org_lines))

    # Budget
    remaining = agent["budget_limit"] - agent["current_spend"]
    sections.append(
        f"\n## Budget Status\n"
        f"- Limit: ${agent['budget_limit']:.2f}\n"
        f"- Spent: ${agent['current_spend']:.4f}\n"
        f"- Remaining: ${remaining:.4f}"
    )

    # Current ticket
    ticket_lines = [
        f"- Ticket #{ticket['id']}: {ticket['title']}",
        f"- Description: {ticket['description'] or 'N/A'}",
        f"- Created by: {ticket['created_by']}",
    ]
    if "result" in ticket.keys() and ticket["result"]:
        ticket_lines.append(f"- Current progress: {ticket['result']}")

    # Parent ticket context
    if ticket["parent_ticket_id"]:
        parent = get_ticket(conn, ticket["parent_ticket_id"])
        if parent:
            ticket_lines.append(
                f"- Parent task: #{parent['id']} [{parent['status']}]: {parent['title']}"
            )
            if parent["result"]:
                ticket_lines.append(f"- Parent result: {parent['result']}")

    sections.append("\n## Current Assignment\n" + "\n".join(ticket_lines))

    # Workspace context
    workspace_dir = get_ticket_workspace(conn, ticket["id"], create=True)
    try:
        rel_workspace = os.path.relpath(workspace_dir, os.getcwd())
    except ValueError:
        rel_workspace = workspace_dir

    workspace_lines = [f"- Path: {rel_workspace}"]
    listing = directory_list(workspace_dir)
    entries = listing.get("entries", [])
    if entries:
        file_summary = ", ".join(
            f"{e['path']} ({e['size']}B)" if e["type"] == "file" else f"{e['path']}/"
            for e in entries[:10]
        )
        if len(entries) > 10:
            file_summary += f" ... (+{len(entries)-10} more)"
        workspace_lines.append(f"- Existing files: {file_summary}")
    else:
        workspace_lines.append("- Existing files: (none)")

    sections.append("\n## Workspace\n" + "\n".join(workspace_lines))

    # Child ticket results
    children = get_child_tickets(conn, ticket["id"])
    if children:
        child_lines = []
        for c in children:
            if c["status"] == "FAILED":
                err = c["error_message"] or c["result"] or "Unknown error"
                child_lines.append(
                    f"- #{c['id']} [FAILED]: {c['title']}\n"
                    f"  Sub-task #{c['id']} failed with error: {err}. "
                    f"Decide whether to re-assign, modify parameters, or fail the parent task."
                )
            else:
                result_preview = (c["result"] or "N/A")
                if len(result_preview) > 500:
                    result_preview = result_preview[:500] + "..."
                child_lines.append(f"- #{c['id']} [{c['status']}]: {c['title']}\n  Result: {result_preview}")
        sections.append("\n## Sub-Task Results\n" + "\n".join(child_lines))

    # Execution history
    logs = get_execution_logs(conn, ticket["id"])
    # Keep only the most recent 3 execution attempts
    if logs:
        logs = logs[-3:]
    if logs:
        log_lines = []
        for i, log in enumerate(logs):
            time_part = ""
            if "execution_time_ms" in log.keys() and log["execution_time_ms"]:
                time_part = f", {log['execution_time_ms']}ms"
            line = (
                f"- Attempt {i + 1}: {log['prompt_tokens']} input + {log['completion_tokens']} output tokens, "
                f"${log['estimated_cost']:.4f}{time_part}"
            )
            if "tool_calls" in log.keys() and log["tool_calls"]:
                try:
                    tc_list = json.loads(log["tool_calls"])
                    if tc_list:
                        tc_items = []
                        for tc in tc_list:
                            t_name = tc.get("tool", "tool")
                            t_succ = "success" if tc.get("success") else f"failed ({tc.get('error', 'error')})"
                            if t_name == "execute_command":
                                out_snip = (tc.get("stdout") or tc.get("stderr") or "").strip()
                                if len(out_snip) > 80:
                                    out_snip = out_snip[:80] + "..."
                                tc_items.append(f"  * [tool] {t_name} ('{tc.get('command')}'): {t_succ} -> {out_snip}")
                            elif t_name == "file_read":
                                cont_snip = (tc.get("content") or "").strip()
                                if len(cont_snip) > 80:
                                    cont_snip = cont_snip[:80] + "..."
                                tc_items.append(f"  * [tool] {t_name} ('{tc.get('path')}'): {t_succ} -> {cont_snip}")
                            else:
                                tc_items.append(f"  * [tool] {t_name} ('{tc.get('path', '')}'): {t_succ}")
                        line += "\n" + "\n".join(tc_items)
                except Exception:
                    pass
            log_lines.append(line)
        sections.append("\n## Execution History\n" + "\n".join(log_lines))

    # Available tools
    tools_section = (
        "\n## Available Tools\n"
        "You can execute actions in your ticket workspace by providing a 'tool_calls' list in your JSON response.\n"
        "Supported tools:\n"
        "1. file_write(path, content): Write text or code to a file inside the workspace.\n"
        "   Args: {\"path\": \"relative/path/to/file.ext\", \"content\": \"file contents\"}\n"
        "2. file_read(path): Read the contents of an existing file from the workspace.\n"
        "   Args: {\"path\": \"relative/path/to/file.ext\"}\n"
        "3. directory_list(path=\".\"): Inspect files and folders inside the workspace.\n"
        "   Args: {\"path\": \".\"}\n"
        "4. execute_command(command, timeout_sec=30): Execute a shell command inside the workspace directory.\n"
        "   Args: {\"command\": \"python3 -m unittest\", \"timeout_sec\": 30}"
    )
    sections.append(tools_section)

    # Response format instructions
    instructions = (
        "\n## Response Format\n"
        "Respond with a single JSON object containing exactly these fields:\n"
        "{\n"
        '  "status": "DONE" | "IN_PROGRESS" | "REVIEW" | "FAILED",\n'
        '  "result": "Your deliverable - a summary of work completed or output produced",\n'
        '  "tool_calls": [\n'
        '    {"tool": "file_write", "args": {"path": "app.py", "content": "..."}}\n'
        "  ],\n"
        '  "new_tickets": [\n'
        '    {"title": "Sub-task title", "description": "Detailed description", "assigned_to": "agent_id"}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        '- "status" must be one of:\n'
        '  * DONE: task complete.\n'
        '  * IN_PROGRESS: task is progressing, continue on next tick.\n'
        '  * REVIEW: delegated, awaiting sub-task completion.\n'
        '  * FAILED: cannot complete.\n'
        '- "tool_calls" can be an empty array if no tool action is needed.\n'
        '- "new_tickets" can be an empty array if no delegation is needed.\n'
        '- "assigned_to" must be the ID of one of your team members listed above.\n'
        "- Respond with ONLY the JSON object. No markdown, no explanation outside the JSON."
    )
    sections.append(instructions)

    system_content = "\n".join(sections)

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Execute ticket #{ticket['id']}: {ticket['title']}"},
    ]


def call_llm(agent, messages):
    """Call LiteLLM with JSON response format, retry, and fallback."""
    kwargs = {
        "model": agent["model_name"],
        "messages": messages,
        "temperature": config.TEMPERATURE,
        "max_tokens": config.MAX_TOKENS,
        "num_retries": config.MAX_RETRIES,
    }

    try:
        return litellm.completion(response_format={"type": "json_object"}, **kwargs)
    except Exception:
        # Fallback for providers that don't support response_format
        return litellm.completion(**kwargs)


def parse_agent_response(content):
    """Parse JSON from LLM response, handling markdown wrappers, extra text, and trailing commas."""
    if not content:
        raise ValueError("Empty response from LLM")

    content = content.strip()

    # Strip markdown code blocks
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    def remove_trailing_commas(text):
        return re.sub(r',\s*([}\]])', r'\1', text)

    # 1. Direct JSON parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 2. Try removing trailing commas directly
    try:
        return json.loads(remove_trailing_commas(content))
    except json.JSONDecodeError:
        pass

    # 3. Extract JSON object {...}
    start = content.find("{")
    end = content.rfind("}") + 1
    if start != -1 and end > start:
        snippet = content[start:end]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            try:
                return json.loads(remove_trailing_commas(snippet))
            except json.JSONDecodeError:
                pass

    # 4. Extract JSON array [...]
    start_arr = content.find("[")
    end_arr = content.rfind("]") + 1
    if start_arr != -1 and end_arr > start_arr:
        snippet = content[start_arr:end_arr]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            try:
                return json.loads(remove_trailing_commas(snippet))
            except json.JSONDecodeError:
                pass

    raise ValueError(f"Could not parse JSON from response: {content[:200]}")


def update_ticket_status(conn, ticket_id, status, result=None, error_message=None):
    clauses = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
    params = [status]

    if result is not None:
        clauses.append("result = ?")
        params.append(result)

    if error_message is not None:
        clauses.append("error_message = ?")
        params.append(error_message)
    elif status in ("DONE", "REVIEW", "OPEN"):
        clauses.append("error_message = NULL")
        clauses.append("retry_count = 0")

    params.append(ticket_id)
    query = f"UPDATE tickets SET {', '.join(clauses)} WHERE id = ?"
    conn.execute(query, tuple(params))
    conn.commit()


def handle_ticket_failure(conn, ticket, error_msg):
    """Handle ticket failure with retry budget and Dead Letter Queue (DLQ)."""
    fresh_ticket = get_ticket(conn, ticket["id"]) or ticket
    current_retries = (fresh_ticket["retry_count"] or 0) + 1
    max_retries = (
        fresh_ticket["max_retries"]
        if fresh_ticket["max_retries"] is not None
        else config.MAX_RETRIES
    )

    if current_retries >= max_retries:
        conn.execute(
            "UPDATE tickets SET status = 'FAILED', retry_count = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (current_retries, error_msg, ticket["id"]),
        )
        print(f"  [failed] Ticket #{ticket['id']} exceeded max retries ({current_retries}/{max_retries}): {error_msg}")
    else:
        conn.execute(
            "UPDATE tickets SET status = 'OPEN', retry_count = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (current_retries, error_msg, ticket["id"]),
        )
        print(f"  [retry] Ticket #{ticket['id']} failed attempt {current_retries}/{max_retries}: {error_msg}. Reverted to OPEN.")
    conn.commit()


def claim_ticket(conn, ticket_id):
    """Atomically claim an OPEN ticket for this worker."""
    cursor = conn.execute(
        "UPDATE tickets SET status = 'IN_PROGRESS', updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND status = 'OPEN'",
        (ticket_id,),
    )
    conn.commit()
    return cursor.rowcount == 1


def update_agent_spend(conn, agent_id, cost):
    conn.execute(
        "UPDATE agents SET current_spend = current_spend + ? WHERE id = ?",
        (cost, agent_id),
    )
    conn.commit()


def update_agent_status(conn, agent_id, status):
    conn.execute("UPDATE agents SET status = ? WHERE id = ?", (status, agent_id))
    conn.commit()


def log_execution(conn, ticket_id, agent_id, prompt_tokens, completion_tokens, cost, execution_time_ms=0, tool_calls=None):
    tool_calls_json = json.dumps(tool_calls) if tool_calls else None
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(execution_logs)").fetchall()]
    if "tool_calls" in cols:
        conn.execute(
            """INSERT INTO execution_logs (ticket_id, agent_id, prompt_tokens, completion_tokens, estimated_cost, execution_time_ms, tool_calls)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticket_id, agent_id, prompt_tokens, completion_tokens, cost, execution_time_ms, tool_calls_json),
        )
    else:
        conn.execute(
            """INSERT INTO execution_logs (ticket_id, agent_id, prompt_tokens, completion_tokens, estimated_cost, execution_time_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticket_id, agent_id, prompt_tokens, completion_tokens, cost, execution_time_ms),
        )
    conn.commit()


def spawn_sub_tickets(conn, parent_ticket_id, agent_id, new_tickets):
    """Create child tickets from agent delegation response."""
    spawned = 0
    subordinate_ids = {row["id"] for row in get_subordinates(conn, agent_id)}

    if not isinstance(new_tickets, list):
        print("  [warn] Invalid new_tickets value; expected an array")
        return 0

    for nt in new_tickets:
        if not isinstance(nt, dict):
            print("  [warn] Invalid sub-ticket entry; expected an object")
            continue

        raw_title = nt.get("title", "")
        raw_assignee = nt.get("assigned_to", "")
        description = nt.get("description", "")

        if not isinstance(raw_title, str) or not isinstance(raw_assignee, str):
            print("  [warn] Invalid sub-ticket title or assignee; skipping")
            continue
        if not isinstance(description, str):
            print("  [warn] Invalid sub-ticket description; skipping")
            continue

        title = raw_title.strip()
        assigned_to = raw_assignee.strip()
        if not title or not assigned_to:
            continue

        if assigned_to not in subordinate_ids:
            print(
                f"  [warn] Agent '{assigned_to}' is not an active subordinate of "
                f"'{agent_id}', skipping sub-ticket: {title}"
            )
            continue

        conn.execute(
            "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, description, assigned_to, agent_id, parent_ticket_id),
        )
        spawned += 1
        print(f"  [spawn] #{parent_ticket_id} -> {assigned_to}: {title}")

    conn.commit()
    return spawned


def process_ticket(conn, ticket_id):
    """Process a single ticket through the agent engine."""
    ticket = get_ticket(conn, ticket_id)
    if not ticket:
        return

    agent = get_agent(conn, ticket["assigned_to"])
    if not agent:
        return

    # Budget check (FR-4.1)
    if agent["current_spend"] >= agent["budget_limit"]:
        update_ticket_status(conn, ticket_id, "PAUSED_BUDGET")
        update_agent_status(conn, agent["id"], "PAUSED_BUDGET")
        print(
            f"  [budget] Agent {agent['id']} budget exceeded "
            f"(${agent['current_spend']:.2f}/${agent['budget_limit']:.2f})"
        )
        return

    # Agent status check
    if agent["status"] != "ACTIVE":
        print(f"  [skip] Agent {agent['id']} is {agent['status']}")
        return

    # Transition to IN_PROGRESS (FR-3.1), guarding against duplicate workers.
    if not claim_ticket(conn, ticket_id):
        print(f"  [skip] Ticket #{ticket_id} is no longer OPEN")
        return
    print(f"  [start] Ticket #{ticket_id} -> IN_PROGRESS (agent: {agent['id']})")

    start_time = time.time()

    # Build memento context (F-2)
    messages = build_memento_context(conn, agent, ticket)

    # Call LLM (F-3, NFR-2)
    try:
        response = call_llm(agent, messages)
    except Exception as e:
        err_msg = f"LLM call failed: {e}"
        print(f"  [error] {err_msg}")
        exec_time_ms = int((time.time() - start_time) * 1000)
        log_execution(conn, ticket_id, agent["id"], 0, 0, 0.0, exec_time_ms)
        handle_ticket_failure(conn, ticket, err_msg)
        return

    # Extract usage and content.
    try:
        content = response.choices[0].message.content
        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    except (AttributeError, IndexError, TypeError) as e:
        err_msg = f"Invalid LLM response: {e}"
        print(f"  [error] {err_msg}")
        exec_time_ms = int((time.time() - start_time) * 1000)
        log_execution(conn, ticket_id, agent["id"], 0, 0, 0.0, exec_time_ms)
        handle_ticket_failure(conn, ticket, err_msg)
        return

    # Calculate cost (FR-4.2)
    cost = config.calculate_cost(agent["model_name"], prompt_tokens, completion_tokens)
    exec_time_ms = int((time.time() - start_time) * 1000)

    # Update spend and log execution (FR-4.2)
    update_agent_spend(conn, agent["id"], cost)

    # Check if budget now exceeded (FR-4.3)
    updated_agent = get_agent(conn, agent["id"])
    if updated_agent["current_spend"] >= updated_agent["budget_limit"]:
        update_agent_status(conn, agent["id"], "PAUSED_BUDGET")
        print(
            f"  [budget] Agent {agent['id']} now paused "
            f"(${updated_agent['current_spend']:.4f}/${updated_agent['budget_limit']:.2f})"
        )

    # Parse response (F-4)
    try:
        parsed = parse_agent_response(content)
    except (json.JSONDecodeError, ValueError) as e:
        err_msg = f"JSON parse failed: {e}"
        print(f"  [error] {err_msg}")
        print(f"  [raw] {content[:300] if content else 'None'}")
        log_execution(conn, ticket_id, agent["id"], prompt_tokens, completion_tokens, cost, exec_time_ms)
        handle_ticket_failure(conn, ticket, err_msg)
        return

    # Extract and validate fields
    if not isinstance(parsed, dict):
        err_msg = "JSON response must be an object"
        print(f"  [error] {err_msg}")
        log_execution(conn, ticket_id, agent["id"], prompt_tokens, completion_tokens, cost, exec_time_ms)
        handle_ticket_failure(conn, ticket, err_msg)
        return

    raw_status = parsed.get("status", "DONE")
    result = parsed.get("result", "")
    new_tickets = parsed.get("new_tickets", [])
    tool_calls = parsed.get("tool_calls", [])

    if not isinstance(raw_status, str) or not isinstance(result, str):
        err_msg = "JSON response contains invalid status or result types"
        print(f"  [error] {err_msg}")
        log_execution(conn, ticket_id, agent["id"], prompt_tokens, completion_tokens, cost, exec_time_ms)
        handle_ticket_failure(conn, ticket, err_msg)
        return
    new_status = raw_status.upper()

    valid_statuses = {"DONE", "IN_PROGRESS", "REVIEW", "FAILED"}
    if new_status not in valid_statuses:
        new_status = "DONE"

    # Execute tool calls if any
    tool_results = []
    if tool_calls:
        workspace_dir = get_ticket_workspace(conn, ticket_id, create=True)
        tool_results = execute_tool_calls(workspace_dir, tool_calls)
        for tr in tool_results:
            t_name = tr.get("tool", "unknown")
            if tr.get("success"):
                print(f"  [tool] {t_name}: success")
            else:
                print(f"  [tool] {t_name}: failed -> {tr.get('error', 'unknown error')}")

    # Record execution log with tool results
    log_execution(
        conn,
        ticket_id,
        agent["id"],
        prompt_tokens,
        completion_tokens,
        cost,
        exec_time_ms,
        tool_calls=tool_results,
    )

    # Update ticket status (FR-3.1)
    if new_status == "FAILED":
        fail_msg = result or "Agent declared task FAILED"
        update_ticket_status(conn, ticket_id, "FAILED", result=result, error_message=fail_msg)
        print(f"  [done] Ticket #{ticket_id} -> FAILED")
    elif new_status == "IN_PROGRESS":
        # Re-queue ticket as OPEN so next heartbeat tick continues execution
        update_ticket_status(conn, ticket_id, "OPEN", result=result)
        print(f"  [progress] Ticket #{ticket_id} continuing in progress (re-queued as OPEN)")
    else:
        update_ticket_status(conn, ticket_id, new_status, result=result)
        print(f"  [done] Ticket #{ticket_id} -> {new_status}")

    if result:
        print(f"  [result] {result[:200]}")

    # Spawn sub-tickets (FR-3.2, FR-3.3)
    if new_tickets:
        spawn_sub_tickets(conn, ticket_id, agent["id"], new_tickets)

    print(f"  [cost] {prompt_tokens}+{completion_tokens} tokens, ${cost:.4f} ({exec_time_ms}ms)")

