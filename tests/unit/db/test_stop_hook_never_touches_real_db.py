"""The Stop hook must never write outside the connection it was given.

Regression guard. The canonical sync added to the Stop hook resolved GLOBAL paths
(~/.sio/sio.db and the real per-platform DB) regardless of the `conn` passed in. Because
tests/unit/test_hooks.py sets neither SIO_DB_PATH nor SIO_HOME, a single test run copied
55,475 production rows into the real canonical database (2026-10-27).

The hook now syncs only when it opened its own connection — the real runtime path.
"""

from __future__ import annotations

import json
import sqlite3

from sio.adapters.claude_code.hooks.stop import handle_stop
from sio.core.db.schema import init_db


def test_injected_connection_means_no_global_sync(tmp_path, monkeypatch):
    """With a conn supplied, the hook must not write to the canonical DB at all."""
    canonical = tmp_path / "canonical-sio.db"
    platform = tmp_path / "claude-code" / "behavior_invocations.db"
    platform.parent.mkdir(parents=True, exist_ok=True)
    init_db(str(canonical)).close()

    # Point the sync's globals at our temp canonical DB. If the guard fails, rows land here
    # instead of the real ~/.sio/sio.db — observable, and harmless.
    monkeypatch.setenv("SIO_DB_PATH", str(canonical))
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(platform))

    conn = init_db(str(tmp_path / "hook.db"))
    conn.execute(
        "INSERT INTO behavior_invocations "
        "(session_id, timestamp, platform, user_message, behavior_type, tool_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("claude:guard-1", "2026-10-27T09:00:00+00:00", "claude-code", "m", "skill", "Bash"),
    )
    conn.commit()

    handle_stop(json.dumps({"session_id": "claude:guard-1",
                            "transcript_path": str(tmp_path / "t.jsonl")}), conn=conn)

    c = sqlite3.connect(str(canonical))
    try:
        n = c.execute("SELECT COUNT(*) FROM behavior_invocations").fetchone()[0]
    finally:
        c.close()
    assert n == 0, (
        f"the hook wrote {n} rows to the canonical DB while running against an injected "
        "connection — this is how a test run reached production"
    )
