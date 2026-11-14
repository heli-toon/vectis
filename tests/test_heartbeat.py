"""Tests for Vectis heartbeat: crash recovery, delegation resolution, and ticket discovery."""

from unittest.mock import patch
import heartbeat


def test_recover_interrupted_tickets(mem_db):
    """Verify lingering IN_PROGRESS tickets revert to OPEN on boot."""
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Task 1', 'desc', 'dev', 'ceo', 'IN_PROGRESS'),"
        "('Task 2', 'desc', 'dev', 'ceo', 'IN_PROGRESS'),"
        "('Task 3', 'desc', 'dev', 'ceo', 'DONE'),"
        "('Task 4', 'desc', 'dev', 'ceo', 'REVIEW'),"
        "('Task 5', 'desc', 'dev', 'ceo', 'FAILED')"
    )
    mem_db.commit()

    reverted = heartbeat.recover_interrupted_tickets(mem_db)
    assert reverted == 2

    # Check statuses
    statuses = {
        row["title"]: row["status"]
        for row in mem_db.execute("SELECT title, status FROM tickets").fetchall()
    }
    assert statuses["Task 1"] == "OPEN"
    assert statuses["Task 2"] == "OPEN"
    assert statuses["Task 3"] == "DONE"
    assert statuses["Task 4"] == "REVIEW"
    assert statuses["Task 5"] == "FAILED"


def test_check_completed_delegations_all_done(mem_db):
    """Verify parent in REVIEW re-opens when all child tickets are DONE."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Parent Task', 'desc', 'ceo', 'human', 'REVIEW')"
    )
    parent_id = cursor.lastrowid

    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id, status) VALUES "
        "('Child 1', 'desc', 'dev', 'ceo', ?, 'DONE'),"
        "('Child 2', 'desc', 'copywriter', 'ceo', ?, 'DONE')",
        (parent_id, parent_id),
    )
    mem_db.commit()

    resolved = heartbeat.check_completed_delegations(mem_db)
    assert resolved == 1

    parent = mem_db.execute("SELECT status FROM tickets WHERE id = ?", (parent_id,)).fetchone()
    assert parent["status"] == "OPEN"


def test_check_completed_delegations_with_failed_child(mem_db):
    """Verify parent in REVIEW re-opens when children are DONE or FAILED."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Parent Task', 'desc', 'ceo', 'human', 'REVIEW')"
    )
    parent_id = cursor.lastrowid

    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id, status) VALUES "
        "('Child 1', 'desc', 'dev', 'ceo', ?, 'DONE'),"
        "('Child 2', 'desc', 'copywriter', 'ceo', ?, 'FAILED')",
        (parent_id, parent_id),
    )
    mem_db.commit()

    resolved = heartbeat.check_completed_delegations(mem_db)
    assert resolved == 1

    parent = mem_db.execute("SELECT status FROM tickets WHERE id = ?", (parent_id,)).fetchone()
    assert parent["status"] == "OPEN"


def test_check_completed_delegations_pending_child(mem_db):
    """Verify parent stays in REVIEW if any child is still OPEN or IN_PROGRESS."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Parent Task', 'desc', 'ceo', 'human', 'REVIEW')"
    )
    parent_id = cursor.lastrowid

    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, parent_ticket_id, status) VALUES "
        "('Child 1', 'desc', 'dev', 'ceo', ?, 'DONE'),"
        "('Child 2', 'desc', 'copywriter', 'ceo', ?, 'IN_PROGRESS')",
        (parent_id, parent_id),
    )
    mem_db.commit()

    resolved = heartbeat.check_completed_delegations(mem_db)
    assert resolved == 0

    parent = mem_db.execute("SELECT status FROM tickets WHERE id = ?", (parent_id,)).fetchone()
    assert parent["status"] == "REVIEW"


def test_find_actionable_tickets(mem_db):
    """Verify actionable ticket discovery filters by status and budget."""
    # Active agent within budget
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Actionable 1', 'desc', 'dev', 'ceo', 'OPEN'),"
        "('Actionable 2', 'desc', 'copywriter', 'ceo', 'OPEN')"
    )

    # Paused agent
    mem_db.execute("UPDATE agents SET status = 'PAUSED_BUDGET' WHERE id = 'reviewer'")
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Paused Agent Task', 'desc', 'reviewer', 'ceo', 'OPEN')"
    )

    # Over-budget agent
    mem_db.execute("UPDATE agents SET current_spend = 10.00, budget_limit = 10.00 WHERE id = 'ceo'")
    mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Over Budget Task', 'desc', 'ceo', 'human', 'OPEN')"
    )
    mem_db.commit()

    actionable = heartbeat.find_actionable_tickets(mem_db)
    actionable_ids = [row["id"] for row in actionable]
    assert len(actionable_ids) == 2


def test_run_heartbeat(mem_db):
    """Verify single heartbeat tick processes actionable tickets."""
    cursor = mem_db.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) VALUES "
        "('Tick Task', 'desc', 'dev', 'ceo', 'OPEN')"
    )
    ticket_id = cursor.lastrowid
    mem_db.commit()

    with patch("heartbeat.process_ticket") as mock_process:
        processed = heartbeat.run_heartbeat(mem_db)
        assert processed == 1
        mock_process.assert_called_once_with(mem_db, ticket_id)
