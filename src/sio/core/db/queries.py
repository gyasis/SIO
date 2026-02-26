"""Query layer for SIO behavior_invocations database."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_INVOCATION_COLS = [
    "session_id", "timestamp", "platform", "user_message", "behavior_type",
    "actual_action", "expected_action", "activated", "correct_action",
    "correct_outcome", "user_satisfied", "user_note", "passive_signal",
    "history_file", "line_start", "line_end", "token_count", "latency_ms",
    "labeled_by", "labeled_at",
]


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to dict."""
    return dict(row)


def insert_invocation(
    conn: sqlite3.Connection, record: dict, *, _batch: bool = False,
) -> int:
    cols = _INVOCATION_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO behavior_invocations ({col_names}) VALUES ({placeholders})",
        values,
    )
    if not _batch:
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


def insert_error_record(
    conn: sqlite3.Connection, record: dict, *, _batch: bool = False,
) -> int:
    """Insert an error record. Returns the new row ID."""
    cols = _ERROR_RECORD_COLS
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [record.get(c) for c in cols]
    cur = conn.execute(
        f"INSERT INTO error_records ({col_names}) VALUES ({placeholders})",
        values,
    )
    if not _batch:
        conn.commit()
    return cur.lastrowid


def get_error_records(
    conn: sqlite3.Connection,
    session_id: str = None,
    error_type: str = None,
    tool_name: str = None,
    since: str = None,
    limit: int = 500,
    project: str = None,
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
    if project:
        query += " AND source_file LIKE ?"
        params.append(f"%{project}%")
    query += " ORDER BY timestamp DESC"
    if limit and limit > 0:
        query += " LIMIT ?"
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
        f"INSERT INTO patterns ({col_names}) VALUES ({placeholders}) "
        f"ON CONFLICT(pattern_id) DO UPDATE SET "
        f"description=excluded.description, tool_name=excluded.tool_name, "
        f"error_count=excluded.error_count, session_count=excluded.session_count, "
        f"first_seen=excluded.first_seen, last_seen=excluded.last_seen, "
        f"rank_score=excluded.rank_score, centroid_embedding=excluded.centroid_embedding, "
        f"updated_at=excluded.updated_at",
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


_PATTERN_UPDATE_ALLOWED = frozenset({
    "description", "tool_name", "error_count", "session_count",
    "first_seen", "last_seen", "rank_score", "centroid_embedding",
    "updated_at",
})


def update_pattern(conn: sqlite3.Connection, id: int, **fields) -> bool:
    """Update specific fields on a pattern by numeric id."""
    if not fields:
        return False
    invalid = set(fields.keys()) - _PATTERN_UPDATE_ALLOWED
    if invalid:
        raise ValueError(f"Invalid columns for pattern update: {invalid}")
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
    conn: sqlite3.Connection, pattern_id: int, error_id: int,
    *, _batch: bool = False,
) -> None:
    """Link an error record to a pattern (join table)."""
    conn.execute(
        "INSERT OR IGNORE INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
        (pattern_id, error_id),
    )
    if not _batch:
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
    "pattern_id", "file_path", "positive_count", "negative_count",
    "min_threshold", "lineage_sessions", "created_at", "updated_at",
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


_DATASET_UPDATE_ALLOWED = frozenset({
    "file_path", "positive_count", "negative_count", "min_threshold",
    "lineage_sessions", "updated_at",
})


def update_dataset(conn: sqlite3.Connection, id: int, **fields) -> bool:
    """Update specific fields on a dataset."""
    if not fields:
        return False
    invalid = set(fields.keys()) - _DATASET_UPDATE_ALLOWED
    if invalid:
        raise ValueError(f"Invalid columns for dataset update: {invalid}")
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
    "pattern_id", "dataset_id", "description", "confidence",
    "proposed_change", "target_file", "change_type", "status",
    "ai_explanation", "user_note", "created_at", "reviewed_at",
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
            "UPDATE suggestions SET status = ?, user_note = ?, reviewed_at = ? WHERE id = ?",
            (status, note, now, id),
        )
    else:
        cur = conn.execute(
            "UPDATE suggestions SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, now, id),
        )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# v2 — AppliedChange queries
# ---------------------------------------------------------------------------

