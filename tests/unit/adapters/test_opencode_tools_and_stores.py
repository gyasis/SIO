"""OpenCode adapter: tool parts, configurable stores, cross-store id uniqueness.

All databases are tiny SYNTHETIC sqlite files built under ``tmp_path``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sio.adapters.base import SessionManifest
from sio.adapters.opencode import adapter as oc
from sio.adapters.opencode.parsing import SessionSignals, part_events

SID = "ses_same_id"


def _tool(cmd: str, output: str, exit_code: int = 0, status: str = "completed") -> dict:
    state = {"status": status, "input": {"command": cmd}, "output": output,
             "metadata": {"exit": exit_code}}
    if status == "error":
        state = {"status": "error", "input": {"command": cmd}, "error": output}
    return {"type": "tool", "tool": "bash", "state": state}


def _make_store(root: Path, parts: list[dict], sid: str = SID) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, title TEXT, slug TEXT)")
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT, "
                 "time_created INTEGER)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
                 "data TEXT, time_created INTEGER)")
    conn.execute("INSERT INTO session VALUES (?, ?, ?, ?)", (sid, "/work/demo", "demo", "d"))
    conn.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                 ("m1", sid, json.dumps({"role": "assistant"}), 1784900000000))
    for i, p in enumerate(parts):
        conn.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?)",
                     (f"p{i}", "m1", sid, json.dumps(p), 1784900000100 + i))
    conn.commit()
    conn.close()
    return db


def _events(db: Path, native_id: str = SID):
    manifest = SessionManifest(agent="opencode", native_id=native_id, kind="db", path=str(db))
    return list(oc.OpenCodeAdapter().get_events(manifest))


class TestToolParts:
    def test_tool_call_and_output_are_events(self, tmp_path):
        db = _make_store(tmp_path / "a", [_tool("ls", "a.txt\n")])
        evs = _events(db)
        assert [(e.role, e.tool) for e in evs] == [("assistant", "bash"), ("tool", "bash")]
        assert evs[0].content == "ls"
        assert evs[1].content == "a.txt\n"
        assert evs[1].error is None

    def test_nonzero_exit_sets_error(self, tmp_path):
        db = _make_store(tmp_path / "a", [_tool("git diff abc", "fatal: bad revision", 128)])
        err = _events(db)[1].error
        assert err and "exit 128" in err and "fatal: bad revision" in err

    def test_fatal_line_in_output_with_exit_zero_sets_error(self, tmp_path):
        db = _make_store(tmp_path / "a", [_tool("make", "building\nerror: no rule\n", 0)])
        assert _events(db)[1].error

    def test_exit_1_with_no_output_is_not_a_failure(self, tmp_path):
        db = _make_store(tmp_path / "a", [_tool("grep nope f", "(no output)", 1)])
        assert _events(db)[1].error is None

    def test_errored_tool_state_sets_error(self, tmp_path):
        db = _make_store(tmp_path / "a", [_tool("curl x", "URL must start with http", status="error")])
        assert "URL must start" in (_events(db)[1].error or "")

    def test_long_output_is_truncated(self, tmp_path):
        db = _make_store(tmp_path / "a", [_tool("cat big", "x" * 50_000)])
        assert len(_events(db)[1].content) < 2_500

    def test_reasoning_and_step_parts_still_skipped(self, tmp_path):
        db = _make_store(tmp_path / "a", [{"type": "reasoning", "text": "hmm"},
                                          {"type": "step-start"}, {"type": "text", "text": "hi"}])
        assert [e.content for e in _events(db)] == ["hi"]


class TestSignals:
    def test_same_command_three_times_is_a_loop_once(self, tmp_path):
        parts = [_tool("ls -F src/", "ok") for _ in range(5)]
        db = _make_store(tmp_path / "a", parts)
        flagged = [e for e in _events(db) if e.error_type == "repeated_attempt"]
        assert len(flagged) == 1
        assert "3 times" in flagged[0].error

    def test_two_repeats_are_not_a_loop(self, tmp_path):
        db = _make_store(tmp_path / "a", [_tool("ls", "ok"), _tool("ls", "ok")])
        assert not [e for e in _events(db) if e.error_type]

    def test_whitespace_differences_still_count(self):
        sig = SessionSignals()
        out = []
        for cmd in ("ls  -F", "ls -F", "ls\t-F"):
            out += part_events(_tool(cmd, "ok"), "t", "assistant", sig)
        assert sum(1 for e in out if e.error_type == "repeated_attempt") == 1

    def test_template_token_leak_in_assistant_text(self):
        for tok in ("<channel|>", "<|channel|>", "<|im_start|>"):
            (ev,) = part_events({"type": "text", "text": f"done{tok}more"}, "t", "assistant")
            assert ev.error and tok in ev.error

    def test_no_leak_flag_for_user_text_or_plain_html(self):
        (u,) = part_events({"type": "text", "text": "<|im_start|>"}, "t", "user")
        (a,) = part_events({"type": "text", "text": "use <end> tags and <b>x</b>"}, "t", "assistant")
        assert u.error is None and a.error is None


class TestStores:
    def test_resolve_db_accepts_dir_file_and_xdg_root(self, tmp_path):
        db = _make_store(tmp_path / "s", [])
        assert oc.resolve_db(tmp_path / "s") == db
        assert oc.resolve_db(db) == db
        nested = _make_store(tmp_path / "root" / "opencode", [])
        assert oc.resolve_db(tmp_path / "root") == nested

    def test_configured_dbs_cli_replaces_default_env_adds(self, tmp_path, monkeypatch):
        a = _make_store(tmp_path / "a", [])
        b = _make_store(tmp_path / "b", [])
        monkeypatch.setattr(oc, "OPENCODE_DB", tmp_path / "default" / "opencode.db")
        monkeypatch.setenv("SIO_OPENCODE_DIRS", f"{tmp_path / 'a'}:{tmp_path / 'b'}")
        assert oc.configured_dbs(None) == [tmp_path / "default" / "opencode.db", a, b]
        assert oc.configured_dbs([str(tmp_path / "b")]) == [b]

    def test_same_session_id_in_two_stores_gets_distinct_native_ids(self, tmp_path, monkeypatch):
        a = _make_store(tmp_path / "a", [{"type": "text", "text": "from a"}])
        b = _make_store(tmp_path / "b", [{"type": "text", "text": "from b"}])
        monkeypatch.setattr(oc, "OPENCODE_DB", tmp_path / "nowhere" / "opencode.db")
        ma = oc.list_sessions(a)[0]
        mb = oc.list_sessions(b)[0]
        assert ma.native_id != mb.native_id
        assert ma.native_id.endswith(f":{SID}") and mb.native_id.endswith(f":{SID}")
        assert [e.content for e in oc.OpenCodeAdapter().get_events(ma)] == ["from a"]
        assert [e.content for e in oc.OpenCodeAdapter().get_events(mb)] == ["from b"]

    def test_default_store_keeps_bare_ids(self, tmp_path, monkeypatch):
        a = _make_store(tmp_path / "a", [])
        monkeypatch.setattr(oc, "OPENCODE_DB", a)
        assert oc.list_sessions(a)[0].native_id == SID

    def test_list_sessions_meta_and_cutoff(self, tmp_path):
        a = _make_store(tmp_path / "a", [])
        (m,) = oc.list_sessions(a)
        assert m.meta["directory"] == "/work/demo" and m.meta["title"] == "demo"
        assert oc.list_sessions(a, cutoff_epoch=1784900000 + 10_000) == []

    def test_handle_for_hashed_id_resolves_via_env(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        a = _make_store(tmp_path / "a", [])
        monkeypatch.setattr(oc, "OPENCODE_DB", tmp_path / "nowhere" / "opencode.db")
        monkeypatch.setattr(factory, "_OPENCODE_DB", tmp_path / "nowhere" / "opencode.db")
        monkeypatch.setenv("SIO_OPENCODE_DIRS", str(tmp_path / "a"))
        native = oc.list_sessions(a)[0].native_id
        m = factory.manifest_from_handle(f"opencode:{native}")
        assert m is not None and m.path == str(a)
