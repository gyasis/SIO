"""Tests for the Goose CLI harness reader (search_goose).

Goose (`goose session`) stores its transcripts in SQLite (WAL mode) at
~/.local/share/goose/sessions/sessions.db — `messages` holds one row per turn
(session_id, role, content_json, created_timestamp epoch SECONDS); `sessions`
maps id -> working_dir/updated_at/name. content_json is a JSON array of
content blocks (join the `text` of `{"type":"text",...}` blocks).

These tests build a hermetic SQLite fixture under tmp_path, monkeypatch the
module's GOOSE_DB constant, and assert the parser finds matches with the
correct role/content/session_id/ts, filters by cutoff, and tolerates a
missing database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SESSION_ID = "20260724_1"


def _make_goose_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, working_dir TEXT, "
        "updated_at TIMESTAMP, name TEXT)"
    )
    conn.execute(
        "CREATE TABLE messages (session_id TEXT, role TEXT, content_json TEXT, "
        "created_timestamp INTEGER, message_id TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        (SESSION_ID, "/home/x/code/demo", "2026-07-24T00:00:00Z", "demo session"),
    )
    rows = [
        (
            SESSION_ID,
            "user",
            json.dumps([{"type": "text", "text": "do you know what kodi is?"}]),
            1784900000,
            "m1",
        ),
        (
            SESSION_ID,
            "assistant",
            json.dumps([{"type": "text", "text": "yes, kodi is media center software"}]),
            1784900010,
            "m2",
        ),
        (
            SESSION_ID,
            "user",
            json.dumps([{"type": "text", "text": "totally unrelated message"}]),
            1784900020,
            "m3",
        ),
    ]
    conn.executemany(
        "INSERT INTO messages (session_id, role, content_json, created_timestamp, "
        "message_id) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class TestGooseParser:
    def test_registered_in_parsers(self):
        from sio.search.cli import PARSERS

        assert "goose" in PARSERS

    def test_registered_in_inventory(self):
        from sio.search.cli import inventory

        agents = {row[0] for row in inventory()}
        assert "goose" in agents

    def test_finds_match_with_correct_session_and_role(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "sessions.db"
        _make_goose_db(db_path)
        monkeypatch.setattr(_cli, "GOOSE_DB", db_path)

        recs = list(_cli.search_goose("kodi", cs=False, cutoff=None))

        assert len(recs) == 2
        for r in recs:
            assert r.agent == "goose"
            assert r.session_id == SESSION_ID
            assert r.metadata["source_kind"] == "sqlite"
            assert r.source_path == str(db_path)

        roles = {r.role for r in recs}
        assert roles == {"user", "assistant"}

        assistant = [r for r in recs if r.role == "assistant"]
        assert len(assistant) == 1
        assert "media center" in assistant[0].content
        assert assistant[0].ts.startswith("2026-")

    def test_no_match_yields_nothing(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "sessions.db"
        _make_goose_db(db_path)
        monkeypatch.setattr(_cli, "GOOSE_DB", db_path)

        assert list(_cli.search_goose("zzz-no-such-token", False, None)) == []

    def test_missing_db_is_safe(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        monkeypatch.setattr(_cli, "GOOSE_DB", tmp_path / "no-such-sessions.db")
        assert list(_cli.search_goose("anything", False, None)) == []

    def test_cutoff_filters_by_row_timestamp(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "sessions.db"
        _make_goose_db(db_path)
        monkeypatch.setattr(_cli, "GOOSE_DB", db_path)

        # cutoff after the first "kodi" row (1784900000) but before the
        # second (1784900010) — only the assistant row should survive.
        recs = list(_cli.search_goose("kodi", cs=False, cutoff=1784900005))
        assert len(recs) == 1
        assert recs[0].role == "assistant"

        # A cutoff after everything yields nothing.
        assert list(_cli.search_goose("kodi", cs=False, cutoff=1784900999)) == []

    def test_empty_pattern_matches_every_content_record(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        db_path = tmp_path / "sessions.db"
        _make_goose_db(db_path)
        monkeypatch.setattr(_cli, "GOOSE_DB", db_path)

        recs = list(_cli.search_goose("", cs=False, cutoff=None))
        assert len(recs) == 3
