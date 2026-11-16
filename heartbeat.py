"""Vectis heartbeat daemon: polling loop for autonomous agent execution."""

import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import config
from agent_engine import process_ticket


def recover_interrupted_tickets(conn):
    """Revert IN_PROGRESS tickets to OPEN on startup (NFR-2: OOM Resiliency)."""
    rows = conn.execute("SELECT id FROM tickets WHERE status = 'IN_PROGRESS'").fetchall()
    conn.execute(
        "UPDATE tickets SET status = 'OPEN', updated_at = CURRENT_TIMESTAMP "
        "WHERE status = 'IN_PROGRESS'"
    )
    conn.commit()
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
    """Find OPEN tickets assigned to ACTIVE agents within budget, avoiding agents already working."""
    return conn.execute(
        """SELECT t.id, t.assigned_to FROM tickets t
           JOIN agents a ON t.assigned_to = a.id
           WHERE t.status = 'OPEN'
           AND a.status = 'ACTIVE'
           AND a.current_spend < a.budget_limit
           AND NOT EXISTS (
               SELECT 1 FROM tickets running
               WHERE running.assigned_to = t.assigned_to
               AND running.status = 'IN_PROGRESS'
           )
           ORDER BY t.created_at ASC"""
    ).fetchall()


def select_ticket_batch(tickets, max_workers):
    """Select up to max_workers tickets, ensuring at most one ticket per agent per tick."""
    batch = []
    seen_agents = set()
    for t in tickets:
        agent_id = t["assigned_to"] if "assigned_to" in t.keys() else None
        if agent_id:
            if agent_id in seen_agents:
                continue
            seen_agents.add(agent_id)
        batch.append(t)
        if len(batch) >= max_workers:
            break
    return batch


def _process_ticket_worker(ticket_id, db_path=None, conn=None):
    """Worker task to process a single ticket safely in a thread."""
    try:
        if conn is not None:
            return process_ticket(conn, ticket_id)
        thread_conn = config.get_thread_connection(db_path)
        return process_ticket(thread_conn, ticket_id)
    except Exception as e:
        print(f"  [worker error] Unhandled error processing ticket #{ticket_id}: {e}")
        return None


def run_heartbeat(conn=None, max_workers=None, db_path=None, executor=None):
    """Execute a single heartbeat tick with concurrent worker processing."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    close_conn = False
    if conn is None:
        conn = config.get_db_connection(db_path)
        close_conn = True

    try:
        # Check for completed delegations
        check_completed_delegations(conn)

        # Find actionable tickets
        tickets = find_actionable_tickets(conn)

        if not tickets:
            print(f"[heartbeat] {now} - No actionable tickets. Sleeping...")
            return 0

        print(f"[heartbeat] {now} - {len(tickets)} actionable ticket(s) found")

        if max_workers is None:
            max_workers = config.MAX_WORKERS

        # Detect if database is backed by a file on disk
        resolved_db_path = db_path
        if not resolved_db_path and conn is not None:
            try:
                rows = conn.execute("PRAGMA database_list").fetchall()
                for r in rows:
                    if r["name"] == "main" and r["file"]:
                        resolved_db_path = r["file"]
                        break
            except Exception:
                pass

        # Select batch of tickets (at most 1 per agent, up to max_workers)
        batch = select_ticket_batch(tickets, max_workers)
        if not batch:
            return 0

        # If single worker or in-memory database with no file path:
        # process sequentially using conn (avoids thread issues with in-memory DBs)
        if max_workers <= 1 or not resolved_db_path:
            processed = 0
            for ticket in batch:
                process_ticket(conn, ticket["id"])
                processed += 1
            return processed

        # Concurrent processing across worker threads
        ticket_ids = [t["id"] for t in batch]
        worker_count = min(max_workers, len(ticket_ids))

        if executor is not None:
            futures = [
                executor.submit(_process_ticket_worker, t_id, db_path=resolved_db_path)
                for t_id in ticket_ids
            ]
            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    print(f"  [worker error] Future execution failed: {e}")
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [
                    pool.submit(_process_ticket_worker, t_id, db_path=resolved_db_path)
                    for t_id in ticket_ids
                ]
                for f in futures:
                    try:
                        f.result()
                    except Exception as e:
                        print(f"  [worker error] Future execution failed: {e}")

        return len(ticket_ids)
    finally:
        if close_conn:
            conn.close()


def run_daemon(interval=None, max_workers=None):
    """Run the heartbeat daemon loop with concurrent worker pool."""
    if interval is None:
        interval = config.POLL_INTERVAL_SECONDS
    if max_workers is None:
        max_workers = config.MAX_WORKERS

    conn = config.get_db_connection()

    print(
        f"Vectis heartbeat daemon started ({max_workers} worker threads). "
        f"Polling every {interval}s. Press Ctrl+C to stop."
    )

    # Recover interrupted tickets on startup
    recover_interrupted_tickets(conn)
    conn.close()

    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        while True:
            run_heartbeat(max_workers=max_workers, executor=executor)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nVectis daemon stopped.")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        config.close_thread_connections()


def run_single_heartbeat(max_workers=None):
    """Run a single heartbeat and exit."""
    if max_workers is None:
        max_workers = config.MAX_WORKERS
    conn = config.get_db_connection()
    recover_interrupted_tickets(conn)
    conn.close()
    return run_heartbeat(max_workers=max_workers)
 
