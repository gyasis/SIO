"""Gold standards manager — promotes verified invocations for regression testing."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _get_conn(db_path) -> tuple[sqlite3.Connection, bool]:
    """Return (conn, owned) — owned=True means the caller must close it."""
    if isinstance(db_path, sqlite3.Connection):
        return db_path, False

    if db_path is None:
        canonical = os.environ.get(
            "SIO_DB_PATH",
            str(Path.home() / ".sio" / "sio.db"),
        )
        conn = sqlite3.connect(canonical)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn, True

    if isinstance(db_path, str):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn, True

    # Path-like
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn, True


def promote_to_gold(
    invocation_id_or_conn,
    invocation_id: int | None = None,
    db_path=None,
) -> int | None:
    """Promote an invocation to gold standard.

    Only promotes invocations with user_satisfied=1 AND correct_outcome=1.

    Supports two calling conventions:
    - New style: ``promote_to_gold(invocation_id, db_path=conn_or_path)``
    - Old style: ``promote_to_gold(conn, invocation_id)``  (backward-compat)

    Args:
        invocation_id_or_conn: Either an integer invocation_id (new style)
            or a sqlite3.Connection (old style).
        invocation_id: When using old style, the integer invocation ID.
        db_path: Connection, path string, or None (new style only).

    Returns:
        The gold_standards row id on success, or None if skipped.
    """
    # Detect calling convention
    if isinstance(invocation_id_or_conn, sqlite3.Connection):
        # Old style: promote_to_gold(conn, invocation_id)
        _conn = invocation_id_or_conn
        owned = False
        _invocation_id = invocation_id
    else:
        # New style: promote_to_gold(invocation_id, db_path=...)
        _invocation_id = invocation_id_or_conn
        _conn, owned = _get_conn(db_path)

    try:
        row = _conn.execute(
            "SELECT * FROM behavior_invocations WHERE id = ?",
            (_invocation_id,),
        ).fetchone()

        if row is None:
            return None

        # Only promote when both signals are positive
        if row["user_satisfied"] != 1 or row["correct_outcome"] != 1:
            return None

        # Idempotency: skip if already promoted
        existing = _conn.execute(
            "SELECT id FROM gold_standards WHERE invocation_id = ?",
            (_invocation_id,),
        ).fetchone()
        if existing is not None:
            return existing["id"]

        now = datetime.now(timezone.utc).isoformat()

        # Build dspy.Example-compatible JSON for the gold row
        dspy_example = {
            "inputs": ["user_message", "platform", "actual_action"],
            "data": {
                "user_message": row["user_message"],
                "platform": row["platform"],
                "actual_action": row["actual_action"],
                "expected_action": row["expected_action"] or row["actual_action"],
                "correct_outcome": row["correct_outcome"],
                "user_satisfied": row["user_satisfied"],
            },
        }
        dspy_example_json = json.dumps(dspy_example)

        # Build the INSERT — handle columns that may not exist in older schemas
        try:
            cur = _conn.execute(
                "INSERT INTO gold_standards "
                "(invocation_id, skill_name, platform, user_message, "
                "expected_action, expected_outcome, created_at, "
                "promoted_by, dspy_example_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _invocation_id,
                    row["actual_action"],
                    row["platform"],
                    row["user_message"],
                    row["expected_action"] or row["actual_action"],
                    str(row["correct_outcome"]) if row["correct_outcome"] is not None else None,
                    now,
                    "auto",
                    dspy_example_json,
                ),
            )
        except sqlite3.OperationalError:
            # Fallback: older schema without promoted_by / dspy_example_json columns
            cur = _conn.execute(
                "INSERT INTO gold_standards "
                "(invocation_id, skill_name, platform, user_message, "
                "expected_action, expected_outcome, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _invocation_id,
                    row["actual_action"],
                    row["platform"],
                    row["user_message"],
                    row["expected_action"] or row["actual_action"],
                    str(row["correct_outcome"]) if row["correct_outcome"] is not None else None,
                    now,
                ),
            )

        _conn.commit()
        return cur.lastrowid
    finally:
        if owned:
            _conn.close()


def get_all_for_skill(
    conn: sqlite3.Connection,
    skill_name: str,
) -> list[dict]:
    """Get all gold standards for a skill."""
    rows = conn.execute(
        "SELECT * FROM gold_standards WHERE skill_name = ?",
        (skill_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def replay_against_prompt(gold: dict, new_prompt: str) -> bool:
    """Check if a gold standard still passes with a new prompt.

    V0.1: Simple heuristic — checks if the gold's user_message
    shares significant terms with the new prompt.

    Returns:
        True if the gold standard is likely still satisfied.
    """
    gold_msg = gold.get("user_message", "").lower()
    new_lower = new_prompt.lower()

    gold_terms = set(gold_msg.split())
    new_terms = set(new_lower.split())

    if not gold_terms:
        return True

    overlap = gold_terms & new_terms
    similarity = len(overlap) / max(len(gold_terms), 1)
    return similarity >= 0.3
