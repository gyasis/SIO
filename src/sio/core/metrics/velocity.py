"""Learning velocity tracking — FR-014, FR-015, FR-016.

Computes error frequency per type over rolling windows, measures correction
decay rate after rule application, and reports adaptation speed (sessions
until error rate drops below threshold).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


def compute_velocity_snapshot(
    db: sqlite3.Connection,
    error_type: str,
    window_days: int = 7,
) -> dict:
    """Compute a velocity snapshot for a given error type.

    Queries error_records for errors of the given type within a rolling
    window, computes error rate, checks if any suggestion targeting this
    error type has been applied, and calculates correction_decay_rate and
    adaptation_speed when applicable.

    The result is persisted into the velocity_snapshots table.

    Args:
        db: Open sqlite3.Connection with SIO schema.
        error_type: The error_type value to track (e.g. "unused_import").
        window_days: Rolling window size in days (default 7).

    Returns:
        Dict with keys: error_type, error_rate, error_count_in_window,
        correction_decay_rate, adaptation_speed, rule_applied,
        rule_suggestion_id, window_start, window_end, created_at.
    """
    now = datetime.now(timezone.utc)
    window_end = now.isoformat()
    window_start = (now - timedelta(days=window_days)).isoformat()

    # Count errors of this type in the window
    row = db.execute(
        "SELECT COUNT(*) FROM error_records "
        "WHERE error_type = ? AND timestamp >= ? AND timestamp <= ?",
        (error_type, window_start, window_end),
    ).fetchone()
    error_count_in_window = row[0]

    # Count total errors in the window (all types)
    total_row = db.execute(
        "SELECT COUNT(*) FROM error_records WHERE timestamp >= ? AND timestamp <= ?",
        (window_start, window_end),
    ).fetchone()
    total_errors_in_window = total_row[0]

    # Compute error rate
    if total_errors_in_window > 0:
        error_rate = error_count_in_window / total_errors_in_window
    else:
        error_rate = 0.0

    # Check if any suggestion targeting this error type has been applied.
    # We join suggestions with applied_changes, and look for suggestions
    # whose description or proposed_change references this error_type,
    # or whose linked pattern's error records include this error_type.
    applied_row = db.execute(
        "SELECT s.id, ac.applied_at FROM suggestions s "
        "JOIN applied_changes ac ON ac.suggestion_id = s.id "
        "WHERE ac.rolled_back_at IS NULL "
        "AND (s.description LIKE ? OR s.proposed_change LIKE ?) "
        "ORDER BY ac.applied_at DESC LIMIT 1",
        (f"%{error_type}%", f"%{error_type}%"),
    ).fetchone()

    rule_applied = applied_row is not None
    rule_suggestion_id = applied_row[0] if applied_row else None
    applied_at = applied_row[1] if applied_row else None

    # Compute correction_decay_rate and adaptation_speed
    correction_decay_rate: float | None = None
    adaptation_speed: int | None = None

    if rule_applied and applied_at:
        # Pre-rule error rate: count errors of this type in a window of
        # the same size BEFORE the rule was applied
        pre_window_end = applied_at
        pre_window_start = (
            datetime.fromisoformat(applied_at) - timedelta(days=window_days)
        ).isoformat()

        pre_type_row = db.execute(
            "SELECT COUNT(*) FROM error_records "
            "WHERE error_type = ? AND timestamp >= ? AND timestamp <= ?",
            (error_type, pre_window_start, pre_window_end),
        ).fetchone()
        pre_type_count = pre_type_row[0]

        pre_total_row = db.execute(
            "SELECT COUNT(*) FROM error_records WHERE timestamp >= ? AND timestamp <= ?",
            (pre_window_start, pre_window_end),
        ).fetchone()
        pre_total_count = pre_total_row[0]

        pre_rate = pre_type_count / pre_total_count if pre_total_count > 0 else 0.0

        # correction_decay_rate = (pre_rate - post_rate) / pre_rate
        if pre_rate > 0:
            correction_decay_rate = (pre_rate - error_rate) / pre_rate
        else:
            correction_decay_rate = 0.0

        # adaptation_speed: count distinct sessions between rule application
        # and when the error rate dropped below a threshold.
        # We define the threshold as 50% of the pre-application rate.
        threshold = pre_rate * 0.5 if pre_rate > 0 else 0.0

        # Get distinct sessions after the rule was applied, ordered by time
        session_rows = db.execute(
            "SELECT DISTINCT session_id FROM error_records WHERE timestamp > ? ORDER BY timestamp",
            (applied_at,),
        ).fetchall()

        if threshold > 0 and session_rows:
            sessions_checked = 0
            cumulative_type_count = 0
            cumulative_total_count = 0

            for srow in session_rows:
                sid = srow[0]
                sessions_checked += 1

                # Count errors in this session
                s_type = db.execute(
                    "SELECT COUNT(*) FROM error_records WHERE session_id = ? AND error_type = ?",
                    (sid, error_type),
                ).fetchone()[0]
                s_total = db.execute(
                    "SELECT COUNT(*) FROM error_records WHERE session_id = ?",
                    (sid,),
                ).fetchone()[0]

                cumulative_type_count += s_type
                cumulative_total_count += s_total

                running_rate = (
                    cumulative_type_count / cumulative_total_count
                    if cumulative_total_count > 0
                    else 0.0
                )

                if running_rate <= threshold:
                    adaptation_speed = sessions_checked
                    break

            # If we never dropped below threshold, report total sessions checked
            if adaptation_speed is None:
                adaptation_speed = sessions_checked

    # Get session_id for this snapshot (most recent session in window)
    latest_session_row = db.execute(
        "SELECT session_id FROM error_records "
        "WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (window_start, window_end),
    ).fetchone()
    session_id = latest_session_row[0] if latest_session_row else "no-session"

    created_at = now.isoformat()

    # Persist to velocity_snapshots
    db.execute(
        "INSERT INTO velocity_snapshots "
        "(error_type, session_id, error_rate, error_count_in_window, "
        "window_start, window_end, rule_applied, rule_suggestion_id, "
        "created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            error_type,
            session_id,
            error_rate,
            error_count_in_window,
            window_start,
            window_end,
            1 if rule_applied else 0,
            rule_suggestion_id,
            created_at,
        ),
    )
    db.commit()

    return {
        "error_type": error_type,
        "error_rate": error_rate,
        "error_count_in_window": error_count_in_window,
        "correction_decay_rate": correction_decay_rate,
        "adaptation_speed": adaptation_speed,
        "rule_applied": rule_applied,
        "rule_suggestion_id": rule_suggestion_id,
        "window_start": window_start,
        "window_end": window_end,
        "created_at": created_at,
    }


def get_velocity_trends(
    db: sqlite3.Connection,
    error_type: str | None = None,
) -> list[dict]:
    """Retrieve velocity snapshots ordered by time.

    Args:
        db: Open sqlite3.Connection with SIO schema.
        error_type: If given, filter to this error type only.

    Returns:
        List of snapshot dicts ordered by created_at ascending.
    """
    if error_type:
        rows = db.execute(
            "SELECT * FROM velocity_snapshots WHERE error_type = ? ORDER BY created_at",
            (error_type,),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM velocity_snapshots ORDER BY created_at").fetchall()

    return [_row_to_dict(r) for r in rows]


def get_skill_effectiveness(
    db: sqlite3.Connection,
) -> list[dict]:
    """Measure effectiveness of suggestions that have been promoted to skills.

    Joins suggestions (where ``skill_file_path IS NOT NULL``) with
    velocity_snapshots to compute per-skill improvement metrics.

    For each skill-linked suggestion, finds the error_type from the linked
    pattern, then compares pre-rule and post-rule error rates from the
    velocity snapshot history.

    Parameters
    ----------
    db:
        Open sqlite3.Connection with SIO schema.

    Returns
    -------
    list[dict]
        Per-skill effectiveness records, each containing:
        ``skill_path``, ``target_error_type``, ``pre_rate``, ``post_rate``,
        ``improvement_pct``, ``sessions_tracked``.
    """
    # Find all suggestions that have a skill file path and have been applied
    skill_rows = db.execute(
        """
        SELECT
            s.id as suggestion_id,
            s.skill_file_path,
            s.description,
            p.tool_name,
            er_types.error_type
        FROM suggestions s
        LEFT JOIN patterns p ON p.id = s.pattern_id
        LEFT JOIN (
            SELECT pe.pattern_id,
                   er.error_type,
                   COUNT(*) as cnt
            FROM pattern_errors pe
            JOIN error_records er ON er.id = pe.error_id
            GROUP BY pe.pattern_id, er.error_type
            ORDER BY cnt DESC
        ) er_types ON er_types.pattern_id = s.pattern_id
        WHERE s.skill_file_path IS NOT NULL
          AND s.skill_file_path != ''
        GROUP BY s.id
        """
    ).fetchall()

    results: list[dict] = []

    for row in skill_rows:
        rd = dict(row)
        skill_path = rd["skill_file_path"]
        suggestion_id = rd["suggestion_id"]
        error_type = rd.get("error_type")

        if not error_type:
            # Try to infer error_type from suggestion description
            desc = rd.get("description") or ""
            # Use a simple heuristic: look for common error type names
            for candidate in (
                "tool_failure",
                "user_correction",
                "repeated_attempt",
                "undo",
                "agent_admission",
            ):
                if candidate.replace("_", " ") in desc.lower() or candidate in desc.lower():
                    error_type = candidate
                    break

        if not error_type:
            # Cannot measure effectiveness without an error type
            results.append(
                {
                    "skill_path": skill_path,
                    "target_error_type": None,
                    "pre_rate": None,
                    "post_rate": None,
                    "improvement_pct": None,
                    "sessions_tracked": 0,
                }
            )
            continue

        # Find velocity snapshots for this error type, linked to this suggestion
        snapshots = db.execute(
            """
            SELECT error_rate, error_count_in_window, rule_applied,
                   rule_suggestion_id, created_at
            FROM velocity_snapshots
            WHERE error_type = ?
            ORDER BY created_at
            """,
            (error_type,),
        ).fetchall()

        if not snapshots:
            results.append(
                {
                    "skill_path": skill_path,
                    "target_error_type": error_type,
                    "pre_rate": None,
                    "post_rate": None,
                    "improvement_pct": None,
                    "sessions_tracked": 0,
                }
            )
            continue

        snap_dicts = [dict(s) for s in snapshots]

        # Split into pre-rule and post-rule snapshots
        # Find the first snapshot where rule_suggestion_id matches
        rule_idx = None
        for i, s in enumerate(snap_dicts):
            if s["rule_suggestion_id"] == suggestion_id and s["rule_applied"]:
                rule_idx = i
                break

        if rule_idx is not None and rule_idx > 0:
            # Pre-rule: average rate of snapshots before the rule
            pre_snapshots = snap_dicts[:rule_idx]
            pre_rate = sum(s["error_rate"] for s in pre_snapshots) / len(pre_snapshots)

            # Post-rule: average rate of snapshots after the rule
            post_snapshots = snap_dicts[rule_idx:]
            post_rate = sum(s["error_rate"] for s in post_snapshots) / len(post_snapshots)

            # Improvement percentage
            if pre_rate > 0:
                improvement_pct = ((pre_rate - post_rate) / pre_rate) * 100
            else:
                improvement_pct = 0.0

            sessions_tracked = len(snap_dicts)
        else:
            # No clear pre/post split; use first and last snapshots
            pre_rate = snap_dicts[0]["error_rate"]
            post_rate = snap_dicts[-1]["error_rate"]

            if pre_rate > 0 and len(snap_dicts) > 1:
                improvement_pct = ((pre_rate - post_rate) / pre_rate) * 100
            else:
                improvement_pct = 0.0

            sessions_tracked = len(snap_dicts)

        results.append(
            {
                "skill_path": skill_path,
                "target_error_type": error_type,
                "pre_rate": round(pre_rate, 4),
                "post_rate": round(post_rate, 4),
                "improvement_pct": round(improvement_pct, 1),
                "sessions_tracked": sessions_tracked,
            }
        )

    return results
