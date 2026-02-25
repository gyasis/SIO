"""PostToolUse hook handler — captures telemetry from Claude Code tool calls."""

from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

_DEFAULT_PLATFORM = "claude-code"
_DEFAULT_DB_DIR = os.path.expanduser("~/.sio/claude-code")


def handle_post_tool_use(stdin_json: str) -> str:
    """Process a PostToolUse hook event.

    Parses the JSON payload, logs the invocation to the database,
    and always returns {"action": "allow"}.

    Args:
        stdin_json: JSON string from stdin per hook-contracts.md.

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
        conn.close()
    except Exception:
        logger.exception("PostToolUse hook error — continuing silently")

    return json.dumps({"action": "allow"})


def main():
    """Entry point when run as a module."""
    stdin_data = sys.stdin.read()
    result = handle_post_tool_use(stdin_data)
    sys.stdout.write(result)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
