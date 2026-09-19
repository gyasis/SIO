"""Tests for the Codex CLI EXTRACT adapter (CodexAdapter) + factory wiring.

Covers: get_events() normalises response_item message/custom_tool_call/
custom_tool_call_output/reasoning records and the session_meta header, and
drops pure harness bookkeeping (turn_context/world_state/token_usage_record/
event_msg); a custom_tool_call's name carries onto its matching output;
manifest_from_handle("codex:<id>") resolves both the full rollout-*.jsonl
stem AND a bare/partial uuid to the same file; adapter_for("codex") returns a
CodexAdapter; and a short get_live_stream() smoke test (tail an appended
line with a thread + timeout, so it can't hang the suite).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

SESSION_UUID = "01a09b79-8bf8-77c2-80be-56ff9aec6e56"
STEM = f"rollout-2026-09-13T11-53-49-{SESSION_UUID}"


def _rollout_dir(home: Path) -> Path:
    d = home / ".codex" / "sessions" / "2026" / "09" / "13"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_rollout(path: Path, rows: list[dict]) -> None:
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


BASE_ROWS = [
    {
        "timestamp": "2026-09-13T15:54:21.118Z",
        "type": "session_meta",
        "payload": {"session_id": SESSION_UUID, "cwd": "/home/x/code", "originator": "codex-tui"},
    },
    {
        "timestamp": "2026-09-13T15:54:22.423Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "you are codex"}]},
    },
    {
        "timestamp": "2026-09-13T15:54:22.451Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello agent"}]},
    },
    {
        "timestamp": "2026-09-13T15:54:26.901Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi there!"}]},
    },
    {
        "timestamp": "2026-09-13T15:54:26.176Z",
        "type": "reasoning",  # never seen at top-level in real files, but guard the branch anyway
    },
    {
        "timestamp": "2026-09-13T15:54:27.000Z",
        "type": "response_item",
        "payload": {"type": "reasoning", "id": "rs_1", "summary": []},
    },
    {
        "timestamp": "2026-09-13T15:54:28.223Z",
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "call_id": "call_1", "name": "exec", "input": "ls -la"},
    },
    {
        "timestamp": "2026-09-13T15:54:30.050Z",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "call_1",
            "output": [{"type": "input_text", "text": "file1\nfile2"}],
        },
    },
    {
        "timestamp": "2026-09-13T15:54:31.000Z",
        "type": "turn_context",
        "payload": {"turn_id": "t1", "cwd": "/home/x/code"},
    },
    {
        "timestamp": "2026-09-13T15:54:32.000Z",
        "type": "world_state",
        "payload": {"full": True},
    },
    {
        "timestamp": "2026-09-13T15:54:33.000Z",
        "type": "token_usage_record",
        "payload": {"tokens": 42},
    },
    {
        "timestamp": "2026-09-13T15:54:34.000Z",
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {"type": "AgentMessage"}},
    },
]


class TestEventFromLine:
    def test_returns_none_for_bookkeeping_noise(self):
        from sio.adapters.codex.adapter import _event_from_line

        noise_types = {"turn_context", "world_state", "token_usage_record", "event_msg"}
        for row in BASE_ROWS:
            if row["type"] in noise_types:
                assert _event_from_line(row, {}) is None

    def test_session_meta_maps_to_system(self):
        from sio.adapters.codex.adapter import _event_from_line

        ev = _event_from_line(BASE_ROWS[0], {})
        assert ev is not None
        assert ev.role == "system"
        assert "/home/x/code" in ev.content
        assert ev.tool is None

    def test_developer_role_maps_to_system_not_user(self):
        from sio.adapters.codex.adapter import _event_from_line

        ev = _event_from_line(BASE_ROWS[1], {})
        assert ev is not None
        assert ev.role == "system"
        assert ev.content == "you are codex"

    def test_user_message(self):
        from sio.adapters.codex.adapter import _event_from_line

        ev = _event_from_line(BASE_ROWS[2], {})
        assert ev is not None
        assert ev.role == "user"
        assert ev.content == "hello agent"
        assert ev.tool is None

    def test_assistant_message_is_text(self):
        from sio.adapters.codex.adapter import _event_from_line

        ev = _event_from_line(BASE_ROWS[3], {})
        assert ev is not None
        assert ev.role == "assistant"
        assert ev.content == "hi there!"
        assert ev.tool is None

    def test_reasoning_maps_to_system(self):
        from sio.adapters.codex.adapter import _event_from_line

        ev = _event_from_line(BASE_ROWS[5], {})
        assert ev is not None
        assert ev.role == "system"
        assert ev.content == ""  # empty summary in the fixture

    def test_custom_tool_call_carries_name_as_tool(self):
        from sio.adapters.codex.adapter import _event_from_line

        call_names: dict[str, str] = {}
        ev = _event_from_line(BASE_ROWS[6], call_names)
        assert ev is not None
        assert ev.tool == "exec"
        assert ev.content == "ls -la"
        assert call_names == {"call_1": "exec"}

    def test_custom_tool_call_output_inherits_name_from_its_call(self):
        from sio.adapters.codex.adapter import _event_from_line

        call_names: dict[str, str] = {}
        _event_from_line(BASE_ROWS[6], call_names)  # the call, first
        ev = _event_from_line(BASE_ROWS[7], call_names)  # its output
        assert ev is not None
        assert ev.tool == "exec"  # same name as the call, via call_id
        assert ev.content == "file1\nfile2"

    def test_tool_call_output_without_a_prior_call_falls_back(self):
        from sio.adapters.codex.adapter import _event_from_line

        ev = _event_from_line(BASE_ROWS[7], {})  # output with no matching call seen
        assert ev is not None
        assert ev.tool == "tool"


class TestCodexAdapterGetEvents:
    def test_get_events_yields_normalised_stream_in_order(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.codex.adapter import CodexAdapter

        rollout = _rollout_dir(tmp_path) / f"{STEM}.jsonl"
        _write_rollout(rollout, BASE_ROWS)

        manifest = SessionManifest(agent="codex", native_id=STEM, kind="file", path=str(rollout))
        events = list(CodexAdapter().get_events(manifest))

        # BASE_ROWS has 12 rows; 5 are dropped as noise -- the 4 pure
        # bookkeeping types (turn_context, world_state, token_usage_record,
        # event_msg) plus the bare top-level "reasoning" row (never a real
        # shape -- reasoning only appears nested under response_item, so
        # this guards that the fallback branch doesn't misparse it). The
        # remaining 7 (session_meta, developer message, user, assistant,
        # response_item reasoning, custom_tool_call, custom_tool_call_output)
        # each yield exactly one event, in file order.
        assert [e.role for e in events] == [
            "system",  # session_meta
            "system",  # developer message
            "user",
            "assistant",
            "system",  # response_item reasoning
            "assistant",  # custom_tool_call
            "tool",  # custom_tool_call_output
        ]
        assert [e.ts for e in events] == sorted(e.ts for e in events)

    def test_skips_malformed_lines(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.codex.adapter import CodexAdapter

        rollout = _rollout_dir(tmp_path) / f"{STEM}.jsonl"
        _write_rollout(rollout, BASE_ROWS)
        with rollout.open("a") as fh:
            fh.write("{not valid json\n")

        manifest = SessionManifest(agent="codex", native_id=STEM, kind="file", path=str(rollout))
        events = list(CodexAdapter().get_events(manifest))
        assert len(events) == 7


class TestFactoryWiring:
    def test_adapter_for_codex_returns_codex_adapter(self):
        from sio.adapters.codex.adapter import CodexAdapter
        from sio.adapters.factory import adapter_for

        adapter = adapter_for("codex")
        assert isinstance(adapter, CodexAdapter)
        assert adapter.agent == "codex"

    def test_manifest_from_handle_resolves_full_stem(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        rollout = _rollout_dir(tmp_path) / f"{STEM}.jsonl"
        _write_rollout(rollout, BASE_ROWS)
        monkeypatch.setattr(factory, "_CODEX_SESSIONS", tmp_path / ".codex" / "sessions")

        manifest = factory.manifest_from_handle(f"codex:{STEM}")
        assert manifest is not None
        assert manifest.agent == "codex"
        assert manifest.native_id == STEM
        assert manifest.path == str(rollout)

    def test_manifest_from_handle_resolves_bare_uuid_to_same_file(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        rollout = _rollout_dir(tmp_path) / f"{STEM}.jsonl"
        _write_rollout(rollout, BASE_ROWS)
        monkeypatch.setattr(factory, "_CODEX_SESSIONS", tmp_path / ".codex" / "sessions")

        manifest = factory.manifest_from_handle(f"codex:{SESSION_UUID}")
        assert manifest is not None
        # Normalised to the real file's stem, so both forms share one cursor.
        assert manifest.native_id == STEM
        assert manifest.path == str(rollout)

    def test_manifest_from_handle_missing_session_returns_none(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        monkeypatch.setattr(factory, "_CODEX_SESSIONS", tmp_path / ".codex" / "sessions")
        assert factory.manifest_from_handle("codex:no-such-session") is None


class TestGetLiveStream:
    def test_live_stream_yields_appended_events(self, tmp_path):
        """Append a line after the tailer starts; the new event must surface.

        Runs the tailer in a background thread with a short poll interval and
        a hard timeout so a regression (e.g. the tailer hanging) fails fast
        instead of hanging the suite.
        """
        from sio.adapters.base import SessionManifest
        from sio.adapters.codex.adapter import CodexAdapter

        rollout = _rollout_dir(tmp_path) / f"{STEM}.jsonl"
        _write_rollout(rollout, BASE_ROWS[:1])  # start with only the session_meta header

        manifest = SessionManifest(agent="codex", native_id=STEM, kind="file", path=str(rollout))
        adapter = CodexAdapter()
        seen: list = []

        def _tail():
            for ev in adapter.get_live_stream(manifest, poll_interval=0.05):
                seen.append(ev)
                if seen:
                    return

        t = threading.Thread(target=_tail, daemon=True)
        t.start()
        time.sleep(0.15)  # let the tailer establish its starting offset
        with rollout.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "timestamp": "2026-09-13T16:00:00.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "new live message"}],
                        },
                    }
                )
                + "\n"
            )
        t.join(timeout=5)
        assert not t.is_alive(), "get_live_stream did not surface the appended event in time"
        assert len(seen) == 1
        assert seen[0].content == "new live message"
        assert seen[0].role == "user"
