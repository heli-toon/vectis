"""Vectis agent engine: memento context builder, LiteLLM wrapper, and state machine."""

import json
import litellm
import config

litellm.set_verbose = False


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
        "SELECT id, title, status, result FROM tickets WHERE parent_ticket_id = ? ORDER BY id",
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

    # Child ticket results
    children = get_child_tickets(conn, ticket["id"])
    if children:
        child_lines = []
        for c in children:
            result_preview = (c["result"] or "N/A")
            if len(result_preview) > 500:
                result_preview = result_preview[:500] + "..."
            child_lines.append(f"- #{c['id']} [{c['status']}]: {c['title']}\n  Result: {result_preview}")
        sections.append("\n## Sub-Task Results\n" + "\n".join(child_lines))

    # Execution history
    logs = get_execution_logs(conn, ticket["id"])
    if logs:
        log_lines = []
        for i, log in enumerate(logs):
            log_lines.append(
                f"- Attempt {i + 1}: {log['prompt_tokens']} input + {log['completion_tokens']} output tokens, "
                f"${log['estimated_cost']:.4f}"
            )
        sections.append("\n## Execution History\n" + "\n".join(log_lines))

    # Response format instructions
    instructions = (
        "\n## Response Format\n"
        "Respond with a single JSON object containing exactly these fields:\n"
        "{\n"
        '  "status": "DONE" | "REVIEW" | "FAILED",\n'
        '  "result": "Your deliverable - a summary of work completed or output produced",\n'
        '  "new_tickets": [\n'
        '    {"title": "Sub-task title", "description": "Detailed description", "assigned_to": "agent_id"}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        '- "status" must be one of: DONE (task complete), REVIEW (delegated, awaiting sub-task completion), '
        "FAILED (cannot complete).\n"
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
    """Parse JSON from LLM response, handling markdown wrappers and extra text."""
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

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Try to extract JSON object from surrounding text
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(content[start:end])
        raise ValueError(f"Could not parse JSON from response: {content[:200]}")


def update_ticket_status(conn, ticket_id, status, result=None):
    if result is not None:
        conn.execute(
            "UPDATE tickets SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, result, ticket_id),
        )
    else:
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, ticket_id),
        )
    conn.commit()


def update_agent_spend(conn, agent_id, cost):
    conn.execute(
        "UPDATE agents SET current_spend = current_spend + ? WHERE id = ?",
        (cost, agent_id),
    )
    conn.commit()


def update_agent_status(conn, agent_id, status):
    conn.execute("UPDATE agents SET status = ? WHERE id = ?", (status, agent_id))
    conn.commit()


def log_execution(conn, ticket_id, agent_id, prompt_tokens, completion_tokens, cost):
    conn.execute(
        """INSERT INTO execution_logs (ticket_id, agent_id, prompt_tokens, completion_tokens, estimated_cost)
           VALUES (?, ?, ?, ?, ?)""",
        (ticket_id, agent_id, prompt_tokens, completion_tokens, cost),
    )
    conn.commit()


def spawn_sub_tickets(conn, parent_ticket_id, agent_id, new_tickets):
    """Create child tickets from agent delegation response."""
    spawned = 0
    for nt in new_tickets:
        title = nt.get("title", "").strip()
        assigned_to = nt.get("assigned_to", "").strip()
        description = nt.get("description", "")

        if not title or not assigned_to:
            continue

        target = conn.execute("SELECT id FROM agents WHERE id = ?", (assigned_to,)).fetchone()
        if not target:
            print(f"  [warn] Unknown agent '{assigned_to}', skipping sub-ticket: {title}")
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

    # Transition to IN_PROGRESS (FR-3.1)
    update_ticket_status(conn, ticket_id, "IN_PROGRESS")
    print(f"  [start] Ticket #{ticket_id} -> IN_PROGRESS (agent: {agent['id']})")

    # Build memento context (F-2)
    messages = build_memento_context(conn, agent, ticket)

    # Call LLM (F-3, NFR-2)
    try:
        response = call_llm(agent, messages)
    except Exception as e:
        print(f"  [error] LLM call failed: {e}")
        update_ticket_status(conn, ticket_id, "OPEN")
        log_execution(conn, ticket_id, agent["id"], 0, 0, 0.0)
        return

    # Extract usage and content
    content = response.choices[0].message.content
    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0

    # Calculate cost (FR-4.2)
    cost = config.calculate_cost(agent["model_name"], prompt_tokens, completion_tokens)

    # Update spend and log execution (FR-4.2)
    update_agent_spend(conn, agent["id"], cost)
    log_execution(conn, ticket_id, agent["id"], prompt_tokens, completion_tokens, cost)

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
        print(f"  [error] JSON parse failed: {e}")
        print(f"  [raw] {content[:300]}")
        update_ticket_status(conn, ticket_id, "OPEN")
        return

    # Extract and validate fields
    new_status = parsed.get("status", "DONE").upper()
    result = parsed.get("result", "")
    new_tickets = parsed.get("new_tickets", [])

    valid_statuses = {"DONE", "REVIEW", "FAILED"}
    if new_status not in valid_statuses:
        new_status = "DONE"

    # Update ticket status (FR-3.1)
    update_ticket_status(conn, ticket_id, new_status, result)
    print(f"  [done] Ticket #{ticket_id} -> {new_status}")
    if result:
        print(f"  [result] {result[:200]}")

    # Spawn sub-tickets (FR-3.2, FR-3.3)
    if new_tickets:
        spawn_sub_tickets(conn, ticket_id, agent["id"], new_tickets)

    print(f"  [cost] {prompt_tokens}+{completion_tokens} tokens, ${cost:.4f}")

