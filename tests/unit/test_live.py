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
        for attr in ("CODEX_SESSIONS", "GEMINI_TMP", "KIMI_SESSIONS", "PI_SESSIONS"):
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


class TestCodexDiscovery:
    """Regression: the codex glob was "rollout-*.json" (real files are
    .jsonl) matched non-recursively against a flat dir, while real files
    live nested under sessions/YYYY/MM/DD/ -- so codex sessions were NEVER
    discovered at any window (see live._recent, which IS recursive; only the
    extension was wrong).
    """

    def _wire(self, monkeypatch, tmp_path, codex_sessions):
        monkeypatch.setattr(live, "CLAUDE_PROJECTS", tmp_path / "nope" / "projects")
        monkeypatch.setattr(live, "GOOSE_DB", tmp_path / "nope" / "sessions.db")
        monkeypatch.setattr(live, "OPENCODE_DB", tmp_path / "nope" / "opencode.db")
        monkeypatch.setattr(live, "CODEX_SESSIONS", codex_sessions)
        monkeypatch.setattr(live, "GEMINI_TMP", tmp_path / "nope" / "gemini")
        monkeypatch.setattr(live, "KIMI_SESSIONS", tmp_path / "nope" / "kimi")
        monkeypatch.setattr(live, "KIMI_SESSION_INDEX", tmp_path / "nope" / "index.jsonl")
        monkeypatch.setattr(live, "PI_SESSIONS", tmp_path / "nope" / "pi")

    def test_discovers_nested_jsonl_rollout_and_reads_cwd(self, tmp_path, monkeypatch):
        codex_sessions = tmp_path / ".codex" / "sessions"
        day_dir = codex_sessions / "2026" / "09" / "13"
        day_dir.mkdir(parents=True)
        rollout = day_dir / "rollout-2026-09-13T11-53-49-01a09b79-8bf8-77c2-80be-56ff9aec6e56.jsonl"
        rollout.write_text(
            json.dumps(
                {
                    "timestamp": "2026-09-13T15:54:21.118Z",
                    "type": "session_meta",
                    "payload": {"cwd": "/home/x/code", "originator": "codex-tui"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "timestamp": "2026-09-13T15:54:22.451Z",
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self._wire(monkeypatch, tmp_path, codex_sessions)
        monkeypatch.setattr(
            live, "_repo_info",
            lambda cwd: {"toplevel": None, "common_dir": None, "branch": None},
        )

        rows = live.discover_sessions(minutes=600)
        assert len(rows) == 1
        row = rows[0]
        assert row["agent"] == "codex"
        assert row["native_id"] == "rollout-2026-09-13T11-53-49-01a09b79-8bf8-77c2-80be-56ff9aec6e56"
        assert row["cwd"] == "/home/x/code"
        assert row["msgs"] == 2

    def test_a_wrong_extension_file_is_never_matched(self, tmp_path, monkeypatch):
        codex_sessions = tmp_path / ".codex" / "sessions"
        day_dir = codex_sessions / "2026" / "09" / "13"
        day_dir.mkdir(parents=True)
        (day_dir / "rollout-2026-09-13T00-00-00-deadbeef.json").write_text("{}", encoding="utf-8")
        self._wire(monkeypatch, tmp_path, codex_sessions)
        monkeypatch.setattr(
            live, "_repo_info",
            lambda cwd: {"toplevel": None, "common_dir": None, "branch": None},
        )
        assert live.discover_sessions(minutes=600) == []


class _Ev:
    """Minimal SessionEvent stand-in (ts/role/content/tool/raw)."""

    def __init__(self, ts="2026-07-28T08:00:00Z", role="assistant", content="", tool=None, raw=None):
        self.ts, self.role, self.content, self.tool = ts, role, content, tool
        self.raw = raw or {}


class TestEventKind:
    def test_tool_wins_over_role(self):
        assert live._event_kind(_Ev(role="assistant", tool="Bash")) == "tool"

    def test_assistant_prose_is_text(self):
        assert live._event_kind(_Ev(role="assistant")) == "text"

    def test_user_and_harness_noise(self):
        assert live._event_kind(_Ev(role="user")) == "user"
        assert live._event_kind(_Ev(role="attachment")) == "system"


class TestResolveKinds:
    def test_none_means_no_filter(self):
        assert live._resolve_kinds(None, False, False) is None

    def test_aliases_compose_into_a_set(self):
        assert live._resolve_kinds(None, True, True) == {"tool", "text"}

    def test_only_is_comma_separated_and_composes_with_aliases(self):
        assert live._resolve_kinds("user", True, False) == {"user", "tool"}

    def test_all_disables_filtering(self):
        assert live._resolve_kinds("all", True, False) is None

    def test_unknown_kind_raises(self):
        import click
        import pytest

        with pytest.raises(click.ClickException):
            live._resolve_kinds("bogus", False, False)


class TestLiveFilter:
    def test_kind_filter(self):
        f = live.LiveFilter(kinds={"text"})
        assert f.passes(_Ev(role="assistant"), "prose")
        assert not f.passes(_Ev(role="assistant", tool="Bash"), "Bash(ls)")

    def test_since_is_exclusive_until_is_inclusive(self):
        f = live.LiveFilter(since="2026-07-28T08:00:00Z", until="2026-07-28T09:00:00Z")
        assert not f.passes(_Ev(ts="2026-07-28T08:00:00Z"), "x")  # == since → excluded
        assert f.passes(_Ev(ts="2026-07-28T08:30:00Z"), "x")
        assert f.passes(_Ev(ts="2026-07-28T09:00:00Z"), "x")  # == until → included
        assert not f.passes(_Ev(ts="2026-07-28T09:00:01Z"), "x")

    def test_blank_bodies_dropped_by_default_but_tools_kept(self):
        f = live.LiveFilter()
        assert not f.passes(_Ev(role="attachment"), "")
        assert f.passes(_Ev(role="assistant", tool="Bash"), "")  # tool name is signal
        assert live.LiveFilter(include_blank=True).passes(_Ev(role="attachment"), "")

    def test_grep_matches_body_or_tool_name(self):
        import re

        f = live.LiveFilter(grep=re.compile("episode", re.IGNORECASE))
        assert f.passes(_Ev(), "ran EPISODE_audio.py")
        assert not f.passes(_Ev(), "unrelated body")


class TestFilterBeforeTruncate:
    """Regression: filtering AFTER truncation silently emptied busy sessions."""

    def test_tail_keeps_n_matching_events_not_n_raw_events(self):
        # 20 trailing harness-noise records after the only 2 tool calls — the
        # old code took the last 5 raw records (all noise) and emitted nothing.
        events = [_Ev(ts=f"2026-07-28T08:00:{i:02d}Z", role="assistant", tool="Bash") for i in range(2)]
        events += [_Ev(ts=f"2026-07-28T08:01:{i:02d}Z", role="attachment") for i in range(20)]
        f = live.LiveFilter(kinds={"tool"})
        kept = [e for e in events if f.passes(e, "")]
        assert len(kept) == 2
        assert all(e.tool == "Bash" for e in kept)


class TestCursorStore:
    """Persistence so a /compact in the READING agent can't lose the resume point."""

    def test_roundtrip_and_isolation_by_handle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        assert live._get_cursor("claude:aaa") is None
        live._save_cursor("claude:aaa", "2026-07-28T08:00:00Z")
        live._save_cursor("claude:bbb", "2026-07-28T09:00:00Z")
        assert live._get_cursor("claude:aaa") == "2026-07-28T08:00:00Z"
        assert live._get_cursor("claude:bbb") == "2026-07-28T09:00:00Z"

    def test_corrupt_store_degrades_instead_of_raising(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        (tmp_path / "live-cursors.json").write_text("NOT JSON {{{")
        assert live._load_cursors() == {}
        assert live._get_cursor("claude:aaa") is None

    def test_empty_values_are_not_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        live._save_cursor("claude:aaa", "")
        live._save_cursor("", "2026-07-28T08:00:00Z")
        assert live._load_cursors() == {}

    def test_store_is_bounded_and_evicts_oldest(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        for i in range(live._CURSOR_KEEP + 25):
            live._save_cursor(f"claude:{i:04d}", f"2026-07-28T08:00:{i % 60:02d}Z")
        assert len(live._load_cursors()) <= live._CURSOR_KEEP

    def test_write_is_atomic_no_tmp_left_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        live._save_cursor("claude:aaa", "2026-07-28T08:00:00Z")
        assert not list(tmp_path.glob("*.tmp.*"))
        assert (tmp_path / "live-cursors.json").exists()

    def test_sio_home_env_overrides_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        assert live._cursor_path() == tmp_path / "live-cursors.json"
        monkeypatch.delenv("SIO_HOME")
        assert live._cursor_path().parent.name == ".sio"


class TestResolveSince:
    def test_explicit_since_beats_resume(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        live._save_cursor("claude:aaa", "2026-07-28T08:00:00Z")
        assert live._resolve_since("claude:aaa", "2026-07-28T10:00:00Z", True) == (
            "2026-07-28T10:00:00Z"
        )

    def test_resume_reads_stored_cursor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        live._save_cursor("claude:aaa", "2026-07-28T08:00:00Z")
        assert live._resolve_since("claude:aaa", None, True) == "2026-07-28T08:00:00Z"

    def test_resume_without_stored_cursor_reads_from_start(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        assert live._resolve_since("claude:aaa", None, True) is None

    def test_no_resume_no_since_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SIO_HOME", str(tmp_path))
        live._save_cursor("claude:aaa", "2026-07-28T08:00:00Z")
        assert live._resolve_since("claude:aaa", None, False) is None


class TestPiDiscovery:
    """pi sessions live at ~/.pi/agent/sessions/<cwd-dir>/<iso-ts>_<uuid>.jsonl;
    discovery reads cwd from the line-1 header and uses the file stem as the
    native id (the same convention codex uses).
    """

    def _wire(self, monkeypatch, tmp_path, pi_sessions):
        monkeypatch.setattr(live, "CLAUDE_PROJECTS", tmp_path / "nope" / "projects")
        monkeypatch.setattr(live, "GOOSE_DB", tmp_path / "nope" / "sessions.db")
        monkeypatch.setattr(live, "OPENCODE_DB", tmp_path / "nope" / "opencode.db")
        for attr in ("CODEX_SESSIONS", "GEMINI_TMP", "KIMI_SESSIONS"):
            monkeypatch.setattr(live, attr, tmp_path / "nope" / attr)
        monkeypatch.setattr(live, "KIMI_SESSION_INDEX", tmp_path / "nope" / "index.jsonl")
        monkeypatch.setattr(live, "PI_SESSIONS", pi_sessions)
        monkeypatch.setattr(
            live, "_repo_info",
            lambda cwd: {"toplevel": None, "common_dir": None, "branch": None},
        )

    def test_discovers_session_and_reads_cwd_from_header(self, tmp_path, monkeypatch):
        pi_sessions = tmp_path / ".pi" / "agent" / "sessions"
        cwd_dir = pi_sessions / "--home-x-code--"
        cwd_dir.mkdir(parents=True)
        stem = "2026-09-15T08-00-00-000Z_0199a0b1-c2d3-7e4f-8a5b-6c7d8e9f0a1b"
        (cwd_dir / f"{stem}.jsonl").write_text(
            json.dumps({
                "type": "session", "version": 3,
                "id": "0199a0b1-c2d3-7e4f-8a5b-6c7d8e9f0a1b",
                "timestamp": "2026-09-15T08:00:00.000Z", "cwd": "/home/x/code",
            })
            + "\n"
            + json.dumps({
                "type": "message", "id": "e1", "parentId": None,
                "timestamp": "2026-09-15T08:01:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            })
            + "\n",
            encoding="utf-8",
        )
        self._wire(monkeypatch, tmp_path, pi_sessions)

        rows = live.discover_sessions(minutes=600)
        assert len(rows) == 1
        row = rows[0]
        assert row["agent"] == "pi"
        assert row["native_id"] == stem
        assert row["cwd"] == "/home/x/code"
        assert row["msgs"] == 2

    def test_missing_pi_tree_is_safe(self, tmp_path, monkeypatch):
        self._wire(monkeypatch, tmp_path, tmp_path / "nope" / "pi")
        assert live.discover_sessions(minutes=600) == []
