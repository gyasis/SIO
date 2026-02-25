"""Gold standards manager — promotes verified invocations for regression testing."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def promote_to_gold(conn: sqlite3.Connection, invocation_id: int) -> int | None:
    """Promote an invocation to gold standard.

    Copies key fields from behavior_invocations into gold_standards.

    Returns:
        The gold standard ID, or None if invocation not found.
    """
    row = conn.execute(
        "SELECT * FROM behavior_invocations WHERE id = ?",
        (invocation_id,),
    ).fetchone()

    if row is None:
        return None

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO gold_standards "
        "(invocation_id, skill_name, platform, user_message, "
        "expected_action, expected_outcome, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            invocation_id,
            row["actual_action"],
            row["platform"],
            row["user_message"],
            row["expected_action"] or row["actual_action"],
            str(row["correct_outcome"]) if row["correct_outcome"] is not None else None,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_all_for_skill(
    conn: sqlite3.Connection, skill_name: str,
) -> list[dict]:
    """Get all gold standards for a skill."""
    rows = conn.execute(
        "SELECT * FROM gold_standards WHERE skill_name = ?",
        (skill_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def replay_against_prompt(gold: dict, new_prompt: str) -> bool:
    """Check if a gold standard still passes with a new prompt.

    V0.1: Simple heuristic — checks if the gold's user_message
    shares significant terms with the new prompt.

    Returns:
        True if the gold standard is likely still satisfied.
    """
    gold_msg = gold.get("user_message", "").lower()
    new_lower = new_prompt.lower()

    gold_terms = set(gold_msg.split())
    new_terms = set(new_lower.split())

    if not gold_terms:
        return True

    overlap = gold_terms & new_terms
    similarity = len(overlap) / max(len(gold_terms), 1)
    return similarity >= 0.3
