"""Health aggregator — per-skill performance metrics."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class SkillHealth:
    platform: str
    skill_name: str
    total_invocations: int
    satisfied_count: int
    unsatisfied_count: int
    unlabeled_count: int
    false_trigger_count: int
    missed_trigger_count: int
    satisfaction_rate: float | None
    flagged: bool = False


def compute_health(
    conn: sqlite3.Connection,
    platform: str | None = None,
    skill: str | None = None,
) -> list[SkillHealth]:
    """Compute per-skill health metrics.

    Args:
        conn: Database connection.
        platform: Optional platform filter.
        skill: Optional skill filter.

    Returns:
        List of SkillHealth records.
    """
    query = """
        SELECT
            platform,
            actual_action as skill_name,
            COUNT(*) as total_invocations,
            SUM(CASE WHEN user_satisfied = 1 THEN 1 ELSE 0 END) as satisfied_count,
            SUM(CASE WHEN user_satisfied = 0 THEN 1 ELSE 0 END) as unsatisfied_count,
            SUM(CASE WHEN user_satisfied IS NULL THEN 1 ELSE 0 END) as unlabeled_count,
            SUM(CASE WHEN activated = 1 AND correct_action = 0
                THEN 1 ELSE 0 END) as false_trigger_count,
            SUM(CASE WHEN activated = 0 THEN 1 ELSE 0 END) as missed_trigger_count
        FROM behavior_invocations
        WHERE 1=1
    """
    params = []
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if skill:
        query += " AND actual_action = ?"
        params.append(skill)
    query += " GROUP BY platform, actual_action"

    rows = conn.execute(query, params).fetchall()
    results = []
    for r in rows:
        sat = r["satisfied_count"]
        unsat = r["unsatisfied_count"]
        total_labeled = sat + unsat
        rate = sat / total_labeled if total_labeled > 0 else None
        flagged = rate is not None and rate < 0.5

        results.append(SkillHealth(
            platform=r["platform"],
            skill_name=r["skill_name"],
            total_invocations=r["total_invocations"],
            satisfied_count=sat,
            unsatisfied_count=unsat,
            unlabeled_count=r["unlabeled_count"],
            false_trigger_count=r["false_trigger_count"],
            missed_trigger_count=r["missed_trigger_count"],
            satisfaction_rate=rate,
            flagged=flagged,
        ))

    return results
