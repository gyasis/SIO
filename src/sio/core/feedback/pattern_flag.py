"""Pattern flag — marks skills as priority optimization candidates (FR-029)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def flag_pattern(
    conn: sqlite3.Connection,
    skill_name: str,
    note: str,
) -> bool:
    """Flag a skill as a priority optimization candidate.

    Args:
        conn: Database connection.
        skill_name: Name of the skill to flag.
        note: User's description of the recurring issue.

    Returns:
        True on success, False if note is empty.
    """
    if not note or not note.strip():
        return False

    # Ensure pattern_flags table exists
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pattern_flags ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "skill_name TEXT NOT NULL, "
        "note TEXT NOT NULL, "
        "flagged_at TEXT NOT NULL)"
    )

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO pattern_flags (skill_name, note, flagged_at) VALUES (?, ?, ?)",
        (skill_name, note.strip(), now),
    )
    conn.commit()
    return True


def get_flagged_skills(conn: sqlite3.Connection) -> list[dict]:
    """Get all flagged skills with their notes."""
    try:
        rows = conn.execute(
            "SELECT skill_name, note, flagged_at FROM pattern_flags ORDER BY flagged_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
