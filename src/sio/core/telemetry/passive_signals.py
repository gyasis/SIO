"""Passive signal detection — auto-detects undos, corrections, re-invocations."""

from __future__ import annotations

import sqlite3
from datetime import datetime

_CORRECTION_PREFIXES = ("no,", "actually,", "instead,", "wait,", "stop,")


def detect_correction(message: str) -> bool:
    """Detect if a user message is correcting a previous action.

    Returns True if message starts with correction language like
    "No,", "Actually,", "Instead,", etc.
    """
    if not message:
        return False
    lower = message.strip().lower()
    return any(lower.startswith(prefix) for prefix in _CORRECTION_PREFIXES)


def detect_undo(
    session_id: str,
    timestamp: str,
    conn: sqlite3.Connection,
) -> bool:
    """Detect if current action is an undo of the previous action.

    Returns True if the previous invocation in this session used a tool
    like git checkout/revert and occurred within 30 seconds.
    """
    rows = conn.execute(
        "SELECT actual_action, user_message, timestamp FROM behavior_invocations "
        "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 2",
        (session_id,),
    ).fetchall()

    if len(rows) < 2:
        return False

    current = rows[0]
    previous = rows[1]

    # Check if current invocation is a revert/undo action.
    # actual_action is the Claude tool name (e.g. "Bash"), so we also
    # check user_message for git undo commands.
    undo_actions = ("git checkout", "git revert", "git reset", "git restore")
    current_action = (current["actual_action"] or "").lower()
    current_message = (current["user_message"] or "").lower()
    if not any(
        undo in current_action or undo in current_message
        for undo in undo_actions
    ):
        return False

    # Check time window (30 seconds)
    try:
        t_current = datetime.fromisoformat(current["timestamp"])
        t_previous = datetime.fromisoformat(previous["timestamp"])
        delta = abs((t_current - t_previous).total_seconds())
        return delta <= 30.0
    except (ValueError, TypeError):
        return False


def detect_re_invocation(
    session_id: str,
    intent: str,
    conn: sqlite3.Connection,
) -> bool:
    """Detect if the same intent was previously invoked with a different tool.

    Returns True if a previous invocation in this session had a different
    actual_action but similar user_message (same intent).
    """
    if not intent:
        return False

    rows = conn.execute(
        "SELECT actual_action, user_message FROM behavior_invocations "
        "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 5",
        (session_id,),
    ).fetchall()

    if len(rows) < 2:
        return False

    current_action = rows[0]["actual_action"]
    for prev in rows[1:]:
        if prev["actual_action"] != current_action and prev["user_message"] == intent:
            return True

    return False
