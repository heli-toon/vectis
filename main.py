"""Vectis CLI entry point."""

import argparse
import os
import sys
import config
from init_db import init_database


def ensure_db():
    """Check that the database has been initialized."""
    if not os.path.exists(config.DB_PATH):
        print(f"Database not found at {config.DB_PATH}. Run 'vectis init' or 'python main.py init' first.")
        sys.exit(1)


def cmd_init(args):
    init_database()


def cmd_run(args):
    from heartbeat import run_daemon, run_single_heartbeat
    ensure_db()
    workers = getattr(args, "workers", None)
    if args.once:
        run_single_heartbeat(max_workers=workers)
    else:
        interval = args.interval if args.interval else config.POLL_INTERVAL_SECONDS
        run_daemon(interval, max_workers=workers)


def cmd_seed(args):
    ensure_db()
    conn = config.get_db_connection()
    status = "AWAITING_APPROVAL" if getattr(args, "approval", False) else "OPEN"
    requires_approval = 1 if getattr(args, "approval", False) else 0
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status, requires_approval) VALUES (?, ?, ?, ?, ?, ?)",
        (args.title, args.desc or "", args.assign, "human_user", status, requires_approval),
    )
    conn.commit()
    ticket_id = cursor.lastrowid
    conn.close()
    print(f"Seeded ticket #{ticket_id}: {args.title}")
    print(f"  Assigned to: {args.assign}")
    print(f"  Status: {status}")
    if args.desc:
        print(f"  Description: {args.desc}")


def cmd_status(args):
    ensure_db()
    conn = config.get_db_connection()

    print("=" * 60)
    print("Vectis System Status")
    print("=" * 60)

    # Agents
    agents = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
    print(f"\nAgents ({len(agents)}):")
    print(f"  {'ID':<15} {'Role':<15} {'Status':<15} {'Spend':>10} {'Budget':>10}")
    print(f"  {'-' * 15} {'-' * 15} {'-' * 15} {'-' * 10} {'-' * 10}")
    for a in agents:
        print(
            f"  {a['id']:<15} {a['role']:<15} {a['status']:<15} "
            f"${a['current_spend']:>8.4f} ${a['budget_limit']:>8.2f}"
        )

    # Ticket counts by status
    status_counts = {}
    for row in conn.execute("SELECT status, COUNT(*) as count FROM tickets GROUP BY status"):
        status_counts[row["status"]] = row["count"]

    total_tickets = sum(status_counts.values())
    print(f"\nTickets ({total_tickets} total):")
    for status in ["OPEN", "AWAITING_APPROVAL", "IN_PROGRESS", "REVIEW", "DONE", "FAILED", "PAUSED_BUDGET"]:
        count = status_counts.get(status, 0)
        if count > 0:
            print(f"  {status}: {count}")

    # Recent activity
    recent = conn.execute(
        "SELECT id, title, status, assigned_to FROM tickets ORDER BY updated_at DESC LIMIT 5"
    ).fetchall()
    if recent:
        print("\nRecent Activity:")
        for t in recent:
            print(f"  #{t['id']} [{t['status']}] {t['title']} ({t['assigned_to']})")

    # Engine Configuration
    print("\nEngine Configuration:")
    print(
        f"  Workers: {config.MAX_WORKERS} | Poll Interval: {config.POLL_INTERVAL_SECONDS}s | "
        f"DB Timeout: {config.DB_TIMEOUT}s"
    )

    print()
    conn.close()


def cmd_agents(args):
    ensure_db()
    conn = config.get_db_connection()
    agents = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()

    print(f"\nAgents ({len(agents)}):")
    print("-" * 80)
    for a in agents:
        manager = a["manager_id"] or "(none)"
        print(f"  ID:       {a['id']}")
        print(f"  Name:     {a['name']}")
        print(f"  Role:     {a['role']}")
        print(f"  Model:    {a['model_name']}")
        print(f"  Manager:  {manager}")
        print(f"  Budget:   ${a['budget_limit']:.2f} (spent: ${a['current_spend']:.4f})")
        print(f"  Status:   {a['status']}")
        print(f"  Prompt:   {a['system_prompt'][:100]}...")
        print("-" * 80)

    conn.close()


