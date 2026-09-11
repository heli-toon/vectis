# End-User Journey & Feature Architecture Plan

## Executive Overview
This document outlines the end-to-end user journey, feature mechanics, operational flows, and interface interaction models for **vectis**, a ultra-lightweight (~35 MB RAM) multi-agent organizational framework designed to run continuously on low-resource environments (such as a 1GB RAM VPS) while maintaining complete functional parity with full-scale multi-agent management platforms.

---

## 1. Persona Profiles

### 1.1 Developer / Systems Admin ("Alex")
* **Goal:** Deploy an autonomous background multi-agent system on a $5/month single-vCPU 1GB RAM VPS without incurring Out-Of-Memory (OOM) crashes.
* **Pain Points:** Standard agent frameworks (CrewAI, AutoGen) consume 400MB–800MB RAM at idle, crash unexpectedly due to memory leaks, and hide internal orchestration state behind complex OOP abstractions.
* **Needs:** Minimal dependencies (`litellm`, `sqlite3`), simple SQLite schema inspection, transparent execution logs, zero background daemon bloat.

### 1.2 Tech Lead / Product Lead ("Jordan")
* **Goal:** Assign high-level strategic tasks (e.g., "Conduct market research and create a launch campaign") and let specialized agents self-organize, delegate sub-tasks, execute work, and report results.
* **Pain Points:** Lack of cost visibility, runaway API bills, infinite loop recursion between agents, non-deterministic agent outputs.
* **Needs:** Rigid spending caps per agent, explicit status transition tracking, structured JSON schema outputs, full task audit trails.

---

