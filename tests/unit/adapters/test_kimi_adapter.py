"""Tests for the Kimi Code CLI EXTRACT adapter (KimiAdapter) + factory wiring.

Covers: get_events() yields normalised SessionEvents for the two searchable
record types and skips noise; manifest_from_handle("kimi:<session>") resolves
to the main agent's wire.jsonl; adapter_for("kimi") returns a KimiAdapter; and
a short get_live_stream() smoke test (tail an appended line with a thread +
timeout, so it can't hang the suite).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

SESSION_DIR = "session_fe5a65fd-be74-4a07-a9d5-c37b6d49c0e8"
WORKSPACE = "wd_series_773848d0eb88"


def _agent_dir(home: Path, agent: str = "main") -> Path:
    d = home / ".kimi-code" / "sessions" / WORKSPACE / SESSION_DIR / "agents" / agent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_wire(path: Path, rows: list[dict]) -> None:
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


BASE_ROWS = [
    {"type": "metadata", "protocol_version": "1.4", "created_at": 1784792289060},
    {"type": "config.update", "profileName": "agent"},
    {
        "type": "turn.prompt",
        "input": [{"type": "text", "text": "hello agent"}],
        "origin": {"kind": "user"},
        "time": 1784792423850,
    },
    {
        "type": "context.append_message",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "hello agent"}],
        },
        "time": 1784792423862,
    },
    {
        "type": "context.append_message",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "hi there!"}],
        },
        "time": "1784792430000",
    },
    {"type": "context.append_loop_event", "event": {"note": "noise"}, "time": 1784792425000},
    {"type": "llm.request", "model": "x", "time": 1784792426000},
    {"type": "usage.record", "tokens": 1, "time": 1784792427000},
]


class TestEventFromLine:
    def test_returns_none_for_noise_types(self):
        from sio.adapters.kimi.adapter import _event_from_line

        for row in BASE_ROWS:
            if row["type"] not in ("context.append_message", "turn.prompt"):
                assert _event_from_line(row) is None

    def test_extracts_append_message(self):
        from sio.adapters.kimi.adapter import _event_from_line

        row = BASE_ROWS[4]  # assistant append_message
        ev = _event_from_line(row)
        assert ev is not None
        assert ev.role == "assistant"
        assert ev.content == "hi there!"
        assert ev.ts.startswith("2026-")

    def test_extracts_turn_prompt(self):
        from sio.adapters.kimi.adapter import _event_from_line

        row = BASE_ROWS[2]
        ev = _event_from_line(row)
        assert ev is not None
        assert ev.role == "user"
        assert ev.content == "hello agent"


class TestKimiAdapterGetEvents:
    def test_get_events_yields_only_content_records(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.kimi.adapter import KimiAdapter

        wire = _agent_dir(tmp_path) / "wire.jsonl"
        _write_wire(wire, BASE_ROWS)

        manifest = SessionManifest(
            agent="kimi", native_id=SESSION_DIR, kind="file", path=str(wire),
        )
        events = list(KimiAdapter().get_events(manifest))
        assert len(events) == 3  # turn.prompt + 2x context.append_message
        assert {e.role for e in events} == {"user", "assistant"}
        assert all(e.tool is None for e in events)

    def test_skips_malformed_lines(self, tmp_path):
        from sio.adapters.base import SessionManifest
        from sio.adapters.kimi.adapter import KimiAdapter

        wire = _agent_dir(tmp_path) / "wire.jsonl"
        _write_wire(wire, BASE_ROWS)
        with wire.open("a") as fh:
            fh.write("{not valid json\n")

        manifest = SessionManifest(
            agent="kimi", native_id=SESSION_DIR, kind="file", path=str(wire),
        )
        events = list(KimiAdapter().get_events(manifest))
        assert len(events) == 3


class TestFactoryWiring:
    def test_adapter_for_kimi_returns_kimi_adapter(self):
        from sio.adapters.factory import adapter_for
        from sio.adapters.kimi.adapter import KimiAdapter

        adapter = adapter_for("kimi")
        assert isinstance(adapter, KimiAdapter)
        assert adapter.agent == "kimi"

    def test_manifest_from_handle_resolves_main_wire(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        wire = _agent_dir(tmp_path, "main") / "wire.jsonl"
        _write_wire(wire, BASE_ROWS)
        # a sibling non-main agent must NOT be picked over main
        other = _agent_dir(tmp_path, "agent-0") / "wire.jsonl"
        _write_wire(other, BASE_ROWS)

        monkeypatch.setattr(factory, "_KIMI_SESSIONS", tmp_path / ".kimi-code" / "sessions")

        manifest = factory.manifest_from_handle(f"kimi:{SESSION_DIR}")
        assert manifest is not None
        assert manifest.agent == "kimi"
        assert manifest.native_id == SESSION_DIR
        assert manifest.path == str(wire)
        assert manifest.path.endswith("agents/main/wire.jsonl")

    def test_manifest_from_handle_resolves_bare_uuid(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        wire = _agent_dir(tmp_path, "main") / "wire.jsonl"
        _write_wire(wire, BASE_ROWS)
        monkeypatch.setattr(factory, "_KIMI_SESSIONS", tmp_path / ".kimi-code" / "sessions")

        bare_uuid = SESSION_DIR.removeprefix("session_")
        manifest = factory.manifest_from_handle(f"kimi:{bare_uuid}")
        assert manifest is not None
        assert manifest.native_id == SESSION_DIR

    def test_manifest_from_handle_missing_session_returns_none(self, tmp_path, monkeypatch):
        import sio.adapters.factory as factory

        monkeypatch.setattr(factory, "_KIMI_SESSIONS", tmp_path / ".kimi-code" / "sessions")
        assert factory.manifest_from_handle("kimi:no-such-session") is None


class TestGetLiveStream:
    def test_live_stream_yields_appended_events(self, tmp_path):
        """Append a line after the tailer starts; the new event must surface.

        Runs the tailer in a background thread with a short poll interval and
        a hard timeout so a regression (e.g. the tailer hanging) fails fast
        instead of hanging the suite.
        """
        from sio.adapters.base import SessionManifest
        from sio.adapters.kimi.adapter import KimiAdapter

        wire = _agent_dir(tmp_path) / "wire.jsonl"
        _write_wire(wire, BASE_ROWS[:2])  # start with only noise, no content yet

        manifest = SessionManifest(
            agent="kimi", native_id=SESSION_DIR, kind="file", path=str(wire),
        )
        adapter = KimiAdapter()
        seen: list = []

        def _tail():
            for ev in adapter.get_live_stream(manifest, poll_interval=0.05):
                seen.append(ev)
                if seen:
                    return

        t = threading.Thread(target=_tail, daemon=True)
        t.start()
        time.sleep(0.15)  # let the tailer establish its starting offset
        with wire.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "context.append_message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "new live message"}],
                        },
                        "time": 1784792999000,
                    }
                )
                + "\n"
            )
        t.join(timeout=5)
        assert not t.is_alive(), "get_live_stream did not surface the appended event in time"
        assert len(seen) == 1
        assert seen[0].content == "new live message"
        assert seen[0].role == "user"
