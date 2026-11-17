"""Vectis zero-dependency embedded web dashboard and REST API.

Powered exclusively by Python's standard library (http.server, json, sqlite3).
Provides real-time visibility into agent hierarchies, ticket dependencies,
execution economics, and workspace deliverables.
"""

import http.server
import json
import mimetypes
import os
import sys
import threading
import urllib.parse
from typing import Any, Dict, List, Optional

import config
from tools import directory_list, file_read, get_root_ticket_id, get_ticket_workspace

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
DASHBOARD_HTML_PATH = os.path.join(TEMPLATES_DIR, "dashboard.html")


def get_db(db_path: Optional[str] = None):
    """Obtain a SQLite database connection with row factory."""
    return config.get_db_connection(db_path or config.DB_PATH)


def build_ticket_tree(conn) -> List[Dict[str, Any]]:
    """Build a nested ticket hierarchy tree (roots -> children)."""
    rows = conn.execute("SELECT * FROM tickets ORDER BY id ASC").fetchall()
    by_id = {}
    roots = []
    for r in rows:
        d = dict(r)
        d["children"] = []
        by_id[d["id"]] = d

    for d in by_id.values():
        pid = d.get("parent_ticket_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(d)
        else:
            roots.append(d)
    return roots


def get_execution_logs(conn, ticket_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch execution logs with token totals and parsed tool calls."""
    query = """
        SELECT l.*, t.title as ticket_title 
        FROM execution_logs l
        LEFT JOIN tickets t ON l.ticket_id = t.id
    """
    params = []
    if ticket_id is not None:
        query += " WHERE l.ticket_id = ?"
        params.append(ticket_id)
    query += " ORDER BY l.executed_at DESC, l.id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(int(limit))

    rows = conn.execute(query, tuple(params)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["total_tokens"] = (d.get("prompt_tokens") or 0) + (d.get("completion_tokens") or 0)
        tc = d.get("tool_calls")
        if tc:
            try:
                d["tool_calls"] = json.loads(tc)
            except Exception:
                d["tool_calls"] = []
        else:
            d["tool_calls"] = []
        results.append(d)
    return results


class VectisRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler for Vectis Web UI and JSON API."""

    server_db_path: Optional[str] = None

    def log_message(self, format, *args):
        # Silence default stderr logging to keep console clean
        pass

    def _get_db(self):
        target_path = getattr(self.server, "db_path", None) or self.server_db_path or config.DB_PATH
        return get_db(target_path)

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status: int = 400):
        self._send_json({"success": False, "error": message}, status=status)

    def _parse_json_body(self) -> Optional[Dict[str, Any]]:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                return {}
            raw_body = self.rfile.read(content_length).decode("utf-8")
            return json.loads(raw_body)
        except Exception as e:
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Static Dashboard Single Page App
        if path in ("", "/", "/index.html"):
            self.serve_dashboard()
            return

        conn = self._get_db()
        try:
            if path == "/api/status":
                self.handle_api_status(conn)
            elif path == "/api/agents":
                self.handle_api_agents(conn)
            elif path == "/api/tickets":
                self.handle_api_tickets(conn, query)
            elif path == "/api/tree":
                self.handle_api_tree(conn)
            elif path == "/api/logs":
                self.handle_api_logs(conn, query)
            elif path.startswith("/api/workspaces/"):
                parts = path.strip("/").split("/")
                # Pattern: api/workspaces/<ticket_id> or api/workspaces/<ticket_id>/file
                if len(parts) == 3 and parts[2].isdigit():
                    self.handle_api_workspace_info(conn, int(parts[2]))
                elif len(parts) == 4 and parts[2].isdigit() and parts[3] == "file":
                    self.handle_api_workspace_file(conn, int(parts[2]), query)
                else:
                    self._send_error("Invalid workspace path", status=404)
            else:
                self._send_error(f"Endpoint not found: {path}", status=404)
        finally:
            conn.close()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        body = self._parse_json_body()
        if body is None:
            self._send_error("Malformed JSON payload in request body", status=400)
            return

        conn = self._get_db()
        try:
            if path == "/api/tickets/seed":
                self.handle_post_tickets_seed(conn, body)
            elif path == "/api/tickets/retry":
                self.handle_post_tickets_retry(conn, body)
            elif path == "/api/tickets/cancel":
                self.handle_post_tickets_cancel(conn, body)
            elif path == "/api/budget/topup":
                self.handle_post_budget_topup(conn, body)
            elif path == "/api/agents/toggle":
                self.handle_post_agents_toggle(conn, body)
            else:
                self._send_error(f"Unknown POST endpoint: {path}", status=404)
        finally:
            conn.close()

    # Handlers
    def serve_dashboard(self):
        if not os.path.exists(DASHBOARD_HTML_PATH):
            content = "<h1>Vectis Dashboard Template Not Found</h1>".encode("utf-8")
        else:
            with open(DASHBOARD_HTML_PATH, "rb") as f:
                content = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def handle_api_status(self, conn):
        status_counts = {}
        for row in conn.execute("SELECT status, COUNT(*) as count FROM tickets GROUP BY status"):
            status_counts[row["status"]] = row["count"]

        total_tickets = sum(status_counts.values())

        agents = conn.execute("SELECT id, status, current_spend, budget_limit FROM agents").fetchall()
        total_spend = sum(a["current_spend"] for a in agents)
        total_budget = sum(a["budget_limit"] for a in agents)
        active_agents = sum(1 for a in agents if a["status"] == "ACTIVE")

        data = {
            "total_tickets": total_tickets,
            "status_counts": status_counts,
            "total_agents": len(agents),
            "active_agents": active_agents,
            "total_spend": round(total_spend, 6),
            "total_budget": round(total_budget, 2),
            "config": {
                "max_workers": config.MAX_WORKERS,
                "poll_interval": config.POLL_INTERVAL_SECONDS,
                "db_timeout": config.DB_TIMEOUT,
                "db_path": getattr(self.server, "db_path", config.DB_PATH),
            },
        }
        self._send_json(data)

    def handle_api_agents(self, conn):
        rows = conn.execute("SELECT * FROM agents ORDER BY id ASC").fetchall()
        results = []
        for r in rows:
            d = dict(r)
            spend = d.get("current_spend") or 0.0
            limit = d.get("budget_limit") or 0.0
            rem = max(0.0, limit - spend)
            pct = (spend / limit * 100.0) if limit > 0 else 0.0
            d["remaining_budget"] = round(rem, 4)
            d["spend_pct"] = round(pct, 2)
            results.append(d)
        self._send_json(results)

    def handle_api_tickets(self, conn, query):
        sql = "SELECT * FROM tickets WHERE 1=1"
        params = []

        if "status" in query and query["status"][0] != "ALL":
            sql += " AND status = ?"
            params.append(query["status"][0])

        if "assigned_to" in query:
            sql += " AND assigned_to = ?"
            params.append(query["assigned_to"][0])

        if "parent_ticket_id" in query:
            pid = query["parent_ticket_id"][0]
            if pid.lower() in ("null", "none"):
                sql += " AND parent_ticket_id IS NULL"
            else:
                sql += " AND parent_ticket_id = ?"
                params.append(int(pid))

        sql += " ORDER BY id DESC"

        if "limit" in query:
            sql += " LIMIT ?"
            params.append(int(query["limit"][0]))

        rows = conn.execute(sql, tuple(params)).fetchall()
        self._send_json([dict(r) for r in rows])

    def handle_api_tree(self, conn):
        tree = build_ticket_tree(conn)
        self._send_json(tree)

    def handle_api_logs(self, conn, query):
        ticket_id = int(query["ticket_id"][0]) if "ticket_id" in query else None
        limit = int(query["limit"][0]) if "limit" in query else 50
        logs = get_execution_logs(conn, ticket_id=ticket_id, limit=limit)
        self._send_json(logs)

    def handle_api_workspace_info(self, conn, ticket_id: int):
        ticket = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            self._send_error(f"Ticket #{ticket_id} not found", status=404)
            return

        root_id = get_root_ticket_id(conn, ticket_id)
        ws_path = get_ticket_workspace(conn, ticket_id, create=False)
        exists = os.path.exists(ws_path)
        entries = []
        if exists:
            listing = directory_list(ws_path)
            entries = listing.get("entries", [])

        self._send_json({
            "ticket_id": ticket_id,
            "root_ticket_id": root_id,
            "workspace_path": ws_path,
            "exists": exists,
            "entries": entries,
        })

    def handle_api_workspace_file(self, conn, ticket_id: int, query):
        file_path = query.get("path", [None])[0]
        if not file_path:
            self._send_error("Query parameter 'path' is required", status=400)
            return

        ws_path = get_ticket_workspace(conn, ticket_id, create=False)
        result = file_read(ws_path, file_path)
        if not result.get("success"):
            self._send_error(result.get("error", "Failed to read file"), status=404)
            return

        self._send_json(result)

    def handle_post_tickets_seed(self, conn, body):
        title = body.get("title")
        description = body.get("description", "")
        assigned_to = body.get("assigned_to", "ceo")

        if not title or not isinstance(title, str) or not title.strip():
            self._send_error("Field 'title' is required and must be a non-empty string", status=400)
            return

        agent = conn.execute("SELECT id FROM agents WHERE id = ?", (assigned_to,)).fetchone()
        if not agent:
            self._send_error(f"Agent '{assigned_to}' does not exist", status=400)
            return

        cursor = conn.execute(
            "INSERT INTO tickets (title, description, assigned_to, created_by) VALUES (?, ?, ?, ?)",
            (title.strip(), description, assigned_to, "web_ui"),
        )
        conn.commit()
        ticket_id = cursor.lastrowid
        self._send_json({
            "success": True,
            "ticket_id": ticket_id,
            "message": f"Ticket #{ticket_id} seeded successfully",
        })

    def handle_post_tickets_retry(self, conn, body):
        ticket_id = body.get("ticket_id")
        if ticket_id is None:
            self._send_error("Field 'ticket_id' is required", status=400)
            return

        ticket = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            self._send_error(f"Ticket #{ticket_id} not found", status=404)
            return

        conn.execute(
            "UPDATE tickets SET status = 'OPEN', retry_count = 0, error_message = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ticket_id,),
        )
        conn.commit()
        self._send_json({
            "success": True,
            "ticket_id": ticket_id,
            "message": f"Ticket #{ticket_id} reset to OPEN",
        })

    def handle_post_tickets_cancel(self, conn, body):
        ticket_id = body.get("ticket_id")
        if ticket_id is None:
            self._send_error("Field 'ticket_id' is required", status=400)
            return

        ticket = conn.execute("SELECT id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            self._send_error(f"Ticket #{ticket_id} not found", status=404)
            return

        conn.execute(
            "UPDATE tickets SET status = 'FAILED', error_message = 'Cancelled by operator', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ticket_id,),
        )
        conn.commit()
        self._send_json({
            "success": True,
            "ticket_id": ticket_id,
            "message": f"Ticket #{ticket_id} cancelled (status: FAILED)",
        })

    def handle_post_budget_topup(self, conn, body):
        agent_id = body.get("agent_id")
        amount = body.get("amount")

        if not agent_id or amount is None:
            self._send_error("Fields 'agent_id' and 'amount' are required", status=400)
            return

        try:
            amount_val = float(amount)
            if amount_val <= 0:
                raise ValueError()
        except ValueError:
            self._send_error("Amount must be a positive number", status=400)
            return

        agent = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not agent:
            self._send_error(f"Agent '{agent_id}' not found", status=404)
            return

        conn.execute(
            "UPDATE agents SET budget_limit = budget_limit + ? WHERE id = ?",
            (amount_val, agent_id),
        )
        new_agent = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        resumed = False
        if new_agent["status"] == "PAUSED_BUDGET" and new_agent["current_spend"] < new_agent["budget_limit"]:
            conn.execute("UPDATE agents SET status = 'ACTIVE' WHERE id = ?", (agent_id,))
            conn.execute(
                "UPDATE tickets SET status = 'OPEN', updated_at = CURRENT_TIMESTAMP WHERE assigned_to = ? AND status = 'PAUSED_BUDGET'",
                (agent_id,),
            )
            resumed = True
        conn.commit()

        self._send_json({
            "success": True,
            "agent_id": agent_id,
            "amount_added": amount_val,
            "new_limit": new_agent["budget_limit"],
            "current_spend": new_agent["current_spend"],
            "resumed": resumed,
        })

    def handle_post_agents_toggle(self, conn, body):
        agent_id = body.get("agent_id")
        target_status = body.get("status")

        if not agent_id or not target_status:
            self._send_error("Fields 'agent_id' and 'status' are required", status=400)
            return

        if target_status not in ("ACTIVE", "DISABLED", "PAUSED_BUDGET"):
            self._send_error("Status must be ACTIVE, DISABLED, or PAUSED_BUDGET", status=400)
            return

        agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not agent:
            self._send_error(f"Agent '{agent_id}' not found", status=404)
            return

        conn.execute("UPDATE agents SET status = ? WHERE id = ?", (target_status, agent_id))
        conn.commit()

        self._send_json({
            "success": True,
            "agent_id": agent_id,
            "status": target_status,
        })


class VectisHTTPServer(http.server.ThreadingHTTPServer):
    """Threaded HTTP Server for Vectis Dashboard with custom database path binding."""

    def __init__(self, server_address, RequestHandlerClass, db_path=None):
        self.db_path = db_path
        super().__init__(server_address, RequestHandlerClass)


def create_server(host: str = "127.0.0.1", port: int = 8080, db_path: Optional[str] = None) -> VectisHTTPServer:
    """Create and return a configured Vectis HTTP server instance."""
    server = VectisHTTPServer((host, port), VectisRequestHandler, db_path=db_path)
    return server


def start_server(host: str = "127.0.0.1", port: int = 8080, db_path: Optional[str] = None, blocking: bool = True):
    """Start the Vectis dashboard server."""
    server = create_server(host, port, db_path)
    print(f"\n🚀 Vectis Web Dashboard running at http://{host}:{port}/")
    print("Press Ctrl+C to stop server.\n")

    if blocking:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down Vectis Web Dashboard...")
        finally:
            server.server_close()
    else:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server