## 2. End-to-End User Journey Map

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     USER JOURNEY LIFECYCLE                                      │
├──────────────────┬──────────────────┬──────────────────┬──────────────────┬──────────────────────┤
│ 1. INITIALIZATION│ 2. BOOTSTRAPPING │ 3. TASK SEEDING  │ 4. ORG EXECUTION │ 5. MONITOR & AUDIT   │
│                  │                  │                  │    (HEARTBEAT)   │                      │
│ • Run DB script  │ • Load Org Chart │ • User creates   │ • Scheduler wakes│ • Inspect SQLite DB  │
│ • Define SQLite  │   (CEO, Dev,     │   Root Ticket    │   assigned agent │ • Track sub-tickets  │
│   tables         │   Marketer, QA)  │ • Assign to CEO  │ • Agent executes │ • Review budgets &   │
│ • Configure      │ • Set budgets &  │ • Status set to  │   & delegates    │   completion summary │
│   LiteLLM models │   system prompts │   'OPEN'         │ • DB auto-updates│                      │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┴──────────────────────┘
```

### Stage 1: Setup & Environment Initialization
1. **Repository Clone & Install:** User clones the repository and installs minimal dependencies via `pip install litellm`.
2. **Configuration:** User sets environment variables (`OPENAI_API_KEY`, `GEMINI_API_KEY`, or `GROQ_API_KEY`) and defines `DB_PATH = "paperclip_mini.db"`.
3. **Database Initialization:** User executes `python init_db.py`. The script provisions the lightweight SQLite database with zero-bloat indexing.

### Stage 2: Organizational Hierarchy Setup
1. **Agent Provisioning:** User inserts agent profiles into the `agents` table. Each agent is given:
   - Unique ID (`ceo`, `dev`, `copywriter`, `reviewer`).
   - Defined Role & System Instructions (Memento Prompt).
   - Hard Spending Budget Limit ($5.00 limit).
   - Direct Manager Link (`manager_id`).
2. **Model Selection:** Each agent can be mapped to different LiteLLM-supported models (e.g., CEO on `gemini/gemini-2.5-flash`, Dev on `groq/llama-3.3-70b`, Copywriter on `anthropic/claude-3-5-haiku`).

### Stage 3: Task Seeding (Root Directive)
1. **Directive Creation:** User submits a top-level ticket via CLI command or direct SQL injection into `tickets` table:
   - `title`: "Build and document a Python REST API wrapper."
   - `assigned_to`: `ceo`
   - `created_by`: `human_user`
   - `status`: `OPEN`

### Stage 4: Continuous Autonomous Execution (The Heartbeat Loop)
1. **Heartbeat Trigger:** The lightweight daemon process (`main.py`) wakes up every N seconds (default: 30s) consuming zero CPU during idle sleep.
2. **Agent Polling:** The heartbeat queries SQLite for agents with `OPEN` tickets where `current_spend < budget_limit`.
3. **Memento Context Injection:** System constructs a lean contextual prompt containing:
   - Agent role and system guidelines.
   - Manager/subordinate relationship metadata.
   - Assigned ticket title, description, and execution history.
4. **LiteLLM Completion:** Call is issued via LiteLLM using strict JSON response schema enforcing output structure.
5. **Recursive Task Delegation:**
   - The CEO agent analyzes the root ticket, writes a strategic overview, and emits JSON declaring two sub-tickets:
     - Sub-ticket A -> Assigned to `dev`: "Write Flask endpoints."
     - Sub-ticket B -> Assigned to `copywriter`: "Draft API documentation."
   - The database atomically inserts these sub-tickets with `assigned_to` links.
   - The original root ticket updates status to `IN_PROGRESS` or `REVIEW`.
6. **Worker Execution:** Subsequent heartbeats wake up `dev` and `copywriter` to execute their respective tasks, produce code/markdown artifacts, and update ticket states to `DONE`.

### Stage 5: System Audit & Outcome Retrieval
1. **Completion Check:** User queries SQLite database or CLI summary tool.
2. **Audit Trail:** User inspects full tree of parent-child ticket dependencies, budget consumption per agent, and aggregated deliverables in the `result` fields.

---

## 3. Core Feature Specification

### Feature F-1: Zero-Overhead Heartbeat Scheduler
* **Description:** A non-blocking periodic loop that replaces heavy event buses and continuous holding threads.
* **Behavior:**
  - Wakes up on configurable intervals (default: 30s).
  - Queries indexed SQLite tables for actionable work items.
  - Releases all resources and sleeps immediately if no tickets are open.
* **Resource Profile:** < 35 MB RAM total process footprint, < 0.1% CPU utilization during idle.

### Feature F-2: Memento Context Injector
* **Description:** Dynamically reconstructs an agent's memory and organizational position at runtime without maintaining persistent in-memory state objects.
* **Key Mechanics:**
  - Pulls agent guidelines, manager details, budget status, and active ticket data.
  - Assembles a clean, concise prompt structure minimizing input token overhead.
  - Enforces system boundaries to prevent prompt injection and task hallucination.

### Feature F-3: LiteLLM Multi-Provider Abstraction
* **Description:** Unified transport abstraction layer allowing agents to seamlessly route requests to 100+ LLM providers.
* **Supported Routing:** OpenAI, Anthropic, Google Gemini, Groq, Mistral, Ollama (Local), Bedrock.
* **Benefits:** Zero framework lock-in, dynamic fallback handling, unified token usage tracking.

### Feature F-4: JSON Schema Enforced Sub-Task Spawning
* **Description:** Enforces structured output formatting (`response_format={"type": "json_object"}`) on every completion call.
* **Capabilities:**
  - Agents mark current ticket as `DONE`, `REVIEW`, or `BLOCKED`.
  - Agents atomically spawn child tasks assigned to other specific agents in the org chart.
  - Prevents unstructured free-form text drift.

### Feature F-5: Financial Guardrails & Budget Tracking
* **Description:** Prevents runaway agent loops and API cost blowouts through hard cap enforcement.
* **Mechanics:**
  - SQLite maintains `budget_limit` and `current_spend` per agent.
  - Cost calculated per execution based on token usage reported by LiteLLM response metadata.
  - If `current_spend >= budget_limit`, heartbeat automatically skips the agent and marks ticket as `PAUSED_BUDGET_EXCEEDED`.

---

## 4. Operational & Resource Requirements

| Metric | Heavy Framework (CrewAI/AutoGen) | Vectis Target |
| :--- | :--- | :--- |
| **Idle RAM Footprint** | 350 MB – 850 MB | **< 35 MB** |
| **Active Execution RAM** | 500 MB – 1.2 GB | **< 60 MB** |
| **Min. Recommended Hardware** | 2 vCPU / 2GB – 4GB RAM | **1 vCPU / 512MB – 1GB RAM** |
| **Third-Party Dependencies** | 40+ packages (Pydantic, LangChain, etc.) | **2 packages (`litellm`, standard library)** |
| **Storage Engine** | External Vector DB / Redis / Heavy ORM | **Native SQLite (`sqlite3`)** |
