"""Tests for the Kimi Code CLI harness reader (search_kimi).

Kimi Code CLI (`kimi`) stores sessions at
~/.kimi-code/sessions/<workspace>/session_<uuid>/agents/<agent>/wire.jsonl —
one JSON object per line. Only ``context.append_message`` and ``turn.prompt``
records carry searchable content; everything else (loop events, llm.request,
usage.record, permission/tool/plan_mode bookkeeping) is noise and must be
skipped.

These tests build a hermetic fixture under a temp HOME and assert the parser
finds matches, derives the session id from the ``session_<uuid>`` directory
name (not the ever-present "wire" file stem), skips noise records, and
tolerates a missing ~/.kimi-code directory.
"""

from __future__ import annotations

import json
from pathlib import Path

SESSION_DIR = "session_217944be-6c81-491e-ab42-5cb1528de7fa"
WORKSPACE = "wd_code_2b52114291a4"


def _make_fixture(home: Path) -> Path:
    """Create a hermetic ~/.kimi-code/sessions/<workspace>/<session>/agents/main/wire.jsonl.

    Returns the wire.jsonl path.
    """
    agent_dir = home / ".kimi-code" / "sessions" / WORKSPACE / SESSION_DIR / "agents" / "main"
    agent_dir.mkdir(parents=True)
    wire = agent_dir / "wire.jsonl"

    rows = [
        {"type": "metadata", "protocol_version": "1.4", "created_at": 1784792289060},
        {"type": "config.update", "profileName": "agent"},
        {
            "type": "turn.prompt",
            "input": [{"type": "text", "text": "do you know what kodi is ?"}],
            "origin": {"kind": "user"},
            "time": 1784792423850,
        },
        {
            "type": "context.append_message",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "do you know what kodi is ?"}],
                "toolCalls": [],
                "origin": {"kind": "user"},
            },
            "time": 1784792423862,
        },
        {
            "type": "context.append_message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Yes, Kodi is media center software."}],
            },
            "time": "1784792430000",  # stringified epoch — must also parse
        },
        # Noise — must never surface as a match, even though it mentions "kodi".
        {
            "type": "context.append_loop_event",
            "event": {"note": "kodi kodi kodi step event, not a real message"},
            "time": 1784792425000,
        },
        {"type": "llm.request", "model": "kodi-model", "time": 1784792426000},
        {"type": "usage.record", "tokens": 123, "time": 1784792427000},
        {
            "type": "permission.record_approval_result",
            "tool": "kodi_tool",
            "time": 1784792428000,
        },
    ]
    with wire.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return wire


class TestKimiParser:
    def test_registered_in_parsers(self):
        """The reader must be wired into PARSERS (drives --agent + 'all' + list)."""
        from sio.search.cli import PARSERS

        assert "kimi" in PARSERS

    def test_registered_in_inventory(self):
        from sio.search.cli import inventory

        agents = {row[0] for row in inventory()}
        assert "kimi" in agents

    def test_finds_match_with_correct_session_and_role(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_kimi("kodi", cs=False, cutoff=None))

        # turn.prompt + 2x context.append_message all mention "kodi"
        assert len(recs) == 3
        for r in recs:
            assert r.agent == "kimi"
            # session_id derived from the session_<uuid> DIR, not the "wire" stem
            assert r.session_id == SESSION_DIR
            assert r.metadata["agent_dir"] == "main"
            assert r.metadata["source_kind"] == "kimi"
            assert r.source_path.endswith("wire.jsonl")

        roles = {r.role for r in recs}
        assert roles == {"user", "assistant"}

        # int-epoch-ms time converts to ISO-8601 UTC
        prompt_recs = [r for r in recs if r.role == "user" and "kodi" in r.content]
        assert any(r.ts.startswith("2026-") for r in prompt_recs)

        # stringified epoch-ms also parses correctly
        assistant = [r for r in recs if r.role == "assistant"]
        assert len(assistant) == 1
        assert assistant[0].ts.startswith("2026-")
        assert "media center" in assistant[0].content

    def test_noise_records_are_skipped(self, tmp_path, monkeypatch):
        """context.append_loop_event / llm.request / usage.record / permission.*
        must never surface as matches even though they contain the pattern."""
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_kimi("kodi", cs=False, cutoff=None))
        # Only the 3 real content records should match, never the noise rows
        # (loop event / llm.request / permission all also contain "kodi").
        assert len(recs) == 3
        for r in recs:
            assert "step event" not in r.content
            assert "kodi-model" not in r.content
            assert "kodi_tool" not in r.content

    def test_no_match_yields_nothing(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        assert list(_cli.search_kimi("zzz-no-such-token", False, None)) == []

    def test_missing_root_is_safe(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        # No ~/.kimi-code at all — must not raise.
        monkeypatch.setattr(_cli, "HOME", tmp_path)
        assert list(_cli.search_kimi("anything", False, None)) == []

    def test_empty_pattern_matches_every_content_record(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_kimi("", cs=False, cutoff=None))
        assert len(recs) == 3  # turn.prompt + 2x context.append_message

    def test_malformed_json_line_is_skipped(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        wire = _make_fixture(tmp_path)
        with wire.open("a") as fh:
            fh.write("{not valid json\n")

        monkeypatch.setattr(_cli, "HOME", tmp_path)
        recs = list(_cli.search_kimi("kodi", cs=False, cutoff=None))
        assert len(recs) == 3  # malformed trailing line contributes nothing