def cmd_tickets(args):
    ensure_db()
    action = getattr(args, "ticket_action", None)
    if action == "retry":
        cmd_tickets_retry(args)
    elif action == "cancel":
        cmd_tickets_cancel(args)
    elif action == "approve":
        cmd_tickets_approve(args)
    elif action == "reject":
        cmd_tickets_reject(args)
    else:
        conn = config.get_db_connection()
        tickets = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()

        print(f"\nTickets ({len(tickets)}):")
        print("-" * 80)
        for t in tickets:
            parent = f"-> #{t['parent_ticket_id']}" if t["parent_ticket_id"] else "(root)"
            print(f"  #{t['id']} [{t['status']}] {t['title']}")
            retry_info = f" | Retries: {t['retry_count']}/{t['max_retries']}" if "retry_count" in t.keys() else ""
            print(f"    Assigned: {t['assigned_to']} | Created by: {t['created_by']} | Parent: {parent}{retry_info}")
            if "error_message" in t.keys() and t["error_message"]:
                print(f"    Error: {t['error_message']}")
            if t["result"]:
                result_preview = t["result"][:150]
                if len(t["result"]) > 150:
                    result_preview += "..."
                print(f"    Result: {result_preview}")
            print()

        conn.close()


def cmd_tickets_retry(args):
    ensure_db()
    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (args.ticket_id,)).fetchone()
    if not ticket:
        print(f"Ticket #{args.ticket_id} not found.")
        conn.close()
        return
    conn.execute(
        "UPDATE tickets SET status = 'OPEN', retry_count = 0, error_message = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (args.ticket_id,),
    )
    conn.commit()
    conn.close()
    print(f"Ticket #{args.ticket_id} reset to OPEN with retry_count=0.")


def cmd_tickets_cancel(args):
    ensure_db()
    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (args.ticket_id,)).fetchone()
    if not ticket:
        print(f"Ticket #{args.ticket_id} not found.")
        conn.close()
        return
    conn.execute(
        "UPDATE tickets SET status = 'FAILED', error_message = 'Cancelled by operator', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (args.ticket_id,),
    )
    conn.commit()
    conn.close()
    print(f"Ticket #{args.ticket_id} cancelled (status: FAILED).")


def cmd_tickets_approve(args):
    ensure_db()
    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (args.ticket_id,)).fetchone()
    if not ticket:
        print(f"Ticket #{args.ticket_id} not found.")
        conn.close()
        return
    if ticket["status"] != "AWAITING_APPROVAL":
        print(f"Ticket #{args.ticket_id} is in status '{ticket['status']}', not AWAITING_APPROVAL.")
        conn.close()
        return

    conn.execute(
        "UPDATE tickets SET status = 'OPEN', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (args.ticket_id,),
    )
    conn.commit()
    conn.close()
    print(f"Ticket #{args.ticket_id} approved -> status set to OPEN.")


def cmd_tickets_reject(args):
    ensure_db()
    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (args.ticket_id,)).fetchone()
    if not ticket:
        print(f"Ticket #{args.ticket_id} not found.")
        conn.close()
        return

    reason = getattr(args, "reason", "Rejected by human operator") or "Rejected by human operator"
    conn.execute(
        "UPDATE tickets SET status = 'FAILED', error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (reason, args.ticket_id),
    )
    conn.commit()
    conn.close()
    print(f"Ticket #{args.ticket_id} rejected -> status set to FAILED ({reason}).")


def cmd_memory(args):
    ensure_db()
    action = getattr(args, "memory_action", None)
    if action == "search":
        cmd_memory_search(args)
    elif action == "add":
        cmd_memory_add(args)
    else:
        conn = config.get_db_connection()
        from memory import list_memories
        memories = list_memories(conn, limit=20)
        conn.close()
        print(f"\nInstitutional Knowledge Entries ({len(memories)}):")
        print("-" * 70)
        for m in memories:
            tags_part = f" [{m['tags']}]" if m.get("tags") else ""
            print(f"  #{m['id']}: {m['title']}{tags_part}")
            preview = m['content'][:120] + "..." if len(m['content']) > 120 else m['content']
            print(f"    {preview}")
            print()


