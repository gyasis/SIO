"""Batch review — sequential review of unlabeled invocations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def get_reviewable(
    conn: sqlite3.Connection,
    platform: str,
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Get unlabeled invocations for review, sorted by timestamp.

    FR-026: Warns if labeled distribution is >90% skewed to one class.

    Returns:
        List of invocation dicts, each with an optional 'skew_warning' key.
    """
    query = (
        "SELECT * FROM behavior_invocations "
        "WHERE user_satisfied IS NULL AND platform = ?"
    )
    params: list = [platform]

    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)

    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    results = [dict(r) for r in rows]

    # FR-026: Check label distribution skew
    dist = conn.execute(
        "SELECT user_satisfied, COUNT(*) as cnt FROM behavior_invocations "
        "WHERE user_satisfied IS NOT NULL AND platform = ? GROUP BY user_satisfied",
        (platform,),
    ).fetchall()

    skew_warning = None
    if dist:
        total_labeled = sum(r["cnt"] for r in dist)
        if total_labeled > 0:
            for r in dist:
                ratio = r["cnt"] / total_labeled
                if ratio > 0.9:
                    label = "satisfied" if r["user_satisfied"] == 1 else "unsatisfied"
                    skew_warning = (
                        f"Warning: {ratio:.0%} of labels are '{label}'. "
                        f"Consider balancing your feedback."
                    )

    if skew_warning:
        for r in results:
            r["skew_warning"] = skew_warning

    return results


def apply_label(
    conn: sqlite3.Connection,
    invocation_id: int,
    signal: str,
    note: str | None = None,
) -> bool:
    """Apply a satisfaction label to a specific invocation.

    Args:
        invocation_id: ID of the invocation to label.
        signal: "++" for satisfied, "--" for unsatisfied.
        note: Optional free-text note.

    Returns:
        True if updated, False if not found.
    """
    satisfied = 1 if signal.startswith("++") else 0
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE behavior_invocations SET user_satisfied = ?, user_note = ?, "
        "labeled_by = 'batch_review', labeled_at = ? WHERE id = ?",
        (satisfied, note, now, invocation_id),
    )
    conn.commit()
    return cur.rowcount > 0
