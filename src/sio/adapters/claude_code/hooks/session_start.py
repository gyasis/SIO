"""SessionStart hook handler — injects a brief SIO briefing at session start.

Runs ``build_session_briefing`` and outputs a compact plain-text summary
that Claude Code injects into the agent's context.  If the SIO database
does not exist yet or the briefing is empty, the hook outputs nothing
(exit 0 with no stdout) so the agent context is not cluttered.

Designed to complete in <2 000 ms. Failures are silent — a missing
briefing is acceptable; blocking the session is not.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

_ERROR_LOG = os.path.expanduser("~/.sio/hook_errors.log")
_DB_PATH = os.path.expanduser("~/.sio/sio.db")


def _log_error(msg: str) -> None:
    """Append an error line to the hook error log file."""
    try:
        os.makedirs(os.path.dirname(_ERROR_LOG), exist_ok=True)
        with open(_ERROR_LOG, "a") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"[{ts}] SessionStart: {msg}\n")
    except Exception:
        pass


def _build_briefing() -> str:
    """Build the session briefing text.

    Returns empty string if no database or nothing to report.
    """
    if not os.path.exists(_DB_PATH):
        return ""

    import sqlite3

    from sio.core.config import load_config
    from sio.suggestions.consultant import build_session_briefing

    config = load_config()
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        text = build_session_briefing(conn, config=config)
    finally:
        conn.close()

    return text.strip() if text else ""


def handle_session_start(stdin_json: str) -> str:
    """Process a SessionStart hook event.

    Builds a brief SIO session briefing and returns it as user-visible
    output.  Claude Code injects hook stdout into the agent context, so
    we output plain text (not JSON action) — the hook is non-blocking.

    If there is nothing to report, returns empty string so nothing is
    injected.

    Args:
        stdin_json: JSON string from stdin (session_id etc.).

    Returns:
        Plain-text briefing string, or empty string.
    """
    try:
        briefing = _build_briefing()
        if briefing:
            return briefing
    except Exception as err:
        _log_error(f"briefing failed: {err!r}")

    return ""


def main():
    """Entry point when run as a module."""
    stdin_data = sys.stdin.read()
    result = handle_session_start(stdin_data)
    if result:
        sys.stdout.write(result)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
