"""`sio mine` for a non-claude agent files ONE session under ONE canonical id
and never duplicates it — the bug behind PR #44.

Before: `mine --session pi:<partial>` stamped rows with the partial handle the
user typed while the bulk path stamped the full file stem, so one session
lived under two ids and every re-mine re-inserted everything (nothing was
recorded in processed_sessions for non-claude agents).

The pi session here is the SYNTHETIC fixture from the pi adapter tests. HOME
and every store root are pointed at ``tmp_path``; nothing touches ``~/.sio``
or ``~/.pi``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.unit.adapters.test_pi_adapter import (
    BASE_ROWS,
    SESSION_UUID,
    STEM,
    _entry,
    _session_dir,
    _write_session,
)

CANONICAL = f"pi:{STEM}"


@pytest.fixture(autouse=True)
def _runs_dir_in_tmp(tmp_path, monkeypatch):
    """Keep `runlogged` run-logs out of the real ~/.sio/runs.

    The writer resolves its directory from Path.home() at import time, so a
    HOME monkeypatch alone does not redirect it.
    """
    from sio.core.runlog import writer as _w

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(_w, "_RUNS_DIR", runs)


@pytest.fixture
def pi_env(tmp_path: Path, monkeypatch):
    """A fake HOME with one pi session and an isolated SIO db."""
    from sio.adapters import factory
    from sio.search import cli as search_cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(factory, "_PI_SESSIONS", tmp_path / ".pi" / "agent" / "sessions")
    monkeypatch.setattr(search_cli, "HOME", tmp_path)
    db = tmp_path / "sio" / "sio.db"
    db.parent.mkdir()
    monkeypatch.setenv("SIO_DB_PATH", str(db))
    session = _session_dir(tmp_path) / f"{STEM}.jsonl"
    _write_session(session, BASE_ROWS)
    return {"db": db, "session": session}


def _errors(db: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT session_id, agent, timestamp, error_type, tool_name, error_text "
            "FROM error_records ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _processed(db: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT file_path, file_hash, agent, message_count FROM processed_sessions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_partial_session_then_bulk_yields_one_canonical_id_no_duplicates(pi_env):
    from sio.cli.main import cli

    runner = CliRunner()
    partial = SESSION_UUID[:8]
    r1 = runner.invoke(cli, ["mine", "--session", f"pi:{partial}"])
    assert r1.exit_code == 0, r1.output
    assert f"Adapter-mined {CANONICAL}" in r1.output
    first = _errors(pi_env["db"])
    assert first, "the fixture carries harness-flagged failures; expected errors"
    assert {row[0] for row in first} == {CANONICAL}
    assert {row[1] for row in first} == {"pi"}
    # the processed row is keyed by the canonical id, with the agent set
    assert [(p[0], p[2]) for p in _processed(pi_env["db"])] == [(CANONICAL, "pi")]

    # same session via --session again: unchanged -> skipped outright
    r2 = runner.invoke(cli, ["mine", "--session", f"pi:{partial}"])
    assert r2.exit_code == 0 and "unchanged since last mine" in r2.output
    assert _errors(pi_env["db"]) == first

    # ... and via the bulk path: skipped as unchanged, still one id, no dups
    r3 = runner.invoke(cli, ["mine", "--agent", "pi", "--since", "30 days"])
    assert r3.exit_code == 0, r3.output
    assert "Bulk-mined pi: 1 session (0 mined, 1 unchanged) -> 0 errors" in r3.output
    assert _errors(pi_env["db"]) == first


def test_bulk_then_partial_session_dedupes_via_constraint(pi_env):
    """Even when the skip logic is bypassed the UNIQUE fingerprint holds."""
    from sio.cli.main import cli

    runner = CliRunner()
    assert runner.invoke(cli, ["mine", "--agent", "pi", "--since", "30 days"]).exit_code == 0
    first = _errors(pi_env["db"])
    # forget the processed row so the session is re-read in full
    conn = sqlite3.connect(str(pi_env["db"]))
    conn.execute("DELETE FROM processed_sessions")
    conn.commit()
    conn.close()
    r = runner.invoke(cli, ["mine", "--session", f"pi:{SESSION_UUID[:8]}"])
    assert r.exit_code == 0, r.output
    # Every error the bulk path found is "already present" for the adapter
    # path, and the adapter path finds nothing more: the bulk parser now keeps
    # harness-flagged failures with empty content (pi's silent `!cmd` exit).
    assert f"0 new, {len(first)} already present" in r.output
    assert _errors(pi_env["db"]) == first
    assert all(row[0] == CANONICAL for row in first)


# ---------------------------------------------------------------------------
# the bulk summary line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            ("pi", 0, 0, 0, 0, 0),
            "Bulk-mined pi: 0 sessions (0 mined, 0 unchanged) -> 0 errors "
            "(0 new, 0 already present).",
        ),
        (
            ("pi", 1, 0, 1, 1, 0),
            "Bulk-mined pi: 1 session (1 mined, 0 unchanged) -> 1 error "
            "(1 new, 0 already present).",
        ),
        (
            ("pi", 0, 1, 0, 0, 0),
            "Bulk-mined pi: 1 session (0 mined, 1 unchanged) -> 0 errors "
            "(0 new, 0 already present).",
        ),
        (
            ("pi", 1, 1, 3, 3, 0),
            "Bulk-mined pi: 2 sessions (1 mined, 1 unchanged) -> 3 errors "
            "(3 new, 0 already present).",
        ),
        (
            ("codex", 4, 2, 7, 5, 2),
            "Bulk-mined codex: 6 sessions (4 mined, 2 unchanged) -> 7 errors "
            "(5 new, 2 already present).",
        ),
    ],
)
def test_bulk_summary_counts_every_session_seen(args, expected):
    """The sessions figure is what was SEEN, split into mined vs unchanged —
    never `1 sessions (1 unchanged, skipped)`, which read as a contradiction."""
    from sio.cli.main import _bulk_summary

    assert _bulk_summary(*args) == expected


# ---------------------------------------------------------------------------
# bulk / --session parity on harness-flagged failures with empty content
# ---------------------------------------------------------------------------

EMPTY_ISERROR = _entry(
    "message", "e6", "e5", "2026-09-15T08:01:06.100Z",
    message={
        "role": "toolResult",
        "toolCallId": "call_bad",
        "toolName": "read",
        "content": [],  # the harness flagged it, but said nothing
        "isError": True,
    },
)


def _fingerprints(db: Path) -> set[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return set(
            conn.execute(
                "SELECT session_id, timestamp, error_type, tool_name, error_text FROM error_records"
            ).fetchall()
        )
    finally:
        conn.close()


def test_bulk_and_session_paths_file_the_same_flagged_failures(pi_env, tmp_path, monkeypatch):
    """One session: (a) an isError toolResult with EMPTY content, (b) a `!cmd`
    with exit code 1 and no output, (c) a successful call. Mined through BOTH
    paths into two fresh DBs, the error rows (fingerprints) are identical."""
    from sio.cli.main import cli
    from tests.unit.adapters.test_pi_adapter import (
        ASSISTANT_CALL,
        ASSISTANT_TEXT,
        BASH_EXEC,
        HEADER,
        TOOL_OK,
        USER_MSG,
    )

    assert BASH_EXEC["message"]["output"] == "" and BASH_EXEC["message"]["exitCode"] == 1
    _write_session(
        pi_env["session"],
        [HEADER, USER_MSG, ASSISTANT_CALL, TOOL_OK, EMPTY_ISERROR, ASSISTANT_TEXT, BASH_EXEC],
    )
    runner = CliRunner()

    db_session = pi_env["db"]
    r1 = runner.invoke(cli, ["mine", "--session", f"pi:{SESSION_UUID[:8]}"])
    assert r1.exit_code == 0, r1.output

    db_bulk = tmp_path / "sio-bulk" / "sio.db"
    db_bulk.parent.mkdir()
    monkeypatch.setenv("SIO_DB_PATH", str(db_bulk))
    r2 = runner.invoke(cli, ["mine", "--agent", "pi", "--since", "30 days"])
    assert r2.exit_code == 0, r2.output
    assert "Bulk-mined pi: 1 session (1 mined, 0 unchanged) -> 2 errors" in r2.output

    expected = {
        (CANONICAL, "2026-09-15T08:01:06.100Z", "tool_failure", "read",
         "isError (empty tool result)"),
        (CANONICAL, "2026-09-15T08:02:00.000Z", "tool_failure", "bash", "exit code 1"),
    }
    assert _fingerprints(db_session) == expected
    assert _fingerprints(db_bulk) == expected


def test_bulk_path_still_drops_unflagged_empty_events(pi_env):
    """Parity must not come from flooding: an empty, un-flagged event is noise."""
    from sio.search.cli import search_pi
    from tests.unit.adapters.test_pi_adapter import HEADER, TOOL_OK

    empty_ok = dict(TOOL_OK, message=dict(TOOL_OK["message"], content=[], isError=False))
    _write_session(pi_env["session"], [HEADER, empty_ok, EMPTY_ISERROR])
    recs = list(search_pi("", False, None))
    tool_recs = [(r.content, (r.metadata or {}).get("error")) for r in recs if r.role == "tool"]
    # the empty successful result is gone; the empty FLAGGED one survives
    assert tool_recs == [("", "isError (empty tool result)")]


def test_grown_session_adds_only_the_new_errors(pi_env):
    from sio.cli.main import cli

    runner = CliRunner()
    assert runner.invoke(cli, ["mine", "--agent", "pi", "--since", "30 days"]).exit_code == 0
    first = _errors(pi_env["db"])

    # the live session grows: a new failing tool call lands
    new_rows = [
        _entry(
            "message", "g1", "e9", "2026-09-15T09:00:00.000Z",
            message={
                "role": "assistant",
                "content": [{"type": "toolCall", "id": "call-new", "name": "bash",
                             "arguments": {"command": "false"}}],
            },
        ),
        _entry(
            "message", "g2", "g1", "2026-09-15T09:00:01.000Z",
            message={
                "role": "toolResult", "toolCallId": "call-new", "toolName": "bash",
                "content": [{"type": "text", "text": "exit code 2"}], "isError": True,
            },
        ),
    ]
    with pi_env["session"].open("a") as fh:
        for row in new_rows:
            fh.write(json.dumps(row) + "\n")

    r = runner.invoke(cli, ["mine", "--agent", "pi", "--since", "30 days"])
    assert r.exit_code == 0, r.output
    assert "0 unchanged" in r.output
    after = _errors(pi_env["db"])
    assert after[: len(first)] == first, "existing rows untouched"
    new = after[len(first):]
    assert len(new) >= 1 and all(n[0] == CANONICAL and n[1] == "pi" for n in new)
    assert any("exit code 2" in n[5] for n in new)
    assert f"{len(new)} new, {len(first)} already present" in r.output
    # a second signature row for the grown session, same key
    assert [p[0] for p in _processed(pi_env["db"])] == [CANONICAL, CANONICAL]


# ---------------------------------------------------------------------------
# long error text: the search parsers cap content at 2000 chars, the adapters
# do not — one error mined both ways must still be ONE row
# ---------------------------------------------------------------------------

LONG_TEXT = "".join(f"line {i:05d}: command failed with a very long message\n" for i in range(120))
assert len(LONG_TEXT) > 2000

LONG_ISERROR = _entry(
    "message", "e6", "e5", "2026-09-15T08:01:06.100Z",
    message={
        "role": "toolResult",
        "toolCallId": "call_long",
        "toolName": "bash",
        "content": [{"type": "text", "text": LONG_TEXT}],
        "isError": True,
    },
)


def _long_session(pi_env) -> None:
    from tests.unit.adapters.test_pi_adapter import ASSISTANT_CALL, HEADER, TOOL_OK, USER_MSG

    _write_session(pi_env["session"], [HEADER, USER_MSG, ASSISTANT_CALL, TOOL_OK, LONG_ISERROR])


@pytest.mark.parametrize("first", ["session", "agent"])
def test_long_error_mined_both_ways_is_one_row_with_the_full_text(pi_env, first):
    """One session with an error longer than the parsers' 2000-char cap, mined
    via --session (full text) AND --agent (capped text) in either order into
    ONE DB: exactly one row, holding the full text.

    The session GROWS between the two runs (a new assistant turn lands), so
    the second run re-reads it instead of skipping it as unchanged — that is
    how the live duplicates arose, and the fingerprint is what must hold."""
    from sio.cli.main import cli
    from tests.unit.adapters.test_pi_adapter import ASSISTANT_TEXT

    _long_session(pi_env)
    runner = CliRunner()
    cmds = {
        "session": ["mine", "--session", f"pi:{SESSION_UUID[:8]}"],
        "agent": ["mine", "--agent", "pi", "--since", "30 days"],
    }
    order = [first, "agent" if first == "session" else "session"]

    r1 = runner.invoke(cli, cmds[order[0]])
    assert r1.exit_code == 0, r1.output
    rows = _errors(pi_env["db"])
    assert [(r[0], r[3], r[4]) for r in rows] == [(CANONICAL, "tool_failure", "bash")]

    with pi_env["session"].open("a") as fh:
        fh.write(json.dumps(ASSISTANT_TEXT) + "\n")
    r2 = runner.invoke(cli, cmds[order[1]])
    assert r2.exit_code == 0, r2.output
    assert "(0 new, 1 already present)" in r2.output
    rows = _errors(pi_env["db"])
    assert len(rows) == 1, rows
    assert rows[0][5] == LONG_TEXT, "the full read wins whichever path ran first"


def test_short_error_mined_both_ways_still_one_row(pi_env):
    """Regression for text below the prefix length: nothing changed there."""
    from sio.cli.main import cli
    from tests.unit.adapters.test_pi_adapter import ASSISTANT_CALL, HEADER, TOOL_OK, USER_MSG

    _write_session(
        pi_env["session"], [HEADER, USER_MSG, ASSISTANT_CALL, TOOL_OK, EMPTY_ISERROR]
    )
    runner = CliRunner()
    assert runner.invoke(cli, ["mine", "--agent", "pi", "--since", "30 days"]).exit_code == 0
    r = runner.invoke(cli, ["mine", "--session", f"pi:{SESSION_UUID[:8]}"])
    assert r.exit_code == 0, r.output
    rows = _errors(pi_env["db"])
    assert [(r[4], r[5]) for r in rows] == [("read", "isError (empty tool result)")]
