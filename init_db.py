"""Vectis database bootstrapper and default org seeder."""

import os
import config

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

DEFAULT_AGENTS = [
    {
        "id": "ceo",
        "name": "Chief Executive",
        "role": "CEO",
        "system_prompt": (
            "You are the Chief Executive Officer of the Vectis organization. "
            "Your primary role is strategic oversight and task delegation. "
            "When you receive a high-level objective: "
            "1) Analyze the objective and break it into concrete, actionable sub-tasks. "
            "2) Delegate each sub-task to the most appropriate team member. "
            "3) Provide clear, detailed descriptions for each delegated task. "
            "4) If work has been submitted by your team for review, evaluate the results "
            "and either approve (DONE) or provide specific feedback for revision. "
            "Do not implement technical work yourself - you orchestrate and coordinate."
        ),
        "model_name": "gemini/gemini-2.5-flash",
        "manager_id": None,
        "budget_limit": 5.00,
    },
    {
        "id": "dev",
        "name": "Senior Developer",
        "role": "Developer",
        "system_prompt": (
            "You are a Senior Developer on the Vectis team. "
            "Your role is to implement technical solutions, write clean code, "
            "and create technical specifications. "
            "When given a task: "
            "1) Analyze the requirements carefully. "
            "2) Produce complete, working code with clear explanations. "
            "3) Include any necessary configuration or setup instructions. "
            "4) If the task is too large, break it down and delegate sub-components. "
            "5) Submit your work for review when complete."
        ),
        "model_name": "groq/llama-3.3-70b",
        "manager_id": "ceo",
        "budget_limit": 5.00,
    },
    {
        "id": "copywriter",
        "name": "Technical Copywriter",
        "role": "Copywriter",
        "system_prompt": (
            "You are a Technical Copywriter on the Vectis team. "
            "Your role is to create clear, professional documentation, "
            "marketing materials, and user-facing content. "
            "When given a task: "
            "1) Understand the target audience and purpose. "
            "2) Produce polished, well-structured content. "
            "3) Ensure accuracy and consistency. "
            "4) Submit your work for review when complete."
        ),
        "model_name": "anthropic/claude-3-5-haiku",
        "manager_id": "ceo",
        "budget_limit": 5.00,
    },
    {
        "id": "reviewer",
        "name": "QA Reviewer",
        "role": "Reviewer",
        "system_prompt": (
            "You are the Quality Assurance Reviewer on the Vectis team. "
            "Your role is to review work submitted by team members. "
            "When given a task to review: "
            "1) Carefully examine the submitted work for correctness, completeness, and quality. "
            "2) Check for errors, inconsistencies, or missing requirements. "
            "3) If the work meets standards, approve it (DONE). "
            "4) If issues are found, provide specific, actionable feedback. "
            "5) Be thorough but constructive."
        ),
        "model_name": "gemini/gemini-2.5-flash",
        "manager_id": "ceo",
        "budget_limit": 5.00,
    },
]


def init_database():
    """Create the SQLite database and seed default agents."""
    conn = config.get_db_connection()

    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    for agent in DEFAULT_AGENTS:
        conn.execute(
            """INSERT OR IGNORE INTO agents
               (id, name, role, system_prompt, model_name, manager_id, budget_limit)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                agent["id"],
                agent["name"],
                agent["role"],
                agent["system_prompt"],
                agent["model_name"],
                agent["manager_id"],
                agent["budget_limit"],
            ),
        )

    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    conn.close()

    print(f"Database initialized: {config.DB_PATH}")
    print(f"Seeded {count} default agents: {', '.join(a['id'] for a in DEFAULT_AGENTS)}")
    print()
    print("Next steps:")
    print("  1. Set API key: export GEMINI_API_KEY=... (or OPENAI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY)")
    print("  2. Seed a task: python main.py seed \"Your task title\" --desc \"Description\"")
    print("  3. Start daemon: python main.py run")


if __name__ == "__main__":
    init_database()
 
