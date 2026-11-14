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


def test_live_stream_is_refused_loudly(tmp_path):
    with pytest.raises(NotImplementedError, match="zstd"):
        next(DshAdapter().get_live_stream(_manifest(_write_session(tmp_path))))
