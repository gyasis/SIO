"""PostToolUse hook handler — captures telemetry from Claude Code tool calls."""

from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

_DEFAULT_PLATFORM = "claude-code"
_DEFAULT_DB_DIR = os.path.expanduser("~/.sio/claude-code")


def handle_post_tool_use(stdin_json: str, *, conn=None) -> str:
    """Process a PostToolUse hook event.

    Parses the JSON payload, logs the invocation to the database,
    and always returns {"action": "allow"}.

    Args:
        stdin_json: JSON string from stdin per hook-contracts.md.
        conn: Optional database connection (for testing). If None,
              creates a new connection to the default DB path.

    Returns:
        JSON string with {"action": "allow"}.
    """
    try:
        payload = json.loads(stdin_json)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"action": "allow"})

    try:
        from sio.core.db.schema import init_db
        from sio.core.telemetry.logger import log_invocation

        own_conn = conn is None
        if own_conn:
            db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
            os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
            conn = init_db(db_path)

        session_id = payload.get("session_id", "unknown")
        tool_name = payload.get("tool_name", "unknown")
        tool_input = json.dumps(payload.get("tool_input", {}))
        tool_output = payload.get("tool_output", "")
        error = payload.get("error")
        user_message = payload.get("user_message", "[UNAVAILABLE]")

        log_invocation(
            conn=conn,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            error=error,
            user_message=user_message,
            platform=_DEFAULT_PLATFORM,
        )

        # Passive signal detection (T043)
        _detect_passive_signals(conn, session_id, user_message)

        if own_conn:
            conn.close()
    except Exception:
        logger.exception("PostToolUse hook error — continuing silently")

    return json.dumps({"action": "allow"})


def _detect_passive_signals(conn, session_id, user_message):
    """Run passive signal detection and update previous invocation if needed."""
    try:
        from sio.core.telemetry.passive_signals import (
            detect_correction,
            detect_re_invocation,
            detect_undo,
        )

        signal = None
        if detect_correction(user_message):
            signal = "correction"
        elif detect_undo(
            session_id,
            __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            conn,
        ):
            signal = "undo"
        elif detect_re_invocation(session_id, user_message, conn):
            signal = "re_invocation"

        if signal:
            # Update the PREVIOUS invocation's passive_signal field
            rows = conn.execute(
                "SELECT id FROM behavior_invocations "
                "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 2",
                (session_id,),
            ).fetchall()
            if len(rows) >= 2:
                prev_id = rows[1]["id"]
                conn.execute(
                    "UPDATE behavior_invocations SET passive_signal = ? "
                    "WHERE id = ?",
                    (signal, prev_id),
                )
                conn.commit()
    except Exception:
        logger.debug("Passive signal detection failed — non-critical")


def main():
    """Entry point when run as a module."""
    stdin_data = sys.stdin.read()
    result = handle_post_tool_use(stdin_data)
    sys.stdout.write(result)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
