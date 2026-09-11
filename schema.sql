-- Vectis SQLite Schema Definition

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT 'gemini/gemini-2.5-flash',
    manager_id TEXT,
    budget_limit REAL DEFAULT 10.00,
    current_spend REAL DEFAULT 0.00,
    status TEXT CHECK(status IN ('ACTIVE', 'PAUSED_BUDGET', 'DISABLED')) DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(manager_id) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    assigned_to TEXT NOT NULL,
    created_by TEXT NOT NULL,
    parent_ticket_id INTEGER,
    status TEXT CHECK(status IN ('OPEN', 'IN_PROGRESS', 'REVIEW', 'DONE', 'FAILED', 'PAUSED_BUDGET')) DEFAULT 'OPEN',
    result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(assigned_to) REFERENCES agents(id),
    FOREIGN KEY(parent_ticket_id) REFERENCES tickets(id)
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0.00,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(ticket_id) REFERENCES tickets(id),
    FOREIGN KEY(agent_id) REFERENCES agents(id)
);

CREATE INDEX IF NOT EXISTS idx_tickets_assigned_status ON tickets(assigned_to, status);
CREATE INDEX IF NOT EXISTS idx_agents_manager ON agents(manager_id);
 
