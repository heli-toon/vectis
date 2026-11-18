"""Vectis system configuration."""

import os
import sqlite3
import threading

# LLM Environment Optimizations
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_TELEMETRY", "False")

# Database
DB_PATH = os.environ.get("VECTIS_DB_PATH", "vectis.db")
DB_TIMEOUT = float(os.environ.get("VECTIS_DB_TIMEOUT", "30.0"))

# Workspaces & Tool Execution
WORKSPACES_DIR = os.environ.get("VECTIS_WORKSPACES_DIR", "workspaces")
DEFAULT_COMMAND_TIMEOUT = int(os.environ.get("VECTIS_COMMAND_TIMEOUT", "30"))
MAX_COMMAND_OUTPUT_BYTES = int(os.environ.get("VECTIS_MAX_OUTPUT_BYTES", "10240"))
MAX_FILE_READ_BYTES = int(os.environ.get("VECTIS_MAX_READ_BYTES", "102400"))

# Heartbeat & Concurrency
POLL_INTERVAL_SECONDS = int(os.environ.get("VECTIS_POLL_INTERVAL", "30"))
MAX_WORKERS = int(os.environ.get("VECTIS_MAX_WORKERS", os.environ.get("VECTIS_MAX_CONCURRENT", "4")))
MAX_CONCURRENT_TASKS = MAX_WORKERS

# LLM
TEMPERATURE = float(os.environ.get("VECTIS_TEMPERATURE", "0.3"))
MAX_TOKENS = int(os.environ.get("VECTIS_MAX_TOKENS", "4096"))
MAX_RETRIES = int(os.environ.get("VECTIS_MAX_RETRIES", "3"))

# Cost per 1M tokens (input, output) in USD
MODEL_COSTS = {
    "gemini/gemini-2.5-flash": (0.075, 0.30),
    "gemini/gemini-2.0-flash": (0.10, 0.40),
    "groq/llama-3.3-70b": (0.59, 0.79),
    "anthropic/claude-3-5-haiku": (0.80, 4.00),
    "anthropic/claude-3-5-sonnet": (3.00, 15.00),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "openai/gpt-4.1": (2.00, 8.00),
}
DEFAULT_COST = (1.00, 2.00)


_thread_local = threading.local()


def get_db_connection(db_path=None, timeout=None):
    """Return a SQLite connection with foreign keys and WAL mode enabled and Row factory."""
    target_path = db_path or DB_PATH
    db_dir = os.path.dirname(target_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(target_path, timeout=timeout or DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_thread_connection(db_path=None):
    """Return a thread-local SQLite connection for the current thread."""
    target_path = db_path or DB_PATH
    conns = getattr(_thread_local, "connections", None)
    if conns is None:
        conns = {}
        _thread_local.connections = conns
    if target_path not in conns:
        conns[target_path] = get_db_connection(target_path)
    return conns[target_path]


def close_thread_connections():
    """Close all SQLite connections opened by the current thread."""
    conns = getattr(_thread_local, "connections", None)
    if conns:
        for conn in list(conns.values()):
            try:
                conn.close()
            except Exception:
                pass
        conns.clear()


def calculate_cost(model_name, prompt_tokens, completion_tokens):
    """Calculate estimated cost from token usage and model rate."""
    input_rate, output_rate = MODEL_COSTS.get(model_name, DEFAULT_COST)
    return (prompt_tokens / 1_000_000 * input_rate) + (completion_tokens / 1_000_000 * output_rate)

