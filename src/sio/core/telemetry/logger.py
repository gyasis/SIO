"""Telemetry logger — writes invocation records to the behavior database."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from sio.core.db.queries import insert_invocation
from sio.core.telemetry.auto_labeler import auto_label
from sio.core.telemetry.secret_scrubber import scrub

logger = logging.getLogger(__name__)


def log_invocation(
    conn: sqlite3.Connection,
    session_id: str,
    tool_name: str,
    tool_input: str,
    tool_output: str | None,
    error: str | None,
    user_message: str,
    platform: str,
) -> int:
    """Log a single tool invocation to the behavior database.

    Scrubs secrets from user_message, applies auto-labeling, and handles
    deduplication and error resilience.

    Returns:
        Row id on success, -1 on error.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        scrubbed_message = scrub(user_message) if user_message else user_message

        # Deduplication: check for existing record within 1s window
        existing = conn.execute(
            "SELECT id FROM behavior_invocations "
            "WHERE session_id = ? AND actual_action = ? "
            "AND ABS(JULIANDAY(timestamp) - JULIANDAY(?)) < (1.0 / 86400.0)",
            (session_id, tool_name, now),
        ).fetchone()
        if existing:
            return existing[0]

        # Auto-label agent-inferred fields
        labels = auto_label(tool_name, tool_input, tool_output, error)

        record = {
            "session_id": session_id,
            "timestamp": now,
            "platform": platform,
            "user_message": scrubbed_message,
            "behavior_type": "skill",
            "actual_action": tool_name,
            "expected_action": None,
            "activated": labels["activated"],
            "correct_action": labels["correct_action"],
            "correct_outcome": labels["correct_outcome"],
            "user_satisfied": None,
            "user_note": None,
            "passive_signal": None,
            "history_file": None,
            "line_start": None,
            "line_end": None,
            "token_count": None,
            "latency_ms": None,
            "labeled_by": None,
            "labeled_at": None,
        }

        return insert_invocation(conn, record)

    except Exception:
        logger.exception("Failed to log invocation")
        return -1
