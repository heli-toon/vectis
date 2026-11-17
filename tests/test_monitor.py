"""Tests for Vectis Terminal User Interface (TUI) Monitor."""

import curses
from unittest.mock import MagicMock
import pytest

import config
from init_db import init_database
from monitor import (
    draw_dashboard,
    format_progress_bar,
    get_system_snapshot,
    run_monitor,
    safe_addstr,
)


@pytest.fixture
def monitor_db(tmp_path):
    """Fixture to provide an initialized database for monitor tests."""
    db_file = str(tmp_path / "monitor_test.db")
    orig_db = config.DB_PATH
    config.DB_PATH = db_file
    init_database()

    conn = config.get_db_connection(db_file)
    conn.execute(
        "INSERT INTO tickets (title, description, assigned_to, created_by, status) "
        "VALUES ('Monitor Task', 'Testing TUI screen', 'ceo', 'operator', 'IN_PROGRESS')"
    )
    conn.execute(
        "INSERT INTO execution_logs (ticket_id, agent_id, prompt_tokens, completion_tokens, estimated_cost, execution_time_ms) "
        "VALUES (1, 'ceo', 50, 25, 0.0001, 150)"
    )
    conn.commit()
    conn.close()

    yield db_file
    config.DB_PATH = orig_db


def test_format_progress_bar():
    """Verify ASCII/Unicode progress bar output."""
    assert format_progress_bar(0.0, 10) == "[░░░░░░░░░░]"
    assert format_progress_bar(50.0, 10) == "[█████░░░░░]"
    assert format_progress_bar(100.0, 10) == "[██████████]"
    assert format_progress_bar(120.0, 10) == "[██████████]"


def test_get_system_snapshot(monitor_db):
    """Verify system snapshot accurately queries database state."""
    conn = config.get_db_connection(monitor_db)
    data = get_system_snapshot(conn)
    conn.close()

    assert data["total_tickets"] >= 1
    assert data["counts"].get("IN_PROGRESS") == 1
    assert len(data["agents"]) == 4
    assert len(data["recent_tickets"]) >= 1
    assert len(data["recent_logs"]) >= 1
    assert "spend_pct" in data["agents"][0]


def test_safe_addstr():
    """Verify safe_addstr clamps coordinates and doesn't raise out of bounds."""
    mock_scr = MagicMock()
    mock_scr.getmaxyx.return_value = (24, 80)

    # In bounds write
    safe_addstr(mock_scr, 5, 10, "Hello Vectis")
    mock_scr.addstr.assert_called_with(5, 10, "Hello Vectis", 0)

    # Out of bounds write (y > max_y)
    mock_scr.reset_mock()
    safe_addstr(mock_scr, 25, 10, "Out of bounds")
    mock_scr.addstr.assert_not_called()

    # Out of bounds write (x > max_x)
    mock_scr.reset_mock()
    safe_addstr(mock_scr, 5, 85, "Out of bounds")
    mock_scr.addstr.assert_not_called()


def test_draw_dashboard_small_terminal():
    """Verify small terminal displays warning message."""
    mock_scr = MagicMock()
    mock_scr.getmaxyx.return_value = (10, 40)  # Too small

    draw_dashboard(mock_scr, {"counts": {}, "agents": []})
    mock_scr.addstr.assert_called()
    call_args = mock_scr.addstr.call_args[0]
    assert "too small" in call_args[2].lower()


def test_draw_dashboard_full_render(monitor_db):
    """Verify full dashboard render calls curses routines correctly."""
    conn = config.get_db_connection(monitor_db)
    data = get_system_snapshot(conn)
    conn.close()

    mock_scr = MagicMock()
    mock_scr.getmaxyx.return_value = (30, 100)

    draw_dashboard(mock_scr, data, paused=False)
    assert mock_scr.addstr.call_count > 10
    mock_scr.refresh.assert_called_once()


def test_run_monitor_quit_on_q(monitor_db):
    """Verify run_monitor terminates cleanly when 'q' is pressed."""
    mock_scr = MagicMock()
    mock_scr.getmaxyx.return_value = (30, 100)
    mock_scr.getch.return_value = ord("q")

    # Should exit immediately
    run_monitor(mock_scr, interval_sec=1.0, db_path=monitor_db)
    mock_scr.nodelay.assert_called_with(True)
