"""sio.applier.writer — apply approved changes to config files.

Public API
----------
    apply_change(db, suggestion_id) -> dict
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def apply_change(db: sqlite3.Connection, suggestion_id: int) -> dict:
    """Apply an approved suggestion to its target file.

    Returns a dict with keys: success, change_id, diff_before, diff_after,
    target_file, reason (on failure).
    """
    row = db.execute(
        "SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)
    ).fetchone()

    if row is None:
        return {"success": False, "reason": "Suggestion not found"}

    suggestion = dict(row)

    if suggestion["status"] != "approved":
        return {
            "success": False,
            "reason": f"Suggestion is not approved (status: {suggestion['status']})",
        }

    target_path = Path(suggestion["target_file"])
    proposed_change = suggestion["proposed_change"]

    # Read existing content (or empty if file doesn't exist)
    if target_path.exists():
        diff_before = target_path.read_text()
    else:
        diff_before = ""
        target_path.parent.mkdir(parents=True, exist_ok=True)

    # Append proposed change (never overwrite)
    diff_after = diff_before
    if diff_before and not diff_before.endswith("\n"):
        diff_after += "\n"
    if diff_before:
        diff_after += "\n"
    diff_after += proposed_change
    if not diff_after.endswith("\n"):
        diff_after += "\n"

    # Write the file
    target_path.write_text(diff_after)

    # Record in applied_changes table
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO applied_changes "
        "(suggestion_id, target_file, diff_before, diff_after, applied_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (suggestion_id, str(target_path), diff_before, diff_after, now),
    )
    change_id = cur.lastrowid

    # Update suggestion status to 'applied'
    db.execute(
        "UPDATE suggestions SET status = 'applied' WHERE id = ?",
        (suggestion_id,),
    )
    db.commit()

    return {
        "success": True,
        "change_id": change_id,
        "diff_before": diff_before,
        "diff_after": diff_after,
        "target_file": str(target_path),
    }
