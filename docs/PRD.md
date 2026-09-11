# Product Requirements Document (PRD)

## Product Name: Vectis
**Document Version:** 1.0.0  
**Status:** Approved for Implementation  
**Target Architecture:** Low-resource deployments (1GB RAM single-core VPS)  

---

## 1. Executive Summary & Objective

### 1.1 Product Vision
Vectis is an enterprise-capable, ultra-lightweight multi-agent orchestration system that provides complete organizational hierarchy management, task delegation, budget tracking, and autonomous execution loops with an idle memory footprint under **35 MB RAM**. 

### 1.2 Core Problem Statement
Modern multi-agent AI frameworks (e.g., AutoGen, CrewAI) rely on heavy dependency stacks (Pydantic, LangChain, complex telemetry, vector databases) that consume hundreds of megabytes of RAM before executing a single line of business logic. On low-spec hosting environments (1GB RAM VPS), these frameworks frequently trigger system Out-Of-Memory (OOM) killer terminations, exhibit high idle CPU consumption, and hide system state within deep software abstractions.

### 1.3 Key Value Proposition
- **Ultra-Low Memory:** < 35 MB idle RAM usage.
- **Dependency Minimization:** Built entirely on Python standard library (`sqlite3`, `json`, `time`) and `litellm`.
- **Model Agnostic:** Instant provider switching via LiteLLM (Gemini, Groq, Claude, OpenAI, Ollama).
- **Hard Cost Controls:** Per-agent financial ceiling enforcement.
- **Auditability:** Complete relational transparency via pure SQLite query logs.

---

## 2. Technical System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                               VECTIS RUNTIME                                     │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   ┌──────────────────────────────────────────────────────────────────────────┐   │
│   │                         MAIN HEARTBEAT DAEMON                            │   │
│   │                            (main.py / sleep)                             │   │
│   └────────────────────────────────────┬─────────────────────────────────────┘   │
│                                        │                                         │
│                                        ▼                                         │
│   ┌──────────────────────────────────────────────────────────────────────────┐   │
│   │                           AGENT ENGINE (35 MB)                           │   │
│   │                                                                          │   │
│   │   1. Poll Pending Tickets    ───► 2. Memento Context Assembly            │   │
│   │   4. Update DB & Sub-Tasks   ◄─── 3. LiteLLM Completion Request          │   │
│   └──────────────┬──────────────────────────────────────────┬────────────────┘   │
│                  │                                          │                    │
│                  ▼                                          ▼                    │
│   ┌──────────────────────────────┐          ┌────────────────────────────────┐   │
│   │      SQLITE DATABASE         │          │       LLM API PROVIDERS        │   │
│   │    (paperclip_mini.db)       │          │   (Gemini, Groq, Anthropic)    │   │
│   │ • agents                     │          │                                │   │
│   │ • tickets                    │          │ via LiteLLM Unified Transport  │   │
│   │ • execution_logs             │          └────────────────────────────────┘   │
│   └──────────────────────────────┘                                               │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Data Architecture & Database Schema

The persistent engine utilizes SQLite native thread-safe storage. The schema enforces strict foreign key relations and indexing for low-latency queries.

```sql
-- SQLite Schema Definition (schema.sql)

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
```

---

## 4. Functional Requirements

### FR-1: Agent Management & Org Structure
- **FR-1.1:** System shall support unlimited hierarchical agent levels (e.g., Owner -> CEO -> Lead -> Specialist).
- **FR-1.2:** System shall allow distinct model assignments per agent profile (e.g., high-reasoning models for management, ultra-fast models for execution).
- **FR-1.3:** System shall isolate system prompts per agent, representing specialized agent capabilities and constraints.

### FR-2: Execution Loop & Heartbeat Mechanics
- **FR-2.1:** System shall operate on a configurable polling heartbeat loop (`POLL_INTERVAL_SECONDS`).
- **FR-2.2:** During each tick, system shall process at most `MAX_CONCURRENT_TASKS` (default: 1 for minimal memory footprint) per agent.
- **FR-2.3:** Process execution shall be stateless between heartbeats; memory context is dynamically hydrated on-demand from SQLite ("Memento Pattern").

### FR-3: Task Delegation & State Machine
- **FR-3.1:** Valid ticket statuses shall strictly strictly obey state transitions: `OPEN` -> `IN_PROGRESS` -> [`REVIEW` | `DONE` | `FAILED` | `PAUSED_BUDGET`].
- **FR-3.2:** System shall parse JSON response structured output containing optional `new_tickets` array.
- **FR-3.3:** Newly spawned child tickets shall inherit the parent ticket's ID as `parent_ticket_id` for hierarchical lineage tracking.

### FR-4: Budget Enforcement & Token Accounting
- **FR-4.1:** Prior to dispatching an API call, system shall verify that `current_spend < budget_limit`.
- **FR-4.2:** Post-call, system shall calculate estimated cost using LiteLLM response usage metadata (`response.usage`) and increment `current_spend`.
- **FR-4.3:** If budget limit is exceeded, system shall update agent status to `PAUSED_BUDGET` and reject further ticket execution until limit is raised.

---

## 5. Non-Functional Requirements

### NFR-1: Resource Constraints
- **Idle Memory:** Process memory consumption must remain below 35 MB RAM when resting between heartbeats.
- **Active Memory:** Peak execution memory must not exceed 60 MB RAM during API payload serialization/deserialization.
- **CPU Footprint:** Sleep duration must yield 0% CPU usage on idle ticks.

### NFR-2: Reliability & Error Recovery
- **OOM Resiliency:** In the event of a abrupt power failure or system restart, ticket status in state `IN_PROGRESS` shall safely revert to `OPEN` upon daemon initialization.
- **API Fault Tolerance:** Transient network errors or rate limits from LLM providers must caught gracefully, retried with exponential backoff, and must not crash the heartbeat daemon.

### NFR-3: Transparency & Auditing
- **Zero Black Box:** All inter-agent communication, delegation events, and task completions must be written in human-readable plain text into SQLite.

---

## 6. Implementation Architecture & Files Map

```
paperclip_mini/
├── schema.sql           # SQLite database table definitions and indices
├── init_db.py           # Database bootstrapper and default Org Seeder
├── agent_engine.py      # Core Memento context builder, LiteLLM wrapper, state machine
├── heartbeat.py         # Polling loop daemon process
├── config.py            # System configuration settings
├── requirements.txt     # Single non-std dependency: litellm
├── Plan.md              # End-User Journey & Feature Architecture
└── PRD.md               # Product Requirements Document
```
