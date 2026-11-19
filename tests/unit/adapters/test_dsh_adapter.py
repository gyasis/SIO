"""Tests for the dsh (DeepSeek Harness) session adapter."""

from __future__ import annotations

import json

import pytest

from sio.adapters.base import SessionManifest
from sio.adapters.dsh.adapter import DshAdapter
from sio.cli.session_source import load_session_messages
from sio.core.session_handle import from_path
from sio.mining.session_distiller import distill_session

zstandard = pytest.importorskip("zstandard")

RECORDS = [
    {"type": "session", "version": 4, "id": "session-abc", "createdAt": 1793449385015,
     "cwd": "/work"},
    {"type": "turn/start", "seq": 1, "time": 1793449385100, "data": {"turn": 1}},
    {"type": "user/message", "seq": 2, "time": 1793449385200,
     "data": {"role": "user", "content": [{"type": "text", "text": "list the files"}]}},
    {"type": "assistant/message", "seq": 3, "time": 1793449385300,
     "data": {"message": {"role": "assistant", "content": [
         {"type": "reasoning", "text": "use bash"},
         {"type": "tool-call", "id": "x", "name": "bash", "arguments": "{\"command\":\"ls\"}"},
     ]}}},
    {"type": "tool/call", "seq": 4, "time": 1793449385400,
     "data": {"callId": "c1", "name": "bash", "arguments": "{\"command\":\"ls\"}"}},
    {"type": "tool/result", "seq": 5, "time": 1793449385500,
     "data": {"message": {"role": "tool", "toolCallId": "c1", "isError": False,
                          "content": [{"type": "text", "text": "a.txt"}]}}},
    {"type": "tool/call", "seq": 6, "time": 1793449385600,
     "data": {"callId": "c2", "name": "read", "arguments": "{\"path\":\"/nope\"}"}},
    {"type": "tool/result", "seq": 7, "time": 1793449385700,
     "data": {"message": {"role": "tool", "toolCallId": "c2", "isError": True,
                          "content": [{"type": "text", "text": "ENOENT"}]}}},
    {"type": "assistant/message", "seq": 8, "time": 1793449385800,
     "data": {"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}},
]


def _write_session(root, records=RECORDS):
    path = root / ".dsh" / "sessions" / "--work--" / "session-abc" / "session.v4.jsonl.zstd"
    path.parent.mkdir(parents=True)
    payload = "\n".join(json.dumps(r) for r in records).encode() + b"\n"
    path.write_bytes(zstandard.ZstdCompressor().compress(payload))
    return path


def _manifest(path):
    return SessionManifest(agent="dsh", native_id="session-abc", kind="file", path=str(path))


def test_events_from_compressed_session(tmp_path):
    events = list(DshAdapter().get_events(_manifest(_write_session(tmp_path))))
    kinds = [(e.role, e.tool) for e in events]
    assert kinds == [
        ("system", None),       # session header
        ("user", None),
        ("system", None),       # reasoning
        ("assistant", "bash"),  # tool/call, not the duplicate tool-call block
        ("tool", "bash"),
        ("assistant", "read"),
        ("tool", "read"),
        ("assistant", None),
    ]
    assert events[6].error == "ENOENT"
    assert events[4].error is None


def test_path_maps_to_dsh_handle(tmp_path):
    assert from_path(str(_write_session(tmp_path))) == "dsh:session-abc"


def test_dsh_session_distills(tmp_path):
    messages, label = load_session_messages(str(_write_session(tmp_path)))
    assert label == "dsh:session-abc"
    distilled = distill_session(messages)
    assert distilled["stats"]["total_tool_calls"] == 2
    assert distilled["stats"]["failed_calls"] == 1
    assert [s["summary"] for s in distilled["steps"]] == ["Run: ls"]


def _append_frame(path, records):
    payload = "\n".join(json.dumps(r) for r in records).encode() + b"\n"
    with open(path, "ab") as fh:
        fh.write(zstandard.ZstdCompressor().compress(payload))


def _follow(path, *, from_start=False, after=None):
    """Drive get_live_stream with time.sleep patched to run `after` once, then stop."""
    import sio.adapters.dsh.adapter as mod

    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] == 1 and after:
            after()
        elif calls["n"] > 1:
            raise StopIteration

    real = mod.time.sleep
    mod.time.sleep = fake_sleep
    out = []
    try:
        for ev in DshAdapter().get_live_stream(_manifest(path), from_start=from_start):
            out.append(ev)
    except (StopIteration, RuntimeError):
        pass
    finally:
        mod.time.sleep = real
    return out


def test_live_stream_yields_only_records_appended_after_start(tmp_path):
    path = _write_session(tmp_path)
    later = {"type": "tool/result", "seq": 9, "time": 1793449385900,
             "data": {"message": {"role": "tool", "toolCallId": "c1", "isError": False,
                                  "content": [{"type": "text", "text": "b.txt"}]}}}
    events = _follow(path, after=lambda: _append_frame(path, [later]))
    assert [(e.role, e.tool, e.content) for e in events] == [("tool", "bash", "b.txt")]


def test_live_stream_from_start_replays_existing(tmp_path):
    events = _follow(_write_session(tmp_path), from_start=True)
    assert len(events) == 8


def test_live_stream_ignores_truncated_trailing_frame(tmp_path):
    path = _write_session(tmp_path)
    frame = zstandard.ZstdCompressor().compress(b'{"type": "user/message"}\n')

    def half_write():
        with open(path, "ab") as fh:
            fh.write(frame[: len(frame) // 2])

    assert _follow(path, after=half_write) == []
