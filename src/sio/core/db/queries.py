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
    """Get labeled invocations for the optimizer.

    Includes records where user_satisfied is set OR labeled_by is set.
    Excludes records with [UNAVAILABLE] user_message.
    """
    where = (
        "WHERE actual_action = ? AND platform = ? "
        "AND (user_satisfied IS NOT NULL OR labeled_by IS NOT NULL) "
        "AND user_message != '[UNAVAILABLE]'"
    )
    count_row = conn.execute(
        f"SELECT COUNT(*) FROM behavior_invocations {where}",
        (skill, platform),
    ).fetchone()
    if count_row[0] < min_examples:
        return []
    rows = conn.execute(
        f"SELECT * FROM behavior_invocations {where} "
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


# ---------------------------------------------------------------------------
# v2 — ErrorRecord queries
# ---------------------------------------------------------------------------

_ERROR_RECORD_COLS = [
    "session_id", "timestamp", "source_type", "source_file", "tool_name",
    "error_text", "user_message", "context_before", "context_after",
    "error_type", "mined_at",
]


def insert_error_record(conn: sqlite3.Connection, record: dict) -> int:
    """Insert an error record. Returns the new row ID."""
    cols = _ERROR_RECORD_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO error_records ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cur.lastrowid


def get_error_records(
    conn: sqlite3.Connection,
    session_id: str = None,
    error_type: str = None,
    tool_name: str = None,
    since: str = None,
    limit: int = 500,
) -> list[dict]:
    """Get error records with optional filters."""
    query = "SELECT * FROM error_records WHERE 1=1"
    params: list = []
    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)
    if error_type:
        query += " AND error_type = ?"
        params.append(error_type)
    if tool_name:
        query += " AND tool_name = ?"
        params.append(tool_name)
    if since:
        query += " AND timestamp >= ?"
        params.append(since)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_error_records(conn: sqlite3.Connection) -> int:
    """Count total error records."""
    row = conn.execute("SELECT COUNT(*) FROM error_records").fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# v2 — Pattern queries
# ---------------------------------------------------------------------------

_PATTERN_COLS = [
    "pattern_id", "description", "tool_name", "error_count", "session_count",
    "first_seen", "last_seen", "rank_score", "centroid_embedding",
    "created_at", "updated_at",
]


def insert_pattern(conn: sqlite3.Connection, record: dict) -> int:
    """Insert a pattern. Returns the new row ID."""
    cols = _PATTERN_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO patterns ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cur.lastrowid


def get_patterns(conn: sqlite3.Connection, min_count: int = 0) -> list[dict]:
    """Get patterns ordered by rank_score DESC, optionally filtered by min error_count."""
    rows = conn.execute(
        "SELECT * FROM patterns WHERE error_count >= ? ORDER BY rank_score DESC",
        (min_count,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_pattern_by_id(conn: sqlite3.Connection, pattern_id: str) -> dict | None:
    """Get a pattern by its human-readable pattern_id slug."""
    row = conn.execute(
        "SELECT * FROM patterns WHERE pattern_id = ?", (pattern_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def update_pattern(conn: sqlite3.Connection, id: int, **fields) -> bool:
    """Update specific fields on a pattern by numeric id."""
    if not fields:
        return False
    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [id]
    cur = conn.execute(
        f"UPDATE patterns SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# v2 — PatternError queries (join table)
# ---------------------------------------------------------------------------


def link_error_to_pattern(
    conn: sqlite3.Connection, pattern_id: int, error_id: int
) -> None:
    """Link an error record to a pattern (join table)."""
    conn.execute(
        "INSERT OR IGNORE INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
        (pattern_id, error_id),
    )
    conn.commit()


def get_errors_for_pattern(
    conn: sqlite3.Connection, pattern_id: int
) -> list[dict]:
    """Get all error records linked to a pattern."""
    rows = conn.execute(
        "SELECT er.* FROM error_records er "
        "JOIN pattern_errors pe ON pe.error_id = er.id "
        "WHERE pe.pattern_id = ? "
        "ORDER BY er.timestamp DESC",
        (pattern_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# v2 — Dataset queries
# ---------------------------------------------------------------------------

_DATASET_COLS = [
    "pattern_id", "train_examples", "val_examples", "test_examples",
    "created_at", "updated_at",
]


def insert_dataset(conn: sqlite3.Connection, record: dict) -> int:
    """Insert a dataset record. Returns the new row ID."""
    cols = _DATASET_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO datasets ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cur.lastrowid


def get_dataset_for_pattern(
    conn: sqlite3.Connection, pattern_id: int
) -> dict | None:
    """Get the dataset for a given pattern."""
    row = conn.execute(
        "SELECT * FROM datasets WHERE pattern_id = ?", (pattern_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def update_dataset(conn: sqlite3.Connection, id: int, **fields) -> bool:
    """Update specific fields on a dataset."""
    if not fields:
        return False
    set_clause = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values()) + [id]
    cur = conn.execute(
        f"UPDATE datasets SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# v2 — Suggestion queries
# ---------------------------------------------------------------------------

_SUGGESTION_COLS = [
    "pattern_id", "suggestion_text", "status", "note",
    "created_at", "updated_at",
]


def insert_suggestion(conn: sqlite3.Connection, record: dict) -> int:
    """Insert a suggestion. Returns the new row ID."""
    cols = _SUGGESTION_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO suggestions ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cur.lastrowid


def get_suggestions(
    conn: sqlite3.Connection, status: str = None
) -> list[dict]:
    """Get suggestions, optionally filtered by status."""
    if status:
        rows = conn.execute(
            "SELECT * FROM suggestions WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM suggestions ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_suggestion_status(
    conn: sqlite3.Connection, id: int, status: str, note: str = None
) -> bool:
    """Update suggestion status and optionally add a note."""
    now = datetime.now(timezone.utc).isoformat()
    if note is not None:
        cur = conn.execute(
            "UPDATE suggestions SET status = ?, note = ?, updated_at = ? WHERE id = ?",
            (status, note, now, id),
        )
    else:
        cur = conn.execute(
            "UPDATE suggestions SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, id),
        )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# v2 — AppliedChange queries
# ---------------------------------------------------------------------------

_APPLIED_CHANGE_COLS = [
    "suggestion_id", "change_type", "target_file", "diff_text",
    "applied_at", "rolled_back_at",
]


def insert_applied_change(conn: sqlite3.Connection, record: dict) -> int:
    """Insert an applied change record. Returns the new row ID."""
    cols = _APPLIED_CHANGE_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO applied_changes ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cur.lastrowid


def get_applied_change(conn: sqlite3.Connection, id: int) -> dict | None:
    """Get an applied change by ID."""
    row = conn.execute(
        "SELECT * FROM applied_changes WHERE id = ?", (id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def mark_rolled_back(
    conn: sqlite3.Connection, id: int, rolled_back_at: str
) -> bool:
    """Mark an applied change as rolled back."""
    cur = conn.execute(
        "UPDATE applied_changes SET rolled_back_at = ? WHERE id = ?",
        (rolled_back_at, id),
    )
    conn.commit()
    return cur.rowcount > 0
