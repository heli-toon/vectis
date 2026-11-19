"""Pytest fixtures for Vectis test suite using in-memory SQLite databases."""

import os
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_TELEMETRY"] = "False"

import sys
import sqlite3
import pytest
from unittest.mock import MagicMock

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")

DEFAULT_TEST_AGENTS = [
    {
        "id": "ceo",
        "name": "Chief Executive",
        "role": "CEO",
        "system_prompt": "You are the CEO. Delegate tasks to your team.",
        "model_name": "gemini/gemini-2.5-flash",
        "manager_id": None,
        "budget_limit": 10.00,
    },
    {
        "id": "dev",
        "name": "Senior Developer",
        "role": "Developer",
        "system_prompt": "You are a Senior Developer. Implement code.",
        "model_name": "groq/llama-3.3-70b",
        "manager_id": "ceo",
        "budget_limit": 5.00,
    },
    {
        "id": "copywriter",
        "name": "Technical Copywriter",
        "role": "Copywriter",
        "system_prompt": "You are a Technical Copywriter. Write documentation.",
        "model_name": "anthropic/claude-3-5-haiku",
        "manager_id": "ceo",
        "budget_limit": 5.00,
    },
    {
        "id": "reviewer",
        "name": "QA Reviewer",
        "role": "Reviewer",
        "system_prompt": "You are the QA Reviewer. Review deliverables.",
        "model_name": "gemini/gemini-2.5-flash",
        "manager_id": "ceo",
        "budget_limit": 5.00,
    },
]


@pytest.fixture
def mem_db():
    """Create an isolated in-memory SQLite database populated with schema and agents."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    for agent in DEFAULT_TEST_AGENTS:
        conn.execute(
            """INSERT INTO agents (id, name, role, system_prompt, model_name, manager_id, budget_limit, allowed_tools)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent["id"],
                agent["name"],
                agent["role"],
                agent["system_prompt"],
                agent["model_name"],
                agent["manager_id"],
                agent["budget_limit"],
                agent.get("allowed_tools", "*"),
            ),
        )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def make_mock_response():
    """Factory fixture for mocked LiteLLM response objects."""
    def _create(content, prompt_tokens=100, completion_tokens=50):
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = content
        mock_resp.choices = [mock_choice]
        mock_resp.usage.prompt_tokens = prompt_tokens
        mock_resp.usage.completion_tokens = completion_tokens
        return mock_resp
    return _create
