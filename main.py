"""Vectis CLI entry point."""

import argparse
import os
import sys
import config
from init_db import init_database
from heartbeat import run_daemon, run_single_heartbeat


def ensure_db():
    """Check that the database has been initialized."""
    if not os.path.exists(config.DB_PATH):
        print(f"Database not found at {config.DB_PATH}. Run 'python main.py init' first.")
        sys.exit(1)


def cmd_init(args):
    init_database()


def cmd_run(args):
    ensure_db()
    if args.once:
        run_single_heartbeat()
    else:
        interval = args.interval if args.interval else config.POLL_INTERVAL_SECONDS
        run_daemon(interval)


def cmd_seed(args):
    ensure_db()
    conn = config.get_db_connection()
    cursor = conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
        (args.title, args.desc or "", args.assign, "human_user"),
    )
    conn.commit()
    ticket_id = cursor.lastrowid
    conn.close()
    print(f"Seeded ticket #{ticket_id}: {args.title}")
    print(f"  Assigned to: {args.assign}")
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
    for status in ["OPEN", "IN_PROGRESS", "REVIEW", "DONE", "FAILED", "PAUSED_BUDGET"]:
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
    conn = config.get_db_connection()
    tickets = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()

    print(f"\nTickets ({len(tickets)}):")
    print("-" * 80)
    for t in tickets:
        parent = f"-> #{t['parent_ticket_id']}" if t["parent_ticket_id"] else "(root)"
        print(f"  #{t['id']} [{t['status']}] {t['title']}")
        print(f"    Assigned: {t['assigned_to']} | Created by: {t['created_by']} | Parent: {parent}")
        if t["result"]:
            result_preview = t["result"][:150]
            if len(t["result"]) > 150:
                result_preview += "..."
            print(f"    Result: {result_preview}")
        print()

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

    seed_parser = subparsers.add_parser("seed", help="Seed a root ticket")
    seed_parser.add_argument("title", help="Ticket title")
    seed_parser.add_argument("--assign", default="ceo", help="Agent ID to assign to (default: ceo)")
    seed_parser.add_argument("--desc", help="Ticket description")

    subparsers.add_parser("status", help="Show system status")
    subparsers.add_parser("agents", help="List all agents")
    subparsers.add_parser("tickets", help="List all tickets")

    tree_parser = subparsers.add_parser("tree", help="Show ticket hierarchy tree")
    tree_parser.add_argument("ticket_id", type=int, nargs="?", help="Root ticket ID (default: all roots)")

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "run": cmd_run,
        "seed": cmd_seed,
        "status": cmd_status,
        "agents": cmd_agents,
        "tickets": cmd_tickets,
        "tree": cmd_tree,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
 
