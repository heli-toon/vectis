# Vectis

Ultra-lightweight multi-agent orchestration system with hierarchical delegation, budget controls, and crash-resilient state management.

## Quick Start

```bash
# 1. Install dependencies
pip3 install -r requirements.txt

# 2. Initialize the database (creates 4 default agents)
python3 main.py init

# 3. Set at least one LLM API key
export GEMINI_API_KEY=your_key_here
# (or OPENAI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY)

# 4. Seed a task
python3 main.py seed "Write a welcome email" --desc "Create a welcome email for new SaaS users" --assign ceo

# 5. Run the heartbeat daemon
python3 main.py run
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `python3 main.py init` | Initialize database with default agents |
| `python3 main.py run` | Start the heartbeat daemon (polls every 30s) |
| `python3 main.py run --once` | Run a single heartbeat tick and exit |
| `python3 main.py run --interval 10` | Run with custom poll interval (seconds) |
| `python3 main.py seed "Title" --desc "..." --assign ceo` | Seed a root ticket |
| `python3 main.py status` | Show system overview (agents, ticket counts, recent activity) |
| `python3 main.py agents` | List all agents with details |
| `python3 main.py tickets` | List all tickets with results |
| `python3 main.py tree` | Show ticket hierarchy tree |
| `python3 main.py tree 1` | Show tree for a specific ticket |

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
OPEN → IN_PROGRESS → DONE
                   → REVIEW (delegated, waiting on sub-tasks)
                   → FAILED
                   → PAUSED_BUDGET
```

When all sub-tickets of a REVIEW ticket reach DONE/FAILED, the parent ticket is automatically re-opened for the manager to review results.

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
| `VECTIS_DB_PATH` | `paperclip_mini.db` | SQLite database path |
| `VECTIS_POLL_INTERVAL` | `30` | Heartbeat poll interval (seconds) |
| `VECTIS_MAX_CONCURRENT` | `1` | Max tickets processed per heartbeat |
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
 