def cmd_memory_search(args):
    ensure_db()
    conn = config.get_db_connection()
    from memory import search_memories
    results = search_memories(conn, args.query, limit=args.limit)
    conn.close()

    print(f"\nSearch Results for '{args.query}' ({len(results)} matches):")
    print("-" * 70)
    if not results:
        print("  No institutional memories matched query.")
    for m in results:
        tags_part = f" [{m['tags']}]" if m.get("tags") else ""
        print(f"  #{m['id']}: {m['title']}{tags_part} (BM25 rank: {m.get('rank', 0):.4f})")
        print(f"    {m['content']}")
        print()


def cmd_memory_add(args):
    ensure_db()
    conn = config.get_db_connection()
    from memory import record_memory
    mem_id = record_memory(
        conn,
        title=args.title,
        content=args.content,
        tags=getattr(args, "tags", "") or "",
        agent_id=None,
    )
    conn.close()
    print(f"Recorded institutional memory #{mem_id}: '{args.title}'")


def cmd_budget(args):
    action = getattr(args, "budget_action", None)
    if action == "topup":
        cmd_budget_topup(args)
    else:
        print("Usage: vectis budget topup <agent_id> <amount>")


def cmd_budget_topup(args):
    ensure_db()
    conn = config.get_db_connection()
    agent = conn.execute("SELECT * FROM agents WHERE id = ?", (args.agent_id,)).fetchone()
    if not agent:
        print(f"Agent '{args.agent_id}' not found.")
        conn.close()
        return

    conn.execute(
        "UPDATE agents SET budget_limit = budget_limit + ? WHERE id = ?",
        (args.amount, args.agent_id),
    )
    new_agent = conn.execute("SELECT * FROM agents WHERE id = ?", (args.agent_id,)).fetchone()
    resumed = False
    if new_agent["status"] == "PAUSED_BUDGET" and new_agent["current_spend"] < new_agent["budget_limit"]:
        conn.execute("UPDATE agents SET status = 'ACTIVE' WHERE id = ?", (args.agent_id,))
        conn.execute(
            "UPDATE tickets SET status = 'OPEN', updated_at = CURRENT_TIMESTAMP WHERE assigned_to = ? AND status = 'PAUSED_BUDGET'",
            (args.agent_id,),
        )
        resumed = True
    conn.commit()

    print(f"Topped up budget for agent '{args.agent_id}' by ${args.amount:.2f}.")
    print(f"  New limit: ${new_agent['budget_limit']:.2f} (current spend: ${new_agent['current_spend']:.4f})")
    if resumed:
        print(f"  Agent '{args.agent_id}' status restored to ACTIVE and paused tickets reverted to OPEN.")
    conn.close()


def cmd_logs(args):
    ensure_db()
    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (args.ticket_id,)).fetchone()
    if not ticket:
        print(f"Ticket #{args.ticket_id} not found.")
        conn.close()
        return

    print(f"\nExecution Logs for Ticket #{ticket['id']}: {ticket['title']}")
    retries = f"{ticket['retry_count']}/{ticket['max_retries']}" if "retry_count" in ticket.keys() else "N/A"
    print(f"Assigned To: {ticket['assigned_to']} | Status: {ticket['status']} | Retries: {retries}")
    if "error_message" in ticket.keys() and ticket["error_message"]:
        print(f"Error Message: {ticket['error_message']}")
    if ticket["result"]:
        result_preview = ticket["result"][:200]
        if len(ticket["result"]) > 200:
            result_preview += "..."
        print(f"Result: {result_preview}")

    logs = conn.execute(
        "SELECT * FROM execution_logs WHERE ticket_id = ? ORDER BY executed_at ASC",
        (args.ticket_id,),
    ).fetchall()

    if not logs:
        print("\nNo execution logs recorded for this ticket.")
        conn.close()
        return

    print("\n" + "=" * 90)
    print(f"  {'#':<4} {'Agent':<12} {'Prompt':>8} {'Comp':>8} {'Total':>8} {'Cost ($)':>10} {'Latency':>10} {'Executed At':<20}")
    print("  " + "-" * 88)

    total_prompt = 0
    total_comp = 0
    total_cost = 0.0
    total_ms = 0

    for i, log in enumerate(logs, 1):
        prompt = log["prompt_tokens"]
        comp = log["completion_tokens"]
        total = prompt + comp
        cost = log["estimated_cost"]
        ms = log["execution_time_ms"] if "execution_time_ms" in log.keys() and log["execution_time_ms"] else 0
        exec_at = str(log["executed_at"])[:19]

        total_prompt += prompt
        total_comp += comp
        total_cost += cost
        total_ms += ms

        latency_str = f"{ms}ms" if ms else "N/A"
        print(f"  {i:<4} {log['agent_id']:<12} {prompt:>8} {comp:>8} {total:>8} ${cost:>9.4f} {latency_str:>10} {exec_at:<20}")
        if "tool_calls" in log.keys() and log["tool_calls"]:
            try:
                import json
                tc_list = json.loads(log["tool_calls"])
                if tc_list:
                    tc_desc = [f"{tc.get('tool')}:{'ok' if tc.get('success') else 'fail'}" for tc in tc_list]
                    print(f"       tools: {', '.join(tc_desc)}")
            except Exception:
                pass

    print("  " + "-" * 88)
    print(f"  {'TOTAL':<16} {total_prompt:>8} {total_comp:>8} {total_prompt + total_comp:>8} ${total_cost:>9.4f} {total_ms:>8}ms")
    print("=" * 90 + "\n")
    conn.close()


