"""Tests for the pi coding-agent harness reader (search_pi).

pi stores one session per file at
``~/.pi/agent/sessions/<cwd-dir>/<iso-ts>_<uuid>.jsonl`` — one JSON object per
line. The parser shares its normalisation with the EXTRACT adapter, so these
tests assert the search-side contract only: registration in PARSERS + the
inventory, the session id (= file stem), roles, ``metadata["tool"]`` on tool
calls/results, ``metadata["error"]`` on an ``isError`` result, noise skipped,
and a missing ~/.pi tree tolerated.

The fixture is SYNTHETIC — never a copy of a real transcript.
"""

from __future__ import annotations

import json
from pathlib import Path

SESSION_UUID = "0199a0b1-c2d3-7e4f-8a5b-6c7d8e9f0a1b"
STEM = f"2026-09-15T08-00-00-000Z_{SESSION_UUID}"


def _make_fixture(home: Path) -> Path:
    """Create a hermetic ~/.pi/agent/sessions/<cwd>/<stem>.jsonl. Returns its path."""
    d = home / ".pi" / "agent" / "sessions" / "--home-someone-code-demo--"
    d.mkdir(parents=True)
    fp = d / f"{STEM}.jsonl"
    ts = "2026-09-15T08:01:00.000Z"
    rows = [
        {"type": "session", "version": 3, "id": SESSION_UUID, "timestamp": ts,
         "cwd": "/home/someone/code/demo"},
        {"type": "model_change", "id": "e1", "parentId": None, "timestamp": ts,
         "provider": "local-provider", "modelId": "kodi-model"},
        {"type": "message", "id": "e2", "parentId": "e1", "timestamp": ts,
         "message": {"role": "user",
                     "content": [{"type": "text", "text": "do you know what kodi is ?"}]}},
        {"type": "message", "id": "e3", "parentId": "e2", "timestamp": ts,
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Yes, Kodi is media center software."},
             {"type": "toolCall", "id": "c1", "name": "bash",
              "arguments": {"command": "which kodi"}},
         ]}},
        {"type": "message", "id": "e4", "parentId": "e3", "timestamp": ts,
         "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "bash",
                     "content": [{"type": "text", "text": "kodi: command not found"}],
                     "isError": True}},
        # Noise — bookkeeping that mentions the pattern must never surface.
        {"type": "custom", "id": "e5", "parentId": "e4", "timestamp": ts,
         "customType": "kodi-ext", "data": {"note": "kodi kodi"}},
        {"type": "label", "id": "e6", "parentId": "e5", "timestamp": ts,
         "targetId": "e2", "label": "kodi bookmark"},
    ]
    with fp.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return fp


class TestPiParser:
    def test_registered_in_parsers(self):
        """The reader must be wired into PARSERS (drives --agent + 'all' + list)."""
        from sio.search.cli import PARSERS

        assert "pi" in PARSERS

    def test_registered_in_inventory(self):
        from sio.search.cli import inventory

        agents = {row[0] for row in inventory()}
        assert "pi" in agents

    def test_finds_match_with_correct_session_roles_and_metadata(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        fp = _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_pi("kodi", cs=False, cutoff=None))

        # model_change (system) + user + assistant text + toolCall + isError result
        assert len(recs) == 5
        for r in recs:
            assert r.agent == "pi"
            assert r.session_id == STEM  # the file stem, codex convention
            assert r.source_path == str(fp)
            assert r.metadata["source_kind"] == "pi"
            assert r.ts == "2026-09-15T08:01:00.000Z"
            assert r.line > 0

        assert {r.role for r in recs} == {"system", "user", "assistant", "tool"}

        call = [r for r in recs if r.role == "assistant" and "tool" in r.metadata]
        assert len(call) == 1 and call[0].metadata["tool"] == "bash"
        assert "which kodi" in call[0].content

        (result,) = [r for r in recs if r.role == "tool"]
        assert result.metadata["tool"] == "bash"
        assert result.metadata["error"] == "kodi: command not found"

    def test_noise_entries_are_skipped(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)

        recs = list(_cli.search_pi("kodi", cs=False, cutoff=None))
        assert all(r.metadata.get("entry_type") not in ("custom", "label") for r in recs)
        assert all("bookmark" not in r.content and "kodi-ext" not in r.content for r in recs)

    def test_no_match_yields_nothing(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)
        assert list(_cli.search_pi("zzz-no-such-token", False, None)) == []

    def test_missing_root_is_safe(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        monkeypatch.setattr(_cli, "HOME", tmp_path)  # no ~/.pi at all
        assert list(_cli.search_pi("anything", False, None)) == []

    def test_empty_pattern_matches_every_content_event(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        _make_fixture(tmp_path)
        monkeypatch.setattr(_cli, "HOME", tmp_path)
        recs = list(_cli.search_pi("", cs=False, cutoff=None))
        # header + model_change + user + assistant text + toolCall + result
        assert len(recs) == 6

    def test_malformed_json_line_is_skipped(self, tmp_path, monkeypatch):
        import sio.search.cli as _cli

        fp = _make_fixture(tmp_path)
        with fp.open("a") as fh:
            fh.write("{not valid json\n")
        monkeypatch.setattr(_cli, "HOME", tmp_path)
        assert len(list(_cli.search_pi("kodi", cs=False, cutoff=None))) == 5
