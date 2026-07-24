"""Tests for the OpenCode CLI harness reader (search_opencode).

OpenCode stores its transcripts in SQLite at
~/.local/share/opencode/opencode.db — `message` holds one row per turn (id,
session_id, data JSON with a top-level `role`, time_created epoch
MILLISECONDS); `part` holds a message's sub-parts (data JSON; only
`{"type":"text",...}` parts carry searchable content — reasoning / tool /
step-start / step-finish parts are noise and must never surface as matches).

These tests build a hermetic SQLite fixture under tmp_path, monkeypatch the
module's OPENCODE_DB constant, and assert the parser finds matches with the
correct role/content/session_id/ts, skips non-text parts, filters by cutoff,
and tolerates a missing database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SESSION_ID = "ses_06c3abcdef"


def _make_opencode_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, title TEXT, "
        "slug TEXT)"
    )
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT, "
        "time_created INTEGER)"
    )
    conn.execute(
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "data TEXT, time_created INTEGER)"
    )
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?)",
        (SESSION_ID, "/home/x/code/demo", "demo session", "demo-slug"),
    )
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        ("msg_1", SESSION_ID, json.dumps({"role": "user"}), 1784900000000),
    )
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        ("msg_2", SESSION_ID, json.dumps({"role": "assistant"}), 1784900010000),
    )
    parts = [
        (
            "part_1",
            "msg_1",
            SESSION_ID,
            json.dumps({"type": "text", "text": "do you know what kodi is?"}),
            1784900000500,
        ),
        # Noise — reasoning part mentions "kodi" but must never surface.
        (
            "part_2",
            "msg_2",
            SESSION_ID,
            json.dumps({"type": "reasoning", "text": "kodi kodi kodi internal thought"}),
            1784900010200,
        ),
        (
            "part_3",
            "msg_2",
            SESSION_ID,
            json.dumps({"type": "text", "text": "yes, kodi is media center software"}),
            1784900010500,
        ),
        # Noise — tool part, no "text" field at all.
        (
            "part_4",
            "msg_2",
            SESSION_ID,
            json.dumps({"type": "tool", "tool": "bash", "input": {"command": "ls"}}),
            1784900010700,
        ),
    ]
    conn.executemany("INSERT INTO part VALUES (?, ?, ?, ?, ?)", parts)
    conn.commit()
    conn.close()


class TestOpenCodeParser:
    def test_registered_in_parsers(self):
        from sio.search.cli import PARSERS

        assert "opencode" in PARSERS

    def test_registered_in_inventory(self):
        from sio.search.cli import inventory

        agents = {row[0] for row in inventory()}
        assert "opencode" in agents

    def test_finds_match_with_correct_session_and_role(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "opencode.db"
        _make_opencode_db(db_path)
        monkeypatch.setattr(_cli, "OPENCODE_DB", db_path)

        recs = list(_cli.search_opencode("kodi", cs=False, cutoff=None))

        assert len(recs) == 2  # only the two TEXT parts, never reasoning/tool
        for r in recs:
            assert r.agent == "opencode"
            assert r.session_id == SESSION_ID
            assert r.metadata["source_kind"] == "sqlite"
            assert r.source_path == str(db_path)

        roles = {r.role for r in recs}
        assert roles == {"user", "assistant"}

        assistant = [r for r in recs if r.role == "assistant"]
        assert len(assistant) == 1
        assert "media center" in assistant[0].content
        assert assistant[0].ts.startswith("2026-")

    def test_non_text_parts_are_skipped(self, tmp_path, monkeypatch):
        """reasoning/tool parts must never surface even though they mention
        the pattern."""
        import sio.search.cli as _cli

        db_path = tmp_path / "opencode.db"
        _make_opencode_db(db_path)
        monkeypatch.setattr(_cli, "OPENCODE_DB", db_path)

        recs = list(_cli.search_opencode("kodi", cs=False, cutoff=None))
        assert len(recs) == 2
        for r in recs:
            assert "internal thought" not in r.content

    def test_no_match_yields_nothing(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "opencode.db"
        _make_opencode_db(db_path)
        monkeypatch.setattr(_cli, "OPENCODE_DB", db_path)

        assert list(_cli.search_opencode("zzz-no-such-token", False, None)) == []

    def test_missing_db_is_safe(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        monkeypatch.setattr(_cli, "OPENCODE_DB", tmp_path / "no-such.db")
        assert list(_cli.search_opencode("anything", False, None)) == []

    def test_cutoff_filters_by_row_timestamp(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "opencode.db"
        _make_opencode_db(db_path)
        monkeypatch.setattr(_cli, "OPENCODE_DB", db_path)

        # cutoff (epoch SECONDS) between the first (…000.5) and second (…010.5)
        # "kodi" text parts (times are epoch ms) — only the assistant part
        # should survive.
        cutoff = 1784900005.0
        recs = list(_cli.search_opencode("kodi", cs=False, cutoff=cutoff))
        assert len(recs) == 1
        assert recs[0].role == "assistant"

        assert list(_cli.search_opencode("kodi", cs=False, cutoff=1784900999.0)) == []

    def test_empty_pattern_matches_every_text_part(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "opencode.db"
        _make_opencode_db(db_path)
        monkeypatch.setattr(_cli, "OPENCODE_DB", db_path)

        recs = list(_cli.search_opencode("", cs=False, cutoff=None))
        assert len(recs) == 2  # only the 2 text parts; reasoning/tool never match