def cmd_tree(args):
    ensure_db()
    conn = config.get_db_connection()

    def print_tree(ticket_id, indent=0):
        ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            return
        prefix = "  " * indent
        print(f"{prefix}#{ticket['id']} [{ticket['status']}] {ticket['title']} ({ticket['assigned_to']})")
        if ticket["result"] and indent == 0:
            result_preview = ticket["result"][:200]
            if len(ticket["result"]) > 200:
                result_preview += "..."
            print(f"{prefix}  Result: {result_preview}")
        children = conn.execute(
            "SELECT id FROM tickets WHERE parent_ticket_id = ? ORDER BY id", (ticket_id,)
        ).fetchall()
        for child in children:
            print_tree(child["id"], indent + 1)

    if args.ticket_id:
        print_tree(args.ticket_id)
    else:
        roots = conn.execute(
            "SELECT id FROM tickets WHERE parent_ticket_id IS NULL ORDER BY id"
        ).fetchall()
        if not roots:
            print("No tickets found.")
        for root in roots:
            print_tree(root["id"])
            print()

    conn.close()


def cmd_workspace(args):
    ensure_db()
    conn = config.get_db_connection()
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (args.ticket_id,)).fetchone()
    if not ticket:
        print(f"Ticket #{args.ticket_id} not found.")
        conn.close()
        return

    from tools import get_ticket_workspace, get_root_ticket_id, directory_list
    root_id = get_root_ticket_id(conn, args.ticket_id)
    ws_path = get_ticket_workspace(conn, args.ticket_id, create=False)
    print(f"\nWorkspace for Ticket #{ticket['id']} (Root Ticket #{root_id}):")
    print(f"  Path: {ws_path}")
    if not os.path.exists(ws_path):
        print("  Status: Not created on disk yet.")
    else:
        listing = directory_list(ws_path)
        entries = listing.get("entries", [])
        if not entries:
            print("  Status: Directory exists (empty).")
        else:
            print(f"  Status: {len(entries)} file(s)/directory(ies) found:")
            for e in entries:
                t_str = "[DIR]" if e["type"] == "directory" else f"[{e['size']}B]"
                print(f"    {t_str:<10} {e['path']}")
    print()
    conn.close()


