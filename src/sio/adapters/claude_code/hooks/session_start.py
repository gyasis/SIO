"""SessionStart hook handler — injects a brief SIO briefing at session start.

Pure reader (rewritten 2026-07-02)
----------------------------------
The briefing is expensive to compute (it scans SIO DBs that have grown to
hundreds of MB).  It is therefore materialised **off-session** into a small
store (see ``sio.suggestions.briefing_store``) by the scheduler / a systemd
user timer / the passive-analysis pipeline — NEVER on a session's hot path.

This hook just *reads* that store: an instant file read, zero compute, zero
subprocess.  If the store is missing/empty (e.g. a brand-new machine before the
timer's first run), it injects nothing and stays silent — the store will be
warm by the next session.

The same store module is shared in core, so every coding-agent adapter's
session-start reads the identical pre-computed briefing — this is not
Claude-Code-specific.
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


def handle_session_start(stdin_json: str) -> str:  # noqa: ARG001
    """Process a SessionStart event — instantly, off the DB-scan critical path.

    Reads the pre-computed briefing store and returns it verbatim.  Never
    computes, never spawns, never blocks.

    Args:
        stdin_json: JSON string from stdin (session_id etc.).  Unused, kept for
            signature compatibility with the Claude Code hook contract.

    Returns:
        The materialised briefing text, or "" when there is nothing to inject.
    """
    try:
        if os.environ.get("SIO_BRIEFING_DISABLED") == "1":
            return ""
        # No canonical DB -> nothing could ever have been briefed.
        if not os.path.exists(_DB_PATH):
            return ""

        from sio.suggestions.briefing_store import read_store

        return read_store()
    except Exception as err:  # noqa: BLE001
        _log_error(f"briefing read failed: {err!r}")
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
