"""Vectis heartbeat daemon: polling loop for autonomous agent execution."""

import time
from datetime import datetime
import config
from agent_engine import process_ticket


def recover_interrupted_tickets(conn):
    """Revert IN_PROGRESS tickets to OPEN on startup (NFR-2: OOM Resiliency)."""
    rows = conn.execute(
        "UPDATE tickets SET status = 'OPEN', updated_at = CURRENT_TIMESTAMP "
        "WHERE status = 'IN_PROGRESS' RETURNING id"
    ).fetchall()
    if rows:
        print(f"[recovery] Reverted {len(rows)} interrupted ticket(s) to OPEN")
    return len(rows)


def check_completed_delegations(conn):
    """Re-open REVIEW tickets whose child tickets are all DONE or FAILED."""
    tickets = conn.execute(
        """SELECT t.id FROM tickets t
           WHERE t.status = 'REVIEW'
           AND EXISTS (SELECT 1 FROM tickets c WHERE c.parent_ticket_id = t.id)
           AND NOT EXISTS (
               SELECT 1 FROM tickets c
               WHERE c.parent_ticket_id = t.id
               AND c.status NOT IN ('DONE', 'FAILED')
           )"""
    ).fetchall()

    for row in tickets:
        conn.execute(
            "UPDATE tickets SET status = 'OPEN', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (row["id"],),
        )
        print(f"  [review] Ticket #{row['id']} re-opened (all sub-tasks complete)")

    if tickets:
        conn.commit()
    return len(tickets)


def find_actionable_tickets(conn):
    """Find OPEN tickets assigned to ACTIVE agents within budget."""
    return conn.execute(
        """SELECT t.id FROM tickets t
           JOIN agents a ON t.assigned_to = a.id
           WHERE t.status = 'OPEN'
           AND a.status = 'ACTIVE'
           AND a.current_spend < a.budget_limit
           ORDER BY t.created_at ASC"""
    ).fetchall()


def run_heartbeat(conn):
    """Execute a single heartbeat tick."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Check for completed delegations
    check_completed_delegations(conn)

    # Find actionable tickets
    tickets = find_actionable_tickets(conn)

    if not tickets:
        print(f"[heartbeat] {now} - No actionable tickets. Sleeping...")
        return 0

    print(f"[heartbeat] {now} - {len(tickets)} actionable ticket(s) found")

    processed = 0
    for ticket in tickets[: config.MAX_CONCURRENT_TASKS]:
        process_ticket(conn, ticket["id"])
        processed += 1

    return processed


def run_daemon(interval=None):
    """Run the heartbeat daemon loop."""
    if interval is None:
        interval = config.POLL_INTERVAL_SECONDS

    conn = config.get_db_connection()

    print(f"Vectis heartbeat daemon started. Polling every {interval}s. Press Ctrl+C to stop.")

    # Recover interrupted tickets on startup
    recover_interrupted_tickets(conn)

    try:
        while True:
            run_heartbeat(conn)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nVectis daemon stopped.")
    finally:
        conn.close()


def run_single_heartbeat():
    """Run a single heartbeat and exit."""
    conn = config.get_db_connection()
    recover_interrupted_tickets(conn)
    run_heartbeat(conn)
    conn.close()
 
