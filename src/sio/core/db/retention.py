"""90-day rolling retention purge for behavior_invocations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def purge(
    conn: sqlite3.Connection,
    older_than_days: int = 90,
    dry_run: bool = False,
) -> int:
    """Delete old invocation records, preserving gold standards.

    Args:
        conn: Database connection.
        older_than_days: Delete records older than this many days.
        dry_run: If True, return count without deleting.

    Returns:
        Number of rows deleted (or would-be-deleted in dry_run mode).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()

    if dry_run:
        row = conn.execute(
            "SELECT COUNT(*) FROM behavior_invocations "
            "WHERE timestamp < ? AND id NOT IN (SELECT invocation_id FROM gold_standards)",
            (cutoff,),
        ).fetchone()
        return row[0]

    cur = conn.execute(
        "DELETE FROM behavior_invocations "
        "WHERE timestamp < ? AND id NOT IN (SELECT invocation_id FROM gold_standards)",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount
