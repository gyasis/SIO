"""sio.ground_truth.reviewer -- Human review actions for ground truth entries.

Public API
----------
    approve(conn, gt_id, note=None) -> bool
    reject(conn, gt_id, note=None) -> bool
    edit(conn, gt_id, new_content) -> int   # returns new row ID
"""

from __future__ import annotations

import sqlite3

from sio.core.db.queries import (
    insert_ground_truth,
    update_ground_truth_label,
)


def approve(conn: sqlite3.Connection, gt_id: int, note: str | None = None) -> bool:
    """Approve a ground truth candidate.

    Sets ``label='positive'`` and ``source='approved'``.

    Args:
        conn: SQLite connection with SIO schema.
        gt_id: The ground_truth row ID.
        note: Optional reviewer note.

    Returns:
        True if the row existed and was updated.
    """
    # Check row exists
    row = conn.execute(
        "SELECT id FROM ground_truth WHERE id = ?", (gt_id,)
    ).fetchone()
    if row is None:
        return False

    update_ground_truth_label(
        conn, gt_id, label="positive", source="approved", user_note=note
    )
    return True


def reject(conn: sqlite3.Connection, gt_id: int, note: str | None = None) -> bool:
    """Reject a ground truth candidate.

    Sets ``label='negative'`` and ``source='rejected'``.

    Args:
        conn: SQLite connection with SIO schema.
        gt_id: The ground_truth row ID.
        note: Optional reviewer note explaining rejection.

    Returns:
        True if the row existed and was updated.
    """
    row = conn.execute(
        "SELECT id FROM ground_truth WHERE id = ?", (gt_id,)
    ).fetchone()
    if row is None:
        return False

    update_ground_truth_label(
        conn, gt_id, label="negative", source="rejected", user_note=note
    )
    return True


def edit(conn: sqlite3.Connection, gt_id: int, new_content: dict) -> int:
    """Edit a ground truth entry by creating a NEW row with updated content.

    The original row is left unchanged. A new row is inserted with the
    edited fields, ``source='edited'``, and ``label='positive'``.

    Args:
        conn: SQLite connection with SIO schema.
        gt_id: The original ground_truth row ID.
        new_content: Dict of fields to override. Supported keys:
            ``rule_title``, ``prevention_instructions``, ``rationale``,
            ``target_surface``, ``error_type``, ``pattern_summary``.

    Returns:
        The new row ID.

    Raises:
        ValueError: If the original row does not exist.
    """
    row = conn.execute(
        "SELECT * FROM ground_truth WHERE id = ?", (gt_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Ground truth row {gt_id} not found")

    original = dict(row)

    new_row_id = insert_ground_truth(
        conn,
        pattern_id=original["pattern_id"],
        error_examples_json=original["error_examples_json"],
        error_type=new_content.get("error_type", original["error_type"]),
        pattern_summary=new_content.get("pattern_summary", original["pattern_summary"]),
        target_surface=new_content.get("target_surface", original["target_surface"]),
        rule_title=new_content.get("rule_title", original["rule_title"]),
        prevention_instructions=new_content.get(
            "prevention_instructions", original["prevention_instructions"]
        ),
        rationale=new_content.get("rationale", original["rationale"]),
        source="edited",
        confidence=original.get("confidence"),
        file_path=original.get("file_path"),
    )

    # Mark the new row as positive immediately
    update_ground_truth_label(conn, new_row_id, label="positive", source="edited")

    return new_row_id
