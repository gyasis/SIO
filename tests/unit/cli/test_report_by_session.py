"""`sio report --by-session` joins spend to tool calls on the transcript path."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

_TRANSCRIPT = "/home/u/.claude/projects/-home-u-code/abc-123.jsonl"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from sio.core.runlog import writer as _w

    (tmp_path / "runs").mkdir()
    monkeypatch.setattr(_w, "_RUNS_DIR", tmp_path / "runs")
    (tmp_path / "sio").mkdir()
    monkeypatch.setenv("SIO_DB_PATH", str(tmp_path / "sio" / "sio.db"))


def _seed(db, snapshots, tool_names):
    """One transcript: a session_metrics row per snapshot, plus its tool calls."""
    from sio.core.db.schema import init_db

    now = datetime.now(timezone.utc).isoformat()
    conn = init_db(str(db))
    try:
        for tokens, errors in snapshots:
            conn.execute(
                "INSERT INTO session_metrics (session_id, file_path, "
                "total_input_tokens, total_output_tokens, error_count, mined_at) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (f"{_TRANSCRIPT}:{tokens:016x}", _TRANSCRIPT, tokens, errors, now),
            )
        # The timestamp varies per row: (platform, session_id, timestamp,
        # tool_name) is UNIQUE, so two Bash calls need distinct times.
        for i, name in enumerate(tool_names):
            conn.execute(
                "INSERT INTO behavior_invocations (session_id, timestamp, platform, "
                "user_message, behavior_type, tool_name, history_file) "
                "VALUES (?, ?, 'claude-code', '[test]', 'tool', ?, ?)",
                ("claude:abc-123", f"{now[:-6]}{i:06d}", name, _TRANSCRIPT),
            )
        conn.commit()
    finally:
        conn.close()


def _run():
    from sio.cli.main import cli

    r = CliRunner().invoke(cli, ["report", "--by-session"])
    assert r.exit_code == 0, r.output
    return r.output


def test_snapshots_collapse_to_the_fullest_one(tmp_path):
    """A growing transcript gets a row per mine; the report shows one line."""
    db = tmp_path / "sio" / "sio.db"
    _seed(db, [(500, 2), (9000, 7), (0, 1)], ["Bash", "Bash", "Read"])
    out = _run()
    assert out.count("abc-123") == 1
    assert "9,000" in out  # the MAX, not the first row and not the 0 placeholder
    assert "500" not in out
    assert "Bash" in out  # most-used tool wins over Read
    assert "Read" not in out


def test_zero_only_session_still_reports(tmp_path):
    """Hook-written placeholder rows are a real session, just an unmeasured one."""
    db = tmp_path / "sio" / "sio.db"
    _seed(db, [(0, 1)], ["Bash"])
    out = _run()
    assert "abc-123" in out


def test_session_without_tool_calls_is_left_out(tmp_path):
    """The join needs history_file; a transcript with no tool rows cannot join."""
    db = tmp_path / "sio" / "sio.db"
    _seed(db, [(4200, 1)], [])
    out = _run()
    assert "abc-123" not in out
    assert "No session spend joinable" in out


def test_history_file_is_the_join_key_not_session_id(tmp_path):
    """session_id differs on both sides by design, so it must not be the key."""
    db = tmp_path / "sio" / "sio.db"
    _seed(db, [(777, 0)], ["Bash"])
    conn = sqlite3.connect(db)
    try:
        sm, bi = conn.execute(
            "SELECT (SELECT session_id FROM session_metrics), "
            "(SELECT session_id FROM behavior_invocations)"
        ).fetchone()
    finally:
        conn.close()
    assert sm != bi
    assert "777" in _run()
