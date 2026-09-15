"""Tests for the pi coding-agent EXTRACT adapter (PiAdapter) + factory wiring.

Covers: events_from_entry() normalises the session header, user text,
assistant text / thinking / toolCall blocks (in block order), successful and
``isError`` toolResults (the latter sets SessionEvent.error), pi's
bashExecution / model_change / thinking_level_change / compaction entries,
and drops ``custom`` / ``label`` bookkeeping; get_events() streams a file;
manifest_from_handle("pi:<id>") resolves both the full ``<iso-ts>_<uuid>``
stem AND a bare/partial uuid to the same file; from_path() maps a session
path to a ``pi:`` handle; adapter_for("pi") returns a PiAdapter; and a short
get_live_stream() smoke test (tail an appended line with a thread + timeout,
so it can't hang the suite).

The fixture is SYNTHETIC (shapes from pi's session-manager.d.ts / messages.d.ts,
content invented) -- never a copy of a real transcript.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

SESSION_UUID = "0199a0b1-c2d3-7e4f-8a5b-6c7d8e9f0a1b"
STEM = f"2026-09-15T08-00-00-000Z_{SESSION_UUID}"
CWD_DIR = "--home-someone-code-demo--"


def _session_dir(home: Path) -> Path:
    d = home / ".pi" / "agent" / "sessions" / CWD_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_session(path: Path, rows: list[dict]) -> None:
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _entry(etype: str, eid: str, parent: str | None, ts: str, **extra) -> dict:
    return {"type": etype, "id": eid, "parentId": parent, "timestamp": ts, **extra}


HEADER = {
    "type": "session",
    "version": 3,
    "id": SESSION_UUID,
    "timestamp": "2026-09-15T08:00:00.000Z",
    "cwd": "/home/someone/code/demo",
}
MODEL_CHANGE = _entry(
    "model_change", "e1", None, "2026-09-15T08:00:00.100Z",
    provider="local-provider", modelId="demo-model:7b",
)
THINKING_LEVEL = _entry(
    "thinking_level_change", "e2", "e1", "2026-09-15T08:00:00.100Z", thinkingLevel="minimal",
)
USER_MSG = _entry(
    "message", "e3", "e2", "2026-09-15T08:01:00.000Z",
    message={
        "role": "user",
        "content": [{"type": "text", "text": "please list the demo directory"}],
        "timestamp": 1789459260000,
    },
)
ASSISTANT_CALL = _entry(
    "message", "e4", "e3", "2026-09-15T08:01:05.000Z",
    message={
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "I should run ls.", "thinkingSignature": "reasoning"},
            {"type": "text", "text": "Listing it now."},
            {
                "type": "toolCall",
                "id": "call_ok",
                "name": "bash",
                "arguments": {"command": "ls demo"},
            },
            {
                "type": "toolCall",
                "id": "call_bad",
                "name": "read",
                "arguments": {"path": "demo/missing.txt"},
            },
        ],
        "api": "openai-completions",
        "provider": "local-provider",
        "model": "demo-model:7b",
        "stopReason": "toolUse",
        "timestamp": 1789459265000,
    },
)
TOOL_OK = _entry(
    "message", "e5", "e4", "2026-09-15T08:01:06.000Z",
    message={
        "role": "toolResult",
        "toolCallId": "call_ok",
        "toolName": "bash",
        "content": [{"type": "text", "text": "a.txt\nb.txt"}],
        "isError": False,
        "timestamp": 1789459266000,
    },
)
TOOL_ERR = _entry(
    "message", "e6", "e5", "2026-09-15T08:01:06.100Z",
    message={
        "role": "toolResult",
        "toolCallId": "call_bad",
        "toolName": "read",
        "content": [{"type": "text", "text": "ENOENT: no such file demo/missing.txt"}],
        "isError": True,
        "timestamp": 1789459266100,
    },
)
ASSISTANT_TEXT = _entry(
    "message", "e7", "e6", "2026-09-15T08:01:10.000Z",
    message={
        "role": "assistant",
        "content": [{"type": "text", "text": "Two files; "}, {"type": "text", "text": "one was missing."}],
        "api": "openai-completions",
        "provider": "local-provider",
        "model": "demo-model:7b",
        "stopReason": "stop",
        "timestamp": 1789459270000,
    },
)
BASH_EXEC = _entry(
    "message", "e8", "e7", "2026-09-15T08:02:00.000Z",
    message={
        "role": "bashExecution",
        "command": "false",
        "output": "",
        "exitCode": 1,
        "cancelled": False,
        "truncated": False,
        "timestamp": 1789459320000,
    },
)
COMPACTION = _entry(
    "compaction", "e9", "e8", "2026-09-15T08:03:00.000Z",
    summary="earlier we listed a directory", firstKeptEntryId="e7", tokensBefore=1000,
)
CUSTOM = _entry("custom", "e10", "e9", "2026-09-15T08:03:01.000Z", customType="x-ext", data={"k": 1})
LABEL = _entry("label", "e11", "e10", "2026-09-15T08:03:02.000Z", targetId="e7", label="bookmark")

BASE_ROWS = [
    HEADER, MODEL_CHANGE, THINKING_LEVEL, USER_MSG, ASSISTANT_CALL, TOOL_OK, TOOL_ERR,
    ASSISTANT_TEXT, BASH_EXEC, COMPACTION, CUSTOM, LABEL,
]


def _events(row: dict, call_names: dict | None = None) -> list:
    from sio.adapters.pi.adapter import events_from_entry

    return list(events_from_entry(row, call_names if call_names is not None else {}))


class TestEventsFromEntry:
    def test_header_maps_to_system_with_cwd(self):
        (ev,) = _events(HEADER)
        assert ev.role == "system"
        assert ev.tool is None
        assert "/home/someone/code/demo" in ev.content
        assert ev.ts == "2026-09-15T08:00:00.000Z"

    def test_model_and_thinking_level_changes_are_system(self):
        (m,) = _events(MODEL_CHANGE)
        (t,) = _events(THINKING_LEVEL)
        assert m.role == "system" and "local-provider/demo-model:7b" in m.content
        assert t.role == "system" and "minimal" in t.content

    def test_user_message(self):
        (ev,) = _events(USER_MSG)
        assert ev.role == "user"
        assert ev.content == "please list the demo directory"
        assert ev.tool is None and ev.error is None

    def test_user_message_with_string_content(self):
        row = _entry(
            "message", "x", None, "2026-09-15T08:00:00.000Z",
            message={"role": "user", "content": "plain string prompt", "timestamp": 1},
        )
        (ev,) = _events(row)
        assert ev.role == "user" and ev.content == "plain string prompt"

    def test_assistant_blocks_expand_in_order(self):
        call_names: dict = {}
        evs = _events(ASSISTANT_CALL, call_names)
        assert [(e.role, e.tool) for e in evs] == [
            ("system", None),  # thinking
            ("assistant", None),  # text
            ("assistant", "bash"),  # toolCall
            ("assistant", "read"),  # toolCall
        ]
        assert evs[0].content == "I should run ls."
        assert evs[1].content == "Listing it now."
        assert json.loads(evs[2].content) == {"command": "ls demo"}
        assert all(e.error is None for e in evs)
        # call id -> name remembered for a toolResult that omits toolName
        assert call_names == {"call_ok": "bash", "call_bad": "read"}

    def test_consecutive_text_blocks_merge_into_one_event(self):
        (ev,) = _events(ASSISTANT_TEXT)
        assert ev.role == "assistant"
        assert ev.content == "Two files;  one was missing."

    def test_tool_result_ok_has_no_error(self):
        (ev,) = _events(TOOL_OK)
        assert ev.role == "tool"
        assert ev.tool == "bash"
        assert ev.content == "a.txt\nb.txt"
        assert ev.error is None

    def test_tool_result_is_error_sets_error(self):
        (ev,) = _events(TOOL_ERR)
        assert ev.role == "tool"
        assert ev.tool == "read"
        assert ev.error == "ENOENT: no such file demo/missing.txt"
        assert ev.content == ev.error

    def test_tool_result_without_tool_name_falls_back_to_call_map(self):
        row = _entry(
            "message", "x", None, "2026-09-15T08:00:00.000Z",
            message={
                "role": "toolResult", "toolCallId": "call_ok",
                "content": [{"type": "text", "text": "ok"}], "isError": False,
            },
        )
        (ev,) = _events(row, {"call_ok": "bash"})
        assert ev.tool == "bash"
        (ev2,) = _events(row, {})
        assert ev2.tool == "tool"

    def test_bash_execution_is_user_command_plus_tool_result(self):
        evs = _events(BASH_EXEC)
        assert [(e.role, e.tool) for e in evs] == [("user", None), ("tool", "bash")]
        assert evs[0].content == "!false"
        assert evs[1].error == "exit code 1"  # non-zero exit = failure

    def test_bash_execution_zero_exit_is_not_an_error(self):
        row = dict(BASH_EXEC, message=dict(BASH_EXEC["message"], exitCode=0, output="hi"))
        _, out = _events(row)
        assert out.error is None and out.content == "hi"

    def test_compaction_is_system_summary(self):
        (ev,) = _events(COMPACTION)
        assert ev.role == "system"
        assert ev.content == "earlier we listed a directory"

    def test_custom_and_label_are_dropped(self):
        assert _events(CUSTOM) == []
        assert _events(LABEL) == []

    def test_unknown_entry_type_is_dropped(self):
        assert _events(_entry("something_new", "z", None, "2026-01-01T00:00:00Z")) == []


class TestPiAdapterGetEvents:
    def _manifest(self, path: Path):
        from sio.adapters.base import SessionManifest

        return SessionManifest(agent="pi", native_id=STEM, kind="file", path=str(path))

    def test_get_events_yields_normalised_stream_in_order(self, tmp_path):
        from sio.adapters.pi.adapter import PiAdapter

        session = _session_dir(tmp_path) / f"{STEM}.jsonl"
        _write_session(session, BASE_ROWS)
        events = list(PiAdapter().get_events(self._manifest(session)))

        roles = [e.role for e in events]
        # header, model_change, thinking_level, user, thinking, text, 2 calls,
        # 2 results, assistant text, bash cmd + result, compaction (custom/label dropped)
        assert roles == [
            "system", "system", "system", "user", "system", "assistant", "assistant",
            "assistant", "tool", "tool", "assistant", "user", "tool", "system",
        ]
        errors = [e for e in events if e.error is not None]
        assert [(e.tool, e.error) for e in errors] == [
            ("read", "ENOENT: no such file demo/missing.txt"),
            ("bash", "exit code 1"),
        ]

    def test_skips_blank_and_malformed_lines(self, tmp_path):
        from sio.adapters.pi.adapter import PiAdapter

        session = _session_dir(tmp_path) / f"{STEM}.jsonl"
        _write_session(session, BASE_ROWS)
        with session.open("a") as fh:
            fh.write("\n{not valid json\n")
        events = list(PiAdapter().get_events(self._manifest(session)))
        assert len(events) == 14


class TestFactoryWiring:
    def test_adapter_for_pi_returns_pi_adapter(self):
        from sio.adapters.factory import adapter_for
        from sio.adapters.pi.adapter import PiAdapter

        adapter = adapter_for("pi")
        assert isinstance(adapter, PiAdapter)
        assert adapter.agent == "pi"

    def test_pi_is_a_known_agent_for_handles(self):
        from sio.core.session_handle import KNOWN_AGENTS, parse_handle

        assert "pi" in KNOWN_AGENTS
        assert parse_handle(f"pi:{SESSION_UUID}") == ("pi", SESSION_UUID)

    def test_from_path_maps_session_file_to_pi_handle(self, tmp_path):
        from sio.core.session_handle import coerce_session_input, from_path

        session = _session_dir(tmp_path) / f"{STEM}.jsonl"
        assert from_path(str(session)) == f"pi:{STEM}"
        assert coerce_session_input(str(session)) == f"pi:{STEM}"

    def test_manifest_from_handle_resolves_full_stem(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        session = _session_dir(tmp_path) / f"{STEM}.jsonl"
        _write_session(session, BASE_ROWS)
        monkeypatch.setattr(factory, "_PI_SESSIONS", tmp_path / ".pi" / "agent" / "sessions")

        manifest = factory.manifest_from_handle(f"pi:{STEM}")
        assert manifest is not None
        assert manifest.agent == "pi"
        assert manifest.native_id == STEM
        assert manifest.path == str(session)
        assert manifest.encoding == "jsonl"

    def test_manifest_from_handle_resolves_bare_uuid_to_same_file(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        session = _session_dir(tmp_path) / f"{STEM}.jsonl"
        _write_session(session, BASE_ROWS)
        monkeypatch.setattr(factory, "_PI_SESSIONS", tmp_path / ".pi" / "agent" / "sessions")

        for native in (SESSION_UUID, SESSION_UUID[:8]):
            manifest = factory.manifest_from_handle(f"pi:{native}")
            assert manifest is not None, native
            # Normalised to the real file's stem, so every form shares one cursor.
            assert manifest.native_id == STEM
            assert manifest.path == str(session)

    def test_manifest_from_handle_missing_session_returns_none(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        monkeypatch.setattr(factory, "_PI_SESSIONS", tmp_path / ".pi" / "agent" / "sessions")
        assert factory.manifest_from_handle("pi:no-such-session") is None


class TestGetLiveStream:
    def test_live_stream_yields_appended_events(self, tmp_path):
        """Append a line after the tailer starts; the new event must surface.

        Runs the tailer in a background thread with a short poll interval and
        a hard timeout so a regression (e.g. the tailer hanging) fails fast
        instead of hanging the suite.
        """
        from sio.adapters.base import SessionManifest
        from sio.adapters.pi.adapter import PiAdapter

        session = _session_dir(tmp_path) / f"{STEM}.jsonl"
        _write_session(session, BASE_ROWS[:3])  # header + bookkeeping only

        manifest = SessionManifest(agent="pi", native_id=STEM, kind="file", path=str(session))
        adapter = PiAdapter()
        seen: list = []

        def _tail():
            for ev in adapter.get_live_stream(manifest, poll_interval=0.05):
                seen.append(ev)
                if seen:
                    return

        t = threading.Thread(target=_tail, daemon=True)
        t.start()
        time.sleep(0.15)  # let the tailer establish its starting offset
        with session.open("a") as fh:
            fh.write(json.dumps(USER_MSG) + "\n")
        t.join(timeout=5)
        assert not t.is_alive(), "get_live_stream did not surface the appended event in time"
        assert len(seen) == 1
        assert seen[0].role == "user"
        assert seen[0].content == "please list the demo directory"
