# Vectis

Ultra-lightweight multi-agent orchestration system with hierarchical delegation, budget controls, and crash-resilient state management.

## Quick Start

```bash
# 1. Install dependencies and CLI package
pip3 install -e .

# 2. Initialize the database (creates 4 default agents)
vectis init

# 3. Set at least one LLM API key
export GEMINI_API_KEY=your_key_here
# (or OPENAI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY)

# 4. Seed a task
vectis seed "Write a welcome email" --desc "Create a welcome email for new SaaS users" --assign ceo

# 5. Run the heartbeat daemon
vectis run
```

## CLI Commands

Commands can be invoked directly via `vectis <cmd>` or via `python3 main.py <cmd>`:

| Command | Description |
|---------|-------------|
| `vectis init` | Initialize database with default agents |
| `vectis run` | Start the heartbeat daemon (polls every 30s) |
| `vectis run --once` | Run a single heartbeat tick and exit |
| `vectis run --interval 10` | Run with custom poll interval (seconds) |
| `vectis run --workers 4` | Run with custom worker thread pool size |
| `vectis seed "Title" --desc "..." --assign ceo` | Seed a root ticket |
| `vectis seed "Title" --approval` | Seed a root ticket requiring operator approval before execution |
| `vectis status` | Show system overview (agents, ticket counts, recent activity) |
| `vectis agents` | List all agents with details |
| `vectis tickets` | List all tickets with results |
| `vectis tickets approve <ticket_id>` | Approve a ticket awaiting operator approval (`AWAITING_APPROVAL` -> `OPEN`) |
| `vectis tickets reject <ticket_id>` | Reject a ticket awaiting operator approval (`AWAITING_APPROVAL` -> `FAILED`) |
| `vectis tickets retry <ticket_id>` | Reset ticket retry counter and status back to OPEN |
| `vectis tickets cancel <ticket_id>` | Cancel ticket and set status to FAILED |
| `vectis budget topup <agent_id> <amount>` | Increase agent dollar budget limit |
| `vectis logs <ticket_id>` | Pretty-print execution history, token costs, and latencies |
| `vectis workspace <ticket_id>` | Inspect file workspace deliverables for a ticket |
| `vectis tree` | Show ticket hierarchy tree |
| `vectis tree 1` | Show tree for a specific ticket |
| `vectis memory add --title "..." --content "..."` | Record knowledge into institutional memory |
| `vectis memory search "<query>"` | Query institutional memory via SQLite FTS5 BM25 ranking |
| `vectis ui` | Start the zero-dependency embedded web dashboard (`http://127.0.0.1:8080`) |
| `vectis ui --port 9000 --open` | Run dashboard on custom port and open in default browser |
| `vectis monitor` | Start the live curses terminal dashboard over SSH |
| `vectis monitor --interval 1.0` | Run terminal monitor with 1-second refresh rate |

## Observability & Dashboards

Vectis includes two zero-dependency observability interfaces built strictly using Python standard library:

### 1. Embedded Web Dashboard
Launch with:
```bash
python3 main.py ui --port 8080
```
- **Live Org Chart:** Visual agent hierarchy, status badges, active models, and real-time spend vs budget meters.
- **Ticket Dependency Graph:** Interactive tree/DAG showing parent objectives, delegated child sub-tasks, retries, and deliverable status.
- **Real-Time Execution Logs:** Live stream of token consumption, LLM transport costs, latency (ms), and expandable tool call audit logs.
- **Workspace File Explorer:** Built-in code & artifact viewer to inspect files produced in `workspaces/ticket_<id>/`.
- **Operator Controls:** Web forms and modal dialogs to seed tasks, top up agent budgets, and pause/resume agents.
- **Zero Dependencies:** Pure Vanilla HTML5, CSS3, and ES6 JavaScript. No Node.js, npm, React, or external CDNs; functions seamlessly in airgapped environments.

### 2. Live Terminal User Interface (TUI) Monitor
Launch with:
```bash
python3 main.py monitor
```
- Designed for lightweight SSH administration and headless VPS instances.
- Renders live agent budget progress meters (`[████░░░░░░] 40%`), ticket status distribution (`OPEN`, `IN_PROGRESS`, `DONE`, `FAILED`), active tasks, and recent execution logs.
- Keyboard shortcuts: `[Q]` quit, `[R]` refresh now, `[P]` pause/resume auto-refresh. Safe against window resizing.


## Architecture

### Agents (Org Chart)

