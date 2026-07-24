"""Unit tests for sio.cli.live — live-session discovery + tail rendering.

Covers the fiddly parts: JSONL tail/parsing helpers, human-readable content
extraction (tool_use / tool_result / fallback), age formatting, and the
discovery pipeline's sub-agent skip + session-id dedup + working-tree collision
flagging.
"""

from __future__ import annotations

import json

from sio.cli import live


class TestContentSnippet:
    def test_plain_text(self):
        entry = {"message": {"content": [{"type": "text", "text": "hello world"}]}}
        assert live._content_snippet(entry) == "hello world"

    def test_tool_use_shows_name_and_salient_arg(self):
        entry = {
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
                ]
            }
        }
        assert live._content_snippet(entry) == "Bash(ls -la)"

    def test_tool_result_text(self):
        entry = {
            "message": {
                "content": [
                    {"type": "tool_result", "content": [{"type": "text", "text": "ok done"}]}
                ]
            }
        }
        assert live._content_snippet(entry) == "ok done"

    def test_tooluseresult_fallback(self):
        entry = {"message": {"content": []}, "toolUseResult": {"stdout": "line1\nline2"}}
        assert live._content_snippet(entry) == "line1 line2"

    def test_empty_when_nothing_useful(self):
        assert live._content_snippet({"message": {"content": []}}) == ""


class TestTailReaders:
    def _write_jsonl(self, path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_tail_json_lines_returns_last_n(self, tmp_path):
        f = tmp_path / "s.jsonl"
        self._write_jsonl(f, [{"i": i} for i in range(50)])
        got = live._tail_json_lines(f, want=5)
        assert [e["i"] for e in got] == [45, 46, 47, 48, 49]

    def test_tail_json_lines_widens_past_small_chunk(self, tmp_path):
        f = tmp_path / "big.jsonl"
        # Each row padded so 5 rows exceed a tiny chunk, forcing a re-read.
        self._write_jsonl(f, [{"i": i, "pad": "x" * 200} for i in range(40)])
        got = live._tail_json_lines(f, want=5, chunk=128)
        assert [e["i"] for e in got] == [35, 36, 37, 38, 39]

    def test_tail_json_lines_skips_unparseable(self, tmp_path):
        f = tmp_path / "s.jsonl"
        f.write_text('{"a":1}\nNOT JSON\n{"a":2}\n', encoding="utf-8")
        got = live._tail_json_lines(f, want=5)
        assert [e["a"] for e in got] == [1, 2]

    def test_count_lines(self, tmp_path):
        f = tmp_path / "s.jsonl"
        self._write_jsonl(f, [{"i": i} for i in range(7)])
        assert live._count_lines(f) == 7


class TestFmtAge:
    def test_seconds(self, monkeypatch):
        monkeypatch.setattr(live.time, "time", lambda: 1000.0)
        assert live._fmt_age(1000.0 - 5) == "5s"

    def test_minutes(self, monkeypatch):
        monkeypatch.setattr(live.time, "time", lambda: 1000.0)
        assert live._fmt_age(1000.0 - 125) == "2m05s"

    def test_hours(self, monkeypatch):
        monkeypatch.setattr(live.time, "time", lambda: 10000.0)
        assert live._fmt_age(10000.0 - 3720) == "1h02m"


class TestDiscover:
    def _proj(self, root, name):
        d = root / f"-home-gyasis-Documents-code-{name}"
        d.mkdir(parents=True)
        return d

    def _session(self, path, sid, cwd):
        path.write_text(
            json.dumps({"type": "assistant", "sessionId": sid, "cwd": cwd,
                        "gitBranch": "main", "timestamp": "2026-07-03T00:00:00",
                        "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n",
            encoding="utf-8",
        )

    def _wire(self, monkeypatch, tmp_path, projects):
        # Point Claude at our fixture tree; silence the other harnesses (incl.
        # the real on-disk goose/opencode SQLite stores, so they can't leak
        # sessions into these assertions).
        monkeypatch.setattr(live, "CLAUDE_PROJECTS", projects)
        monkeypatch.setattr(live, "GOOSE_DB", tmp_path / "nope" / "sessions.db")
        monkeypatch.setattr(live, "OPENCODE_DB", tmp_path / "nope" / "opencode.db")
        for attr in ("CODEX_SESSIONS", "GEMINI_TMP", "KIMI_SESSIONS"):
            monkeypatch.setattr(live, attr, tmp_path / "nope" / attr)
        monkeypatch.setattr(live, "KIMI_SESSION_INDEX", tmp_path / "nope" / "index.jsonl")

    def test_skips_subagents_and_dedups_by_session_id(self, tmp_path, monkeypatch):
        projects = tmp_path / "projects"
        p = self._proj(projects, "cadastre")
        self._session(p / "aaaa1111.jsonl", "aaaa1111", "/home/x/code/cadastre")
        # Sub-agent transcript carrying the PARENT id — must NOT create a 2nd row.
        sub = p / "aaaa1111" / "subagents"
        sub.mkdir(parents=True)
        self._session(sub / "agent-9.jsonl", "aaaa1111", "/home/x/code/cadastre")
        self._wire(monkeypatch, tmp_path, projects)
        monkeypatch.setattr(
            live, "_repo_info",
            lambda cwd: {"toplevel": cwd, "common_dir": cwd + "/.git", "branch": "main"},
        )
        rows = live.discover_sessions(minutes=600)
        assert len(rows) == 1
        assert rows[0]["native_id"] == "aaaa1111"
        assert rows[0]["collision"] is False

    def test_flags_collision_on_shared_working_tree(self, tmp_path, monkeypatch):
        projects = tmp_path / "projects"
        p = self._proj(projects, "cadastre")
        self._session(p / "aaaa1111.jsonl", "aaaa1111", "/home/x/code/cadastre")
        self._session(p / "bbbb2222.jsonl", "bbbb2222", "/home/x/code/cadastre")
        # A third session in a different tree must stay collision-free.
        q = self._proj(projects, "SIO")
        self._session(q / "cccc3333.jsonl", "cccc3333", "/home/x/code/SIO")
        self._wire(monkeypatch, tmp_path, projects)
        monkeypatch.setattr(
            live, "_repo_info",
            lambda cwd: {"toplevel": cwd, "common_dir": cwd + "/.git", "branch": "main"},
        )
        rows = {r["native_id"]: r for r in live.discover_sessions(minutes=600)}
        assert rows["aaaa1111"]["collision"] is True
        assert rows["bbbb2222"]["collision"] is True
        assert rows["cccc3333"]["collision"] is False
