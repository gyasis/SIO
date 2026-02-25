"""Feedback labeler — applies user binary satisfaction labels."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def label_latest(
    conn: sqlite3.Connection,
    session_id: str,
    signal: str,
    note: str | None = None,
) -> bool:
    """Label the most recent invocation in a session.

    Args:
        conn: Database connection.
        session_id: Session to find the latest invocation for.
        signal: "++" for satisfied, "--" for unsatisfied.
        note: Optional free-text note.

    Returns:
        True if a record was updated, False if no invocation found.
    """
    # Parse signal
    if signal.startswith("++"):
        satisfied = 1
    elif signal.startswith("--"):
        satisfied = 0
    else:
        return False

    # Find most recent invocation for this session
    row = conn.execute(
        "SELECT id FROM behavior_invocations WHERE session_id = ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (session_id,),
    ).fetchone()

    if not row:
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE behavior_invocations SET user_satisfied = ?, user_note = ?, "
        "labeled_by = 'inline', labeled_at = ? WHERE id = ?",
        (satisfied, note, now, row[0]),
    )
    conn.commit()
    return True