```
CEO (gemini-2.5-flash)
├── Developer (llama-3.3-70b via Groq)
├── Copywriter (claude-3-5-haiku)
└── Reviewer (gemini-2.5-flash)
```

Each agent has a role, system prompt, LLM model, budget limit, and manager. Agents can delegate to their subordinates via sub-tickets.

### Ticket Lifecycle

```
[Seed --approval] → AWAITING_APPROVAL → OPEN (upon operator approval)
                                      → FAILED (upon operator rejection)

OPEN → IN_PROGRESS → DONE
                   → REVIEW (delegated, waiting on sub-tasks)
                   → FAILED
                   → PAUSED_BUDGET
```

When all sub-tickets of a REVIEW ticket reach DONE/FAILED, the parent ticket is automatically re-opened for the manager to review results.

### Institutional Memory & Zero-Bloat RAG (SQLite FTS5)

- Zero external vector database dependencies (no ChromaDB, FAISS, or heavy embedding models).
- Uses SQLite's native C FTS5 engine with Porter stemming (`tokenize='porter unicode61'`) and BM25 relevance ranking.
- When tickets complete (`DONE`), solutions and key architectural decisions are automatically indexed into memory.
- During context assembly, the Memento pattern dynamically retrieves top-k relevant past knowledge entries.
- Agents can query memory at any time via the `search_knowledge` tool.

### Human-in-the-Loop (HITL) Guardrails & Tool RBAC

- Critical tasks can require operator signoff before execution (`requires_approval = 1` / `AWAITING_APPROVAL`).
- Operators approve or reject tasks with one click in the Web UI or via `vectis tickets approve` / `vectis tickets reject`.
- Granular Role-Based Access Control (`allowed_tools` column per agent): prevents non-technical agents from executing shell commands.

### Memento Pattern

Agents are stateless LLM calls. All context is reconstructed from the SQLite database on each execution:
- Agent identity, system prompt, org position
- Budget status
- Current ticket details and parent context
- Sub-task results
- Execution history (token usage, costs)

### Budget Enforcement

- Each agent has a per-agent budget limit (default: $5.00)
- Costs are calculated from token usage and model pricing
- When spend exceeds the limit, the agent is set to `PAUSED_BUDGET` and tickets are put on hold
- Budgets can be topped up via direct SQL update

### Crash Resilience

- All state lives in SQLite, not in memory
- On daemon startup, any `IN_PROGRESS` tickets are reverted to `OPEN`
- The daemon can be killed and restarted without losing progress

## Configuration

Environment variables (all optional, with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `VECTIS_DB_PATH` | `vectis.db` | SQLite database path |
| `VECTIS_DB_TIMEOUT` | `30.0` | SQLite driver busy timeout (seconds) |
| `VECTIS_POLL_INTERVAL` | `30` | Heartbeat poll interval (seconds) |
| `VECTIS_MAX_WORKERS` | `4` | Max concurrent worker threads in heartbeat |
| `VECTIS_MAX_CONCURRENT` | `4` | Alias for `VECTIS_MAX_WORKERS` |
| `VECTIS_TEMPERATURE` | `0.3` | LLM temperature |
| `VECTIS_MAX_TOKENS` | `4096` | Max tokens per LLM call |
| `VECTIS_MAX_RETRIES` | `3` | LLM retry count |

## Adding Custom Agents

```bash
python3 -c "
import config
conn = config.get_db_connection()
conn.execute('''
    INSERT INTO agents (id, name, role, system_prompt, model_name, manager_id, budget_limit)
    VALUES (?, ?, ?, ?, ?, ?, ?)
''', ('analyst', 'Data Analyst', 'Analyst',
      'You are a Data Analyst...', 'openai/gpt-4o-mini', 'ceo', 3.00))
conn.commit()
conn.close()
"
```

## Turnkey Deployment

### Docker & Docker Compose
Vectis includes an ultra-lightweight multi-stage Alpine Dockerfile (< 65 MB) and Docker Compose configuration:

```bash
# Start background daemon and web dashboard together:
docker compose up -d

# Check logs:
docker compose logs -f daemon

# Inspect web dashboard at http://localhost:8080
```

### Linux VPS / Systemd
Auto-restart systemd unit files are provided:
- `vectis.service`: Heartbeat execution daemon
- `vectis-ui.service`: Embedded web dashboard

See [docs/DEPLOYMENT.md](file:///home/muhaimin/Documents/vectis/docs/DEPLOYMENT.md) for complete production setup instructions.

 
