"""Gemini JSONL and nested Codex rollout parsing in sio.search.cli."""

from __future__ import annotations

import json

import pytest

from sio.adapters.codex.adapter import _event_from_line
from sio.mining.session_distiller import distill_session
from sio.search import cli as search_cli


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(search_cli, "HOME", tmp_path)
    return tmp_path


def _jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_gemini_jsonl_messages_and_tool_calls(home):
    _jsonl(
        home / ".gemini" / "tmp" / "h1" / "chats" / "session-2026-05-04T11-45-abc.jsonl",
        [
            {"sessionId": "abc-uuid", "projectHash": "p1", "kind": "main"},
            {"id": "1", "timestamp": "t1", "type": "user", "content": [{"text": "build it"}]},
            {"$set": {"lastUpdated": "t2"}},
            {
                "id": "2", "timestamp": "t3", "type": "gemini", "content": "on it",
                "toolCalls": [
                    {"id": "c1", "name": "write_file", "args": {"path": "a"}, "status": "success",
                     "result": [{"functionResponse": {"response": {"output": "wrote a"}}}]},
                    {"id": "c2", "name": "run_shell", "args": {"command": "x"}, "status": "error",
                     "result": [{"functionResponse": {"response": {"error": "boom"}}}]},
                ],
            },
        ],
    )
    recs = list(search_cli.search_gemini("", False, None))
    assert {r.session_id for r in recs} == {"session-2026-05-04T11-45-abc"}
    assert [(r.role, r.metadata.get("tool")) for r in recs] == [
        ("user", None),
        ("assistant", None),
        ("assistant", "write_file"),
        ("tool", "write_file"),
        ("assistant", "run_shell"),
        ("tool", "run_shell"),
    ]
    assert recs[3].content == "wrote a"
    assert "error" not in recs[3].metadata
    assert recs[5].metadata["error"] == "boom"


def test_gemini_set_snapshot_replaces_messages(home):
    _jsonl(
        home / ".gemini" / "tmp" / "h2" / "chats" / "session-x.jsonl",
        [
            {"sessionId": "u", "projectHash": "p"},
            {"$set": {"messages": [
                {"id": "1", "timestamp": "t", "type": "user", "content": [{"text": "hello"}]},
            ]}},
        ],
    )
    recs = list(search_cli.search_gemini("", False, None))
    assert [(r.role, r.content) for r in recs] == [("user", "hello")]


ROLLOUT = [
    {"type": "session_meta", "timestamp": "t0", "payload": {"cwd": "/w", "originator": "codex"}},
    {"type": "response_item", "timestamp": "t1",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "check git"}]}},
    {"type": "response_item", "timestamp": "t2",
     "payload": {"type": "function_call", "name": "exec_command",
                 "arguments": "{\"cmd\":\"git status\"}", "call_id": "k1"}},
    {"type": "response_item", "timestamp": "t3",
     "payload": {"type": "function_call_output", "call_id": "k1",
                 "output": "Process exited with code 128\nOutput:\nfatal: not a git repository"}},
    {"type": "response_item", "timestamp": "t4",
     "payload": {"type": "function_call", "name": "exec_command",
                 "arguments": "{\"cmd\":\"ls\"}", "call_id": "k2"}},
    {"type": "response_item", "timestamp": "t5",
     "payload": {"type": "function_call_output", "call_id": "k2",
                 "output": "Process exited with code 0\nOutput:\na.txt"}},
]
STEM = "rollout-2026-06-03T10-31-46-019e8de5-c6b6-7683-a9d1-68d1c426877e"


def test_codex_reads_nested_rollouts_and_skips_covered_history(home):
    _jsonl(home / ".codex" / "sessions" / "2026" / "06" / "03" / f"{STEM}.jsonl", ROLLOUT)
    _jsonl(
        home / ".codex" / "history.jsonl",
        [
            {"session_id": STEM[-36:], "ts": 1, "text": "check git"},
            {"session_id": "11111111-2222-3333-4444-555555555555", "ts": 2, "text": "orphan"},
        ],
    )
    recs = list(search_cli.search_codex("", False, None))
    sids = {r.session_id for r in recs}
    assert sids == {STEM, "11111111-2222-3333-4444-555555555555"}
    rollout = [r for r in recs if r.session_id == STEM]
    assert [r.metadata.get("tool") for r in rollout if r.role == "tool"] == [
        "exec_command", "exec_command",
    ]
    assert "error" in rollout[3].metadata
    assert "error" not in rollout[5].metadata


def test_codex_function_calls_distill():
    names: dict[str, str] = {}
    events = [e for e in (_event_from_line(o, names) for o in ROLLOUT) if e is not None]
    from sio.cli.session_source import events_to_paired_messages

    distilled = distill_session(events_to_paired_messages(events))
    assert distilled["stats"]["total_tool_calls"] == 2
    assert distilled["stats"]["failed_calls"] == 1
    assert [s["summary"] for s in distilled["steps"]] == ["Run: ls"]
