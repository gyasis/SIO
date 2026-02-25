"""Query layer for SIO behavior_invocations database."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

_INVOCATION_COLS = [
    "session_id", "timestamp", "platform", "user_message", "behavior_type",
    "actual_action", "expected_action", "activated", "correct_action",
    "correct_outcome", "user_satisfied", "user_note", "passive_signal",
    "history_file", "line_start", "line_end", "token_count", "latency_ms",
    "labeled_by", "labeled_at",
]


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def insert_invocation(conn: sqlite3.Connection, record: dict) -> int:
    cols = _INVOCATION_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO behavior_invocations ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cur.lastrowid


def get_invocation_by_id(conn: sqlite3.Connection, id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM behavior_invocations WHERE id = ?", (id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_unlabeled(
    conn: sqlite3.Connection, platform: str, limit: int = 50
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM behavior_invocations WHERE user_satisfied IS NULL AND platform = ? "
        "ORDER BY timestamp DESC LIMIT ?",
        (platform, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_by_skill(
    conn: sqlite3.Connection, skill_name: str, platform: str = None
) -> list[dict]:
    if platform:
        rows = conn.execute(
            "SELECT * FROM behavior_invocations WHERE actual_action = ? AND platform = ?",
            (skill_name, platform),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM behavior_invocations WHERE actual_action = ?",
            (skill_name,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM behavior_invocations WHERE session_id = ? ORDER BY timestamp",
        (session_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_satisfaction(
    conn: sqlite3.Connection,
    id: int,
    satisfied: int,
    note: str | None,
    labeled_by: str,
) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE behavior_invocations SET user_satisfied = ?, user_note = ?, "
        "labeled_by = ?, labeled_at = ? WHERE id = ?",
        (satisfied, note, labeled_by, now, id),
    )
    conn.commit()
    return cur.rowcount > 0


def count_by_platform(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT platform, COUNT(*) as cnt FROM behavior_invocations GROUP BY platform"
    ).fetchall()
    return {row["platform"]: row["cnt"] for row in rows}


def get_skill_health(
    conn: sqlite3.Connection, platform: str = None, skill: str = None
) -> list[dict]:
    query = """
        SELECT
            actual_action as skill_name,
            platform,
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
    query += " GROUP BY actual_action, platform"
    rows = conn.execute(query, params).fetchall()
    results = []
    for r in rows:
        d = _row_to_dict(r)
        sat = d["satisfied_count"]
        unsat = d["unsatisfied_count"]
        d["satisfaction_rate"] = sat / (sat + unsat) if (sat + unsat) > 0 else None
        results.append(d)
    return results


def get_labeled_for_optimizer(
    conn: sqlite3.Connection,
    skill: str,
    platform: str,
    min_examples: int = 10,
) -> list[dict]:
    count_row = conn.execute(
        "SELECT COUNT(*) FROM behavior_invocations "
        "WHERE actual_action = ? AND platform = ? AND user_satisfied IS NOT NULL "
        "AND user_message != '[UNAVAILABLE]'",
        (skill, platform),
    ).fetchone()
    if count_row[0] < min_examples:
        return []
    rows = conn.execute(
        "SELECT * FROM behavior_invocations "
        "WHERE actual_action = ? AND platform = ? AND user_satisfied IS NOT NULL "
        "AND user_message != '[UNAVAILABLE]' "
        "ORDER BY timestamp DESC",
        (skill, platform),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_by_pattern(
    conn: sqlite3.Connection, behavior_type: str, failure_mode: str
) -> int:
    # Map failure_mode strings to SQL conditions
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
