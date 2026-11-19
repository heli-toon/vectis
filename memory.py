"""Vectis zero-dependency institutional memory and knowledge retrieval engine.

Leverages SQLite's native FTS5 full-text search with Porter stemming (BM25 ranking)
for zero-RAM RAG and cross-ticket knowledge retention.
"""

import re
import sqlite3
from typing import Any, Dict, List, Optional


def sanitize_fts5_query(query: str) -> str:
    """Sanitize and tokenize free-form text into safe SQLite FTS5 query syntax."""
    if not isinstance(query, str) or not query.strip():
        return ""

    tokens = re.findall(r"[a-zA-Z0-9_]+", query)
    if not tokens:
        return ""

    # Filter out single character tokens that add noise
    filtered = [t for t in tokens if len(t) > 1] or tokens
    # Prefix match with OR union
    return " OR ".join(f'"{t}"*' for t in filtered[:10])


def record_memory(
    conn: sqlite3.Connection,
    title: str,
    content: str,
    tags: str = "",
    agent_id: Optional[str] = None,
    ticket_id: Optional[int] = None,
) -> int:
    """Store a distilled piece of institutional knowledge into SQLite memories table."""
    if not title or not isinstance(title, str) or not title.strip():
        raise ValueError("Memory title must be a non-empty string.")
    if not content or not isinstance(content, str) or not content.strip():
        raise ValueError("Memory content must be a non-empty string.")

    cursor = conn.execute(
        """INSERT INTO memories (title, content, tags, agent_id, ticket_id)
           VALUES (?, ?, ?, ?, ?)""",
        (title.strip(), content.strip(), (tags or "").strip(), agent_id, ticket_id),
    )
    conn.commit()
    return cursor.lastrowid


def search_memories(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Search institutional memory using SQLite FTS5 BM25 relevance ranking."""
    sanitized = sanitize_fts5_query(query)
    if not sanitized:
        return []

    try:
        rows = conn.execute(
            """SELECT m.id, m.title, m.content, m.tags, m.agent_id, m.ticket_id,
                      m.created_at, fts.rank
               FROM memories_fts fts
               JOIN memories m ON fts.rowid = m.id
               WHERE memories_fts MATCH ?
               ORDER BY fts.rank ASC
               LIMIT ?""",
            (sanitized, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # Fallback to simple LIKE search if FTS table query encounters issue
        escaped = query.strip()[:50]
        rows = conn.execute(
            """SELECT id, title, content, tags, agent_id, ticket_id, created_at, 0 as rank
               FROM memories
               WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?
               ORDER BY id DESC LIMIT ?""",
            (f"%{escaped}%", f"%{escaped}%", f"%{escaped}%", max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]


def list_memories(
    conn: sqlite3.Connection,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List recent memories ordered by newest first."""
    rows = conn.execute(
        """SELECT id, title, content, tags, agent_id, ticket_id, created_at
           FROM memories
           ORDER BY id DESC
           LIMIT ?""",
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_memory(conn: sqlite3.Connection, memory_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve a single memory by ID."""
    row = conn.execute(
        """SELECT id, title, content, tags, agent_id, ticket_id, created_at
           FROM memories
           WHERE id = ?""",
        (memory_id,),
    ).fetchone()
    return dict(row) if row else None
