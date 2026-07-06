"""Tests for the PromptChain harness reader (search_promptchain).

PromptChain is a TUI/CLI coding agent built on the PromptChain library. Its
transcripts live at ~/.promptchain/sessions/<uuid>/messages.jsonl (one JSON
object per turn: {role, content, timestamp, metadata}), with a sibling
sessions.db mapping <uuid> -> friendly session name.

These tests build a hermetic fixture under a temp HOME and assert the parser
finds matches, maps uuid->name, and tolerates PromptChain's quirks (stringified
epoch timestamp, Python-repr metadata string).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _make_fixture(home: Path) -> str:
    """Create ~/.promptchain/sessions/<uuid>/messages.jsonl + sessions.db.

    Returns the session uuid.
    """
    uuid = "0913de5f-f19c-4501-b3da-38550fd7090a"
    sessions = home / ".promptchain" / "sessions"
    sdir = sessions / uuid
    sdir.mkdir(parents=True)

    rows = [
        {
            "role": "user",
            "content": "Read /tmp/pc_codeblock.py with your file tools",
            "timestamp": "1782549688.710987",  # stringified epoch
            "metadata": "{}",
        },
        {
            "role": "assistant",
            "content": "The contents of /tmp/pc_codeblock.py are: def add(a, b)...",
            "timestamp": "1782549698.733568",
            # Python-repr string (single quotes — NOT valid JSON)
            "metadata": "{'agent_name': 'default', 'model_name': 'openai/gpt-4.1-mini'}",
        },
    ]
    with (sdir / "messages.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    # sessions.db: uuid -> friendly name "default"
    db = sessions / "sessions.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO sessions (id, name) VALUES (?, ?)", (uuid, "default"))
    conn.commit()
    conn.close()
    return uuid


class TestPromptChainParser:
    def test_registered_in_parsers(self):
        """The reader must be wired into PARSERS (drives --agent + 'all' + list)."""
        from sio.search.cli import PARSERS

        assert "promptchain" in PARSERS

    def test_finds_match_and_maps_session_name(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        uuid = _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_promptchain("codeblock", cs=False, cutoff=None))

        assert len(recs) == 2  # both turns mention codeblock
        for r in recs:
            assert r.agent == "promptchain"
            # uuid resolved to friendly name from sessions.db
            assert r.session_id == "default"
            assert r.metadata["uuid"] == uuid
            assert r.metadata["session_name"] == "default"
            assert r.source_path.endswith("messages.jsonl")

        # roles preserved, newest line has a parsed timestamp
        roles = {r.role for r in recs}
        assert roles == {"user", "assistant"}
        assert any(r.ts.startswith("2026-") for r in recs)

    def test_extracts_model_name_from_repr_metadata(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_promptchain("contents", cs=False, cutoff=None))
        assistant = [r for r in recs if r.role == "assistant"]
        assert assistant, "assistant turn should match 'contents'"
        assert assistant[0].metadata.get("model_name") == "openai/gpt-4.1-mini"

    def test_no_match_yields_nothing(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        assert list(_cli.search_promptchain("zzz-no-such-token", False, None)) == []

    def test_missing_root_is_safe(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        # No ~/.promptchain at all — must not raise.
        monkeypatch.setattr(_cli, "HOME", tmp_path)
        assert list(_cli.search_promptchain("anything", False, None)) == []

    def test_falls_back_to_uuid_without_db(self, tmp_path, monkeypatch):
        """If sessions.db is absent, session_id falls back to the dir uuid."""
        import sio.search.cli as _cli

        uuid = _make_fixture(tmp_path)
        (tmp_path / ".promptchain" / "sessions" / "sessions.db").unlink()
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_promptchain("codeblock", False, None))
        assert recs and all(r.session_id == uuid for r in recs)
        assert all(r.metadata["session_name"] == "" for r in recs)
