"""Vectis Terminal User Interface (TUI) Monitor.

Built exclusively on Python's standard library 'curses' module.
Provides an interactive real-time dashboard for remote servers and SSH sessions.
"""

import curses
import datetime
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import config


def get_system_snapshot(conn) -> Dict[str, Any]:
    """Retrieve all current system data from SQLite in a single transaction."""
    # Agents
    agents = [dict(r) for r in conn.execute("SELECT * FROM agents ORDER BY id ASC").fetchall()]
    for a in agents:
        spend = a.get("current_spend") or 0.0
        limit = a.get("budget_limit") or 0.0
        a["spend_pct"] = (spend / limit * 100.0) if limit > 0 else 0.0

    # Status counts
    counts = {}
    for row in conn.execute("SELECT status, COUNT(*) as count FROM tickets GROUP BY status"):
        counts[row["status"]] = row["count"]

    total_tickets = sum(counts.values())

    # Recent tickets
    recent_tickets = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tickets ORDER BY updated_at DESC, id DESC LIMIT 8"
        ).fetchall()
    ]

    # Recent execution logs
    recent_logs = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM execution_logs ORDER BY executed_at DESC, id DESC LIMIT 6"
        ).fetchall()
    ]

    total_spend = sum(a["current_spend"] for a in agents)
    total_budget = sum(a["budget_limit"] for a in agents)

    return {
        "agents": agents,
        "counts": counts,
        "total_tickets": total_tickets,
        "recent_tickets": recent_tickets,
        "recent_logs": recent_logs,
        "total_spend": total_spend,
        "total_budget": total_budget,
    }


def format_progress_bar(pct: float, width: int = 10) -> str:
    """Format an ASCII/Unicode progress bar."""
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * width))
    empty = width - filled
    return "[" + ("█" * filled) + ("░" * empty) + "]"


def safe_addstr(stdscr, y: int, x: int, text: str, attr: int = 0):
    """Safely write string to curses window without raising out-of-bounds error."""
    max_y, max_x = stdscr.getmaxyx()
    if y < 0 or y >= max_y or x < 0 or x >= max_x:
        return
    available_width = max_x - x - 1
    if available_width <= 0:
        return
    clipped = text[:available_width]
    try:
        stdscr.addstr(y, x, clipped, attr)
    except curses.error:
        pass