def cmd_ui(args):
    ensure_db()
    import threading
    from web_ui import start_server
    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    port = getattr(args, "port", 8080) or 8080
    if getattr(args, "open", False):
        try:
            import webbrowser
            threading.Timer(0.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
        except Exception:
            pass
    start_server(host=host, port=port, db_path=config.DB_PATH, blocking=True)


def cmd_monitor(args):
    ensure_db()
    from monitor import start_monitor
    interval = getattr(args, "interval", 2.0) or 2.0
    start_monitor(interval_sec=interval, db_path=config.DB_PATH)


def main():
    parser = argparse.ArgumentParser(
        prog="vectis",
        description="Vectis - Ultra-lightweight multi-agent orchestration system",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Initialize database with default agents")

    run_parser = subparsers.add_parser("run", help="Start the heartbeat daemon")
    run_parser.add_argument("--once", action="store_true", help="Run a single heartbeat and exit")
    run_parser.add_argument("--interval", type=int, help="Override poll interval (seconds)")
    run_parser.add_argument("--workers", type=int, default=None, help="Override worker thread count (default: 4)")

    seed_parser = subparsers.add_parser("seed", help="Seed a root ticket")
    seed_parser.add_argument("title", help="Ticket title")
    seed_parser.add_argument("--assign", default="ceo", help="Agent ID to assign to (default: ceo)")
    seed_parser.add_argument("--desc", help="Ticket description")
    seed_parser.add_argument("--approval", action="store_true", help="Require human operator approval before execution")

    subparsers.add_parser("status", help="Show system status")
    subparsers.add_parser("agents", help="List all agents")

    tickets_parser = subparsers.add_parser("tickets", help="List or manage tickets")
    tickets_sub = tickets_parser.add_subparsers(dest="ticket_action")
    retry_parser = tickets_sub.add_parser("retry", help="Reset retry counter and status back to OPEN")
    retry_parser.add_argument("ticket_id", type=int, help="Ticket ID to retry")
    cancel_parser = tickets_sub.add_parser("cancel", help="Cancel ticket and set status to FAILED")
    cancel_parser.add_argument("ticket_id", type=int, help="Ticket ID to cancel")
    approve_parser = tickets_sub.add_parser("approve", help="Approve a ticket awaiting human approval")
    approve_parser.add_argument("ticket_id", type=int, help="Ticket ID to approve")
    reject_parser = tickets_sub.add_parser("reject", help="Reject a ticket awaiting human approval")
    reject_parser.add_argument("ticket_id", type=int, help="Ticket ID to reject")
    reject_parser.add_argument("--reason", default="Rejected by operator", help="Rejection reason")

    memory_parser = subparsers.add_parser("memory", help="Inspect and query institutional knowledge")
    memory_sub = memory_parser.add_subparsers(dest="memory_action")
    mem_search = memory_sub.add_parser("search", help="Search institutional knowledge")
    mem_search.add_argument("query", help="Keywords to search")
    mem_search.add_argument("--limit", type=int, default=5, help="Max results (default: 5)")
    mem_add = memory_sub.add_parser("add", help="Add knowledge to institutional memory")
    mem_add.add_argument("--title", required=True, help="Knowledge title")
    mem_add.add_argument("--content", required=True, help="Knowledge content/solution")
    mem_add.add_argument("--tags", default="", help="Comma-separated tags")

    budget_parser = subparsers.add_parser("budget", help="Manage agent budgets")
    budget_sub = budget_parser.add_subparsers(dest="budget_action")
    topup_parser = budget_sub.add_parser("topup", help="Top up an agent's budget")
    topup_parser.add_argument("agent_id", help="Agent ID")
    topup_parser.add_argument("amount", type=float, help="Amount in USD to add")

    logs_parser = subparsers.add_parser("logs", help="View execution logs for a ticket")
    logs_parser.add_argument("ticket_id", type=int, help="Ticket ID")

    tree_parser = subparsers.add_parser("tree", help="Show ticket hierarchy tree")
    tree_parser.add_argument("ticket_id", type=int, nargs="?", help="Root ticket ID (default: all roots)")

    ws_parser = subparsers.add_parser("workspace", help="Inspect workspace for a ticket")
    ws_parser.add_argument("ticket_id", type=int, help="Ticket ID")

    ui_parser = subparsers.add_parser("ui", help="Start the embedded web dashboard")
    ui_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind to (default: 127.0.0.1)")
    ui_parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    ui_parser.add_argument("--open", action="store_true", help="Automatically open dashboard in web browser")

    monitor_parser = subparsers.add_parser("monitor", help="Start curses terminal dashboard monitor")
    monitor_parser.add_argument("--interval", type=float, default=2.0, help="Refresh interval in seconds (default: 2.0)")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "run": cmd_run,
        "seed": cmd_seed,
        "status": cmd_status,
        "agents": cmd_agents,
        "tickets": cmd_tickets,
        "budget": cmd_budget,
        "logs": cmd_logs,
        "tree": cmd_tree,
        "workspace": cmd_workspace,
        "ui": cmd_ui,
        "monitor": cmd_monitor,
        "memory": cmd_memory,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
 
