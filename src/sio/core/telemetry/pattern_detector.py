"""Pattern threshold detector — identifies recurring failure patterns (FR-028)."""

from __future__ import annotations

import sqlite3


def count_pattern_occurrences(
    conn: sqlite3.Connection,
    behavior_type: str,
    failure_mode: str,
) -> int:
    """Count occurrences of a specific failure pattern.

    Args:
        behavior_type: e.g., "skill", "mcp_tool"
        failure_mode: e.g., "incorrect_outcome", "not_activated", "wrong_action"
    """
    mode_map = {
        "incorrect_outcome": "correct_outcome = 0",
        "not_activated": "activated = 0",
        "wrong_action": "correct_action = 0",
    }
    condition = mode_map.get(failure_mode, "correct_outcome = 0")
    row = conn.execute(
        f"SELECT COUNT(*) FROM behavior_invocations WHERE behavior_type = ? AND {condition}",
        (behavior_type,),
    ).fetchone()
    return row[0]


def is_optimization_candidate(
    conn: sqlite3.Connection,
    skill_name: str,
    threshold: int = 3,
) -> bool:
    """Check if a skill has recurring failures across enough distinct sessions.

    FR-028: Only returns True when the same failure pattern recurs across
    >= threshold DISTINCT sessions. A single session with many failures
    does NOT qualify.
    """
    row = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM behavior_invocations "
        "WHERE actual_action = ? AND (correct_outcome = 0 OR user_satisfied = 0)",
        (skill_name,),
    ).fetchone()
    return row[0] >= threshold