def draw_dashboard(stdscr, data: Dict[str, Any], paused: bool = False, colors: Optional[Dict[str, int]] = None):
    """Render the full Vectis TUI monitor screen."""
    colors = colors or {}
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()

    if max_y < 16 or max_x < 70:
        safe_addstr(stdscr, 0, 0, "Terminal window too small for Vectis Monitor. Resize to at least 70x16.", curses.A_BOLD)
        stdscr.refresh()
        return

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c_primary = colors.get("primary", 0)
    c_green = colors.get("green", 0)
    c_yellow = colors.get("yellow", 0)
    c_red = colors.get("red", 0)
    c_cyan = colors.get("cyan", 0)
    c_dim = colors.get("dim", 0)

    # 1. Header
    title = " VECTIS "
    safe_addstr(stdscr, 0, 0, title, curses.A_BOLD | curses.A_REVERSE | c_primary)
    meta = f" Autonomous Multi-Agent Engine  |  Workers: {config.MAX_WORKERS}  |  Poll: {config.POLL_INTERVAL_SECONDS}s"
    safe_addstr(stdscr, 0, len(title), meta, curses.A_BOLD)
    safe_addstr(stdscr, 0, max_x - len(now_str) - 2, now_str, c_dim)

    # 2. Tickets Metrics Summary Bar
    c = data["counts"]
    open_c = c.get("OPEN", 0)
    act_c = c.get("IN_PROGRESS", 0)
    rev_c = c.get("REVIEW", 0)
    done_c = c.get("DONE", 0)
    fail_c = c.get("FAILED", 0)
    pau_c = c.get("PAUSED_BUDGET", 0)

    summary_text = (
        f" Tickets ({data['total_tickets']}): "
        f"OPEN: {open_c} | ACTIVE: {act_c} | REVIEW: {rev_c} | DONE: {done_c} | FAILED: {fail_c} | PAUSED: {pau_c}"
    )
    safe_addstr(stdscr, 2, 0, summary_text, curses.A_REVERSE)

    # 3. Agents Table
    row = 4
    safe_addstr(stdscr, row, 0, "AGENT ORGANIZATIONAL POOL & ECONOMIC BUDGETS", curses.A_BOLD | c_cyan)
    row += 1
    header = f"{'ID':<10} {'ROLE':<14} {'STATUS':<14} {'MODEL':<24} {'SPEND':>9} {'CAP':>8} {'USAGE'}"
    safe_addstr(stdscr, row, 0, header, curses.A_UNDERLINE | c_dim)
    row += 1

    for agent in data["agents"]:
        if row >= max_y - 12:
            break
        a_id = agent["id"]
        role = agent["role"][:13]
        status = agent["status"]
        model = agent["model_name"][:23]
        spend = f"${agent['current_spend']:.4f}"
        limit = f"${agent['budget_limit']:.2f}"
        pct = agent["spend_pct"]
        bar = f"{format_progress_bar(pct, 8)} {pct:>5.1f}%"

        s_attr = curses.A_NORMAL
        if status == "ACTIVE":
            s_attr = c_green
        elif status == "PAUSED_BUDGET":
            s_attr = c_yellow | curses.A_BOLD
        elif status == "DISABLED":
            s_attr = c_dim

        line = f"{a_id:<10} {role:<14} {status:<14} {model:<24} {spend:>9} {limit:>8} {bar}"
        safe_addstr(stdscr, row, 0, line)
        # Highlight status token
        safe_addstr(stdscr, row, 25, f"{status:<14}", s_attr)
        row += 1

    # 4. Recent Tickets
    row += 1
    if row < max_y - 7:
        safe_addstr(stdscr, row, 0, "ACTIVE & RECENT TICKETS", curses.A_BOLD | c_cyan)
        row += 1
        t_header = f"{'ID':<5} {'STATUS':<13} {'ASSIGN':<9} {'RETRIES':<9} {'TITLE'}"
        safe_addstr(stdscr, row, 0, t_header, curses.A_UNDERLINE | c_dim)
        row += 1

        for t in data["recent_tickets"][:6]:
            if row >= max_y - 6:
                break
            t_id = f"#{t['id']}"
            t_status = t["status"]
            t_assign = f"@{t['assigned_to']}"
            t_retries = f"{t.get('retry_count', 0)}/{t.get('max_retries', 3)}"
            t_title = t["title"]

            st_attr = curses.A_NORMAL
            if t_status == "DONE":
                st_attr = c_green
            elif t_status == "IN_PROGRESS":
                st_attr = c_yellow | curses.A_BOLD
            elif t_status == "FAILED":
                st_attr = c_red | curses.A_BOLD
            elif t_status == "REVIEW":
                st_attr = c_cyan

            line = f"{t_id:<5} {t_status:<13} {t_assign:<9} {t_retries:<9} {t_title}"
            safe_addstr(stdscr, row, 0, line)
            safe_addstr(stdscr, row, 6, f"{t_status:<13}", st_attr)
            row += 1

    # 5. Recent Execution Logs
    row += 1
    if row < max_y - 3:
        safe_addstr(stdscr, row, 0, "RECENT EXECUTION AUDIT TRAIL", curses.A_BOLD | c_cyan)
        row += 1
        l_header = f"{'TICKET':<8} {'AGENT':<9} {'TOKENS':>8} {'COST ($)':>10} {'LATENCY':>9} {'TIMESTAMP'}"
        safe_addstr(stdscr, row, 0, l_header, curses.A_UNDERLINE | c_dim)
        row += 1

        for log in data["recent_logs"][:3]:
            if row >= max_y - 2:
                break
            lt_id = f"#{log['ticket_id']}"
            l_agent = f"@{log['agent_id']}"
            total_tokens = (log.get("prompt_tokens") or 0) + (log.get("completion_tokens") or 0)
            cost_str = f"${log.get('estimated_cost', 0):.4f}"
            lat = f"{log.get('execution_time_ms', 0)}ms" if log.get("execution_time_ms") else "—"
            time_str = str(log.get("executed_at", ""))[:19]

            line = f"{lt_id:<8} {l_agent:<9} {total_tokens:>8} {cost_str:>10} {lat:>9} {time_str}"
            safe_addstr(stdscr, row, 0, line)
            row += 1

    # 6. Footer bar
    foot_status = "PAUSED" if paused else "LIVE"
    f_attr = c_yellow if paused else c_green
    footer = f" [{foot_status}]  [Q] Quit  |  [R] Refresh Now  |  [P] Pause/Resume  |  Database: {config.DB_PATH} "
    safe_addstr(stdscr, max_y - 1, 0, footer.ljust(max_x - 1), curses.A_REVERSE | f_attr)

    stdscr.refresh()


def init_curses_colors() -> Dict[str, int]:
    """Initialize curses color pairs if supported."""
    colors = {}
    try:
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_RED, -1)
            curses.init_pair(4, curses.COLOR_CYAN, -1)
            curses.init_pair(5, curses.COLOR_BLUE, -1)
            curses.init_pair(6, curses.COLOR_WHITE, -1)

            colors["green"] = curses.color_pair(1)
            colors["yellow"] = curses.color_pair(2)
            colors["red"] = curses.color_pair(3)
            colors["cyan"] = curses.color_pair(4)
            colors["primary"] = curses.color_pair(5)
            colors["dim"] = curses.color_pair(6) | curses.A_DIM
    except Exception:
        pass
    return colors


def run_monitor(stdscr, interval_sec: float = 2.0, db_path: Optional[str] = None):
    """Main event loop for curses TUI."""
    target_db = db_path or config.DB_PATH
    try:
        curses.curs_set(0)
    except Exception:
        pass
    stdscr.nodelay(True)

    colors = init_curses_colors()
    paused = False
    last_refresh = 0.0
    data = None

    while True:
        now = time.time()
        # Handle input
        try:
            ch = stdscr.getch()
        except Exception:
            ch = -1

        if ch in (ord("q"), ord("Q"), 27):  # 'q' or ESC
            break
        elif ch in (ord("r"), ord("R")):
            last_refresh = 0.0  # Force immediate refresh
        elif ch in (ord("p"), ord("P")):
            paused = not paused
            last_refresh = 0.0

        if not paused and (now - last_refresh >= interval_sec or data is None):
            try:
                conn = config.get_db_connection(target_db)
                data = get_system_snapshot(conn)
                conn.close()
                last_refresh = now
            except Exception as e:
                safe_addstr(stdscr, 1, 0, f"Error reading database: {e}", curses.A_BOLD)
                stdscr.refresh()

        if data:
            draw_dashboard(stdscr, data, paused=paused, colors=colors)

        time.sleep(0.1)


def start_monitor(interval_sec: float = 2.0, db_path: Optional[str] = None):
    """Entry point to launch curses monitor."""
    try:
        curses.wrapper(run_monitor, interval_sec=interval_sec, db_path=db_path)
    except KeyboardInterrupt:
        pass
