"""Tests for database schema initialization, indexes, migration, and pragmas."""

import sqlite3
import config
from init_db import init_database, DEFAULT_AGENTS


def test_schema_tables_exist(mem_db):
    """Verify core tables exist in schema."""
    cursor = mem_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    assert "agents" in tables
    assert "tickets" in tables
    assert "execution_logs" in tables
    assert "memories" in tables


def test_schema_columns(mem_db):
    """Verify enhanced Phase 1 columns exist in tickets and execution_logs."""
    tickets_cols = {row["name"] for row in mem_db.execute("PRAGMA table_info(tickets)").fetchall()}
    assert "retry_count" in tickets_cols
    assert "max_retries" in tickets_cols
    assert "error_message" in tickets_cols
    assert "requires_approval" in tickets_cols

    agents_cols = {row["name"] for row in mem_db.execute("PRAGMA table_info(agents)").fetchall()}
    assert "allowed_tools" in agents_cols

    logs_cols = {row["name"] for row in mem_db.execute("PRAGMA table_info(execution_logs)").fetchall()}
    assert "execution_time_ms" in logs_cols
    assert "tool_calls" in logs_cols


def test_schema_indexes(mem_db):
    """Verify performance indexes are created."""
    cursor = mem_db.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = {row["name"] for row in cursor.fetchall()}
    assert "idx_tickets_assigned_status" in indexes
    assert "idx_agents_manager" in indexes


def test_default_agents_seeding(mem_db):
    """Verify agents seeded in mem_db match expected defaults."""
    rows = mem_db.execute("SELECT * FROM agents").fetchall()
    assert len(rows) == len(DEFAULT_AGENTS)

    agents_by_id = {r["id"]: r for r in rows}
    assert "ceo" in agents_by_id
    assert agents_by_id["ceo"]["manager_id"] is None
    assert agents_by_id["dev"]["manager_id"] == "ceo"
    assert agents_by_id["copywriter"]["manager_id"] == "ceo"
    assert agents_by_id["reviewer"]["manager_id"] == "ceo"


def test_schema_migration_idempotent(tmp_path, monkeypatch):
    """Verify init_database migrates older tables missing columns cleanly."""
    db_file = str(tmp_path / "test_migration.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)

    # Create old schema without retry_count, max_retries, error_message, execution_time_ms
    old_conn = sqlite3.connect(db_file)
    old_conn.execute("""
        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            model_name TEXT NOT NULL DEFAULT 'gemini/gemini-2.5-flash',
            manager_id TEXT,
            budget_limit REAL DEFAULT 10.00,
            current_spend REAL DEFAULT 0.00,
            status TEXT DEFAULT 'ACTIVE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    old_conn.execute("""
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            assigned_to TEXT NOT NULL,
            created_by TEXT NOT NULL,
            parent_ticket_id INTEGER,
            status TEXT DEFAULT 'OPEN',
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    old_conn.execute("""
        CREATE TABLE execution_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            agent_id TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            estimated_cost REAL DEFAULT 0.00,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    old_conn.commit()
    old_conn.close()

    # Run init_database which should apply migrations
    init_database()

    # Verify columns were added
    verify_conn = config.get_db_connection()
    tickets_cols = {row["name"] for row in verify_conn.execute("PRAGMA table_info(tickets)").fetchall()}
    assert "retry_count" in tickets_cols
    assert "max_retries" in tickets_cols
    assert "error_message" in tickets_cols
    assert "requires_approval" in tickets_cols

    agents_cols = {row["name"] for row in verify_conn.execute("PRAGMA table_info(agents)").fetchall()}
    assert "allowed_tools" in agents_cols

    logs_cols = {row["name"] for row in verify_conn.execute("PRAGMA table_info(execution_logs)").fetchall()}
    assert "execution_time_ms" in logs_cols
    assert "tool_calls" in logs_cols
    verify_conn.close()


def test_wal_pragma_configuration(tmp_path):
    """Verify WAL mode and pragmas in get_db_connection."""
    db_file = str(tmp_path / "wal_test.db")
    conn = config.get_db_connection(db_file)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.upper() == "WAL"

        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_timeout == 5000

        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert foreign_keys == 1
    finally:
        conn.close()