_APPLIED_CHANGE_COLS = [
    "suggestion_id", "target_file", "diff_before", "diff_after",
    "commit_sha", "applied_at", "rolled_back_at",
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


# ---------------------------------------------------------------------------
# v3 — Ground Truth queries (DSPy suggestion engine)
# ---------------------------------------------------------------------------


def insert_ground_truth(
    conn: sqlite3.Connection,
    pattern_id: str,
    error_examples_json: str,
    error_type: str,
    pattern_summary: str,
    target_surface: str,
    rule_title: str,
    prevention_instructions: str,
    rationale: str,
    source: str = "agent",
    confidence: float | None = None,
    file_path: str | None = None,
    quality_assessment: str | None = None,
    strict: bool = True,
) -> int:
    """Insert a ground truth candidate.

    Args:
        conn: Database connection.
        pattern_id: Identifier for the error pattern.
        error_examples_json: JSON-encoded array of error examples.
        error_type: Category of error (tool_failure, user_correction, etc.).
        pattern_summary: Description of the recurring error pattern.
        target_surface: Where the fix should be applied.
        rule_title: Concise title for the improvement.
        prevention_instructions: Actionable prevention text in markdown.
        rationale: Why this improvement addresses the pattern.
        source: Origin of the entry (agent, seed, approved, edited, rejected).
        confidence: Optional confidence score.
        file_path: Optional path to the relevant file.
        quality_assessment: Optional self-assessment of candidate quality.
        strict: If True (default), raise ValueError when pattern_id is missing.
            If False, warn and continue (backward compat).

    Returns:
        The new row ID.

    Raises:
        ValueError: If pattern_id does not reference a valid pattern and strict=True.
    """
    # T101/T126: Application-level FK validation for pattern_id
    pat_row = conn.execute(
        "SELECT id FROM patterns WHERE pattern_id = ?", (pattern_id,)
    ).fetchone()
    if pat_row is None:
        if strict:
            raise ValueError(
                f"ground_truth.pattern_id '{pattern_id}' has no matching "
                f"patterns row"
            )
        logger.warning(
            "ground_truth.pattern_id '%s' has no matching patterns row; "
            "inserting anyway (strict=False).",
            pattern_id,
        )

    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO ground_truth "
        "(pattern_id, error_examples_json, error_type, pattern_summary, "
        "target_surface, rule_title, prevention_instructions, rationale, "
        "source, confidence, file_path, quality_assessment, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pattern_id, error_examples_json, error_type, pattern_summary,
            target_surface, rule_title, prevention_instructions, rationale,
            source, confidence, file_path, quality_assessment, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_ground_truth_by_pattern(
    conn: sqlite3.Connection, pattern_id: str
) -> list[dict]:
    """Get all ground truth entries for a pattern.

    Args:
        conn: Database connection.
        pattern_id: The pattern identifier to filter by.

    Returns:
        List of ground truth rows as dicts.
    """
    rows = conn.execute(
        "SELECT * FROM ground_truth WHERE pattern_id = ? ORDER BY created_at DESC",
        (pattern_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_pending_ground_truth(
    conn: sqlite3.Connection, surface_type: str | None = None
) -> list[dict]:
    """Get all pending (unreviewed) ground truth entries.

    Args:
        conn: Database connection.
        surface_type: Optional target_surface filter.

    Returns:
        List of pending ground truth rows as dicts.
    """
    if surface_type:
        rows = conn.execute(
            "SELECT * FROM ground_truth WHERE label = 'pending' AND target_surface = ? "
            "ORDER BY created_at DESC",
            (surface_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ground_truth WHERE label = 'pending' ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_ground_truth_label(
    conn: sqlite3.Connection,
    gt_id: int,
    label: str,
    source: str | None = None,
    user_note: str | None = None,
) -> None:
    """Update label (approve/reject) and optionally source and note.

    Args:
        conn: Database connection.
        gt_id: The ground truth row ID.
        label: New label value (pending, positive, negative).
        source: Optional new source value.
        user_note: Optional user note.
    """
    now = datetime.now(timezone.utc).isoformat()
    if source is not None and user_note is not None:
        conn.execute(
            "UPDATE ground_truth SET label = ?, source = ?, user_note = ?, reviewed_at = ? "
            "WHERE id = ?",
            (label, source, user_note, now, gt_id),
        )
    elif source is not None:
        conn.execute(
            "UPDATE ground_truth SET label = ?, source = ?, reviewed_at = ? WHERE id = ?",
            (label, source, now, gt_id),
        )
    elif user_note is not None:
        conn.execute(
            "UPDATE ground_truth SET label = ?, user_note = ?, reviewed_at = ? WHERE id = ?",
            (label, user_note, now, gt_id),
        )
    else:
        conn.execute(
            "UPDATE ground_truth SET label = ?, reviewed_at = ? WHERE id = ?",
            (label, now, gt_id),
        )
    conn.commit()


def get_training_corpus(conn: sqlite3.Connection) -> list[dict]:
    """Get all positive-labeled ground truth for DSPy training.

    Args:
        conn: Database connection.

    Returns:
        List of positive ground truth rows as dicts.
    """
    rows = conn.execute(
        "SELECT * FROM ground_truth WHERE label = 'positive' ORDER BY created_at"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_ground_truth_stats(conn: sqlite3.Connection) -> dict:
    """Get stats: count by label, count by surface, total.

    Args:
        conn: Database connection.

    Returns:
        Dict with 'total', 'by_label', and 'by_surface' keys.
    """
    total_row = conn.execute("SELECT COUNT(*) FROM ground_truth").fetchone()
    total = total_row[0]

    label_rows = conn.execute(
        "SELECT label, COUNT(*) as cnt FROM ground_truth GROUP BY label"
    ).fetchall()
    by_label = {row["label"]: row["cnt"] for row in label_rows}

    surface_rows = conn.execute(
        "SELECT target_surface, COUNT(*) as cnt FROM ground_truth GROUP BY target_surface"
    ).fetchall()
    by_surface = {row["target_surface"]: row["cnt"] for row in surface_rows}

    return {
        "total": total,
        "by_label": by_label,
        "by_surface": by_surface,
    }
