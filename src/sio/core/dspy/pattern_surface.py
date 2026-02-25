"""Pattern surfacing — finds recurring failure patterns for user review (FR-030)."""

from __future__ import annotations

import sqlite3


def surface_patterns(
    conn: sqlite3.Connection,
    skill_name: str | None = None,
    platform: str | None = None,
    min_count: int = 3,
) -> list[dict]:
    """Find recurring failure patterns across sessions.

    Returns patterns where the same skill fails across >= min_count
    distinct sessions. Nothing deploys without user acknowledgment.

    Args:
        conn: Database connection.
        skill_name: Filter to a specific skill.
        platform: Filter to a specific platform.
        min_count: Minimum distinct session count threshold.

    Returns:
        List of dicts with keys: skill_name, description, count,
        affected_sessions.
    """
    query = """
        SELECT
            actual_action as skill_name,
            COUNT(*) as fail_count,
            GROUP_CONCAT(DISTINCT session_id) as sessions
        FROM behavior_invocations
        WHERE (correct_outcome = 0 OR user_satisfied = 0)
    """
    params: list = []
    if skill_name:
        query += " AND actual_action = ?"
        params.append(skill_name)
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    query += " GROUP BY actual_action"

    rows = conn.execute(query, params).fetchall()
    results = []

    for r in rows:
        sessions_str = r["sessions"] or ""
        sessions = [s for s in sessions_str.split(",") if s]

        if len(sessions) < min_count:
            continue

        results.append({
            "skill_name": r["skill_name"],
            "description": (
                f"Skill '{r['skill_name']}' has {r['fail_count']} "
                f"failures across {len(sessions)} sessions"
            ),
            "count": r["fail_count"],
            "affected_sessions": sessions,
        })

    return results
