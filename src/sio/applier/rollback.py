"""sio.applier.rollback — revert applied changes.

Public API
----------
    rollback_change(db, change_id) -> dict
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def rollback_change(db: sqlite3.Connection, change_id: int) -> dict:
    """Rollback an applied change by restoring diff_before content.

    Returns a dict with keys: success, change_id, target_file, reason (on failure).
    """
    row = db.execute(
        "SELECT * FROM applied_changes WHERE id = ?", (change_id,)
    ).fetchone()

    if row is None:
        return {"success": False, "reason": "Change not found"}

    change = dict(row)

    if change.get("rolled_back_at") is not None:
        return {
            "success": False,
            "change_id": change_id,
            "target_file": change["target_file"],
            "reason": "Change already rolled back",
        }

    target_path = Path(change["target_file"])
    diff_before = change["diff_before"]

    # Restore file to original content
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(diff_before)

    # Mark rollback timestamp
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE applied_changes SET rolled_back_at = ? WHERE id = ?",
        (now, change_id),
    )

    # Update suggestion status to 'rolled_back'
    db.execute(
        "UPDATE suggestions SET status = 'rolled_back' WHERE id = ?",
        (change["suggestion_id"],),
    )
    db.commit()

    return {
        "success": True,
        "change_id": change_id,
        "target_file": change["target_file"],
    }
