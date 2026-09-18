"""Vectis system configuration."""

import os
import sqlite3

# Database
DB_PATH = os.environ.get("VECTIS_DB_PATH", "vectis.db")

# Heartbeat
POLL_INTERVAL_SECONDS = int(os.environ.get("VECTIS_POLL_INTERVAL", "30"))
MAX_CONCURRENT_TASKS = int(os.environ.get("VECTIS_MAX_CONCURRENT", "1"))

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


def get_db_connection():
    """Return a SQLite connection with foreign keys enabled and Row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def calculate_cost(model_name, prompt_tokens, completion_tokens):
    """Calculate estimated cost from token usage and model rate."""
    input_rate, output_rate = MODEL_COSTS.get(model_name, DEFAULT_COST)
    return (prompt_tokens / 1_000_000 * input_rate) + (completion_tokens / 1_000_000 * output_rate)

