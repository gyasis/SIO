"""sio.review.reviewer — human review workflow for suggestions.

Public API
----------
    review_pending(db) -> list[dict]
    get_suggestion(db, id) -> dict | None
    approve(db, id, note=None) -> bool
    reject(db, id, note=None) -> bool
    defer(db, id, note=None) -> bool
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def review_pending(db: sqlite3.Connection) -> list[dict]:
    """Load all suggestions with status='pending', ordered by confidence DESC."""
    rows = db.execute(
        "SELECT * FROM suggestions WHERE status = 'pending' "
        "ORDER BY confidence DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_suggestion(db: sqlite3.Connection, id: int) -> dict | None:
    """Retrieve a single suggestion by ID."""
    row = db.execute(
        "SELECT * FROM suggestions WHERE id = ?", (id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def _update_status(
    db: sqlite3.Connection,
    id: int,
    status: str,
    note: str | None = None,
) -> bool:
    """Update suggestion status, optionally set user_note and reviewed_at."""
    now = datetime.now(timezone.utc).isoformat()
    if note is not None:
        cur = db.execute(
            "UPDATE suggestions SET status = ?, user_note = ?, reviewed_at = ? "
            "WHERE id = ?",
            (status, note, now, id),
        )
    else:
        cur = db.execute(
            "UPDATE suggestions SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, now, id),
        )
    db.commit()
    return cur.rowcount > 0


def approve(db: sqlite3.Connection, id: int, note: str | None = None) -> bool:
    """Approve a suggestion. Returns True if the row existed."""
    return _update_status(db, id, "approved", note)


def reject(db: sqlite3.Connection, id: int, note: str | None = None) -> bool:
    """Reject a suggestion. Returns True if the row existed."""
    return _update_status(db, id, "rejected", note)


def defer(db: sqlite3.Connection, id: int, note: str | None = None) -> bool:
    """Defer a suggestion — keeps status as 'pending', records optional note."""
    now = datetime.now(timezone.utc).isoformat()
    if note is not None:
        cur = db.execute(
            "UPDATE suggestions SET user_note = ?, reviewed_at = ? WHERE id = ?",
            (note, now, id),
        )
    else:
        cur = db.execute(
            "UPDATE suggestions SET reviewed_at = ? WHERE id = ?",
            (now, id),
        )
    db.commit()
    return cur.rowcount > 0
