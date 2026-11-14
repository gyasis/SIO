"""Tests for sio.cli.session_source: loading one session from any harness."""

from __future__ import annotations

import json

import pytest

from sio.adapters.base import SessionEvent
from sio.cli.session_source import (
    SessionLoadError,
    events_to_paired_messages,
    load_session_messages,
    resolve_session,
)
from sio.mining.session_distiller import distill_session


def _ev(role, content="", tool=None, raw=None, error=None):
    return SessionEvent(ts="t", role=role, content=content, tool=tool, raw=raw or {}, error=error)


def test_tool_call_joined_to_its_result():
    msgs = events_to_paired_messages(
        [
            _ev("user", "fix the build"),
            _ev("assistant", '{"command": "make"}', tool="bash"),
            _ev("tool", "ok", tool="bash"),
        ]
    )
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    call = msgs[1]
    assert call["tool_name"] == "bash"
    assert call["tool_input"] == '{"command": "make"}'
    assert call["tool_output"] == "ok"
    assert call["error"] is None


def test_flagged_result_marks_the_call_failed():
    msgs = events_to_paired_messages(
        [
            _ev("assistant", "{}", tool="read"),
            _ev("tool", "boom", tool="read", error="boom"),
        ]
    )
    assert msgs[0]["error"] == "boom"


def test_results_pair_in_order_across_interleaved_calls():
    msgs = events_to_paired_messages(
        [
            _ev("assistant", "a", tool="bash"),
            _ev("assistant", "b", tool="read"),
            _ev("tool", "out-b", tool="read"),
            _ev("tool", "out-a", tool="bash"),
        ]
    )
    assert msgs[0]["tool_output"] == "out-a"
    assert msgs[1]["tool_output"] == "out-b"


def test_whole_call_in_raw_is_one_message():
    msgs = events_to_paired_messages(
        [_ev("tool", "x", tool="edit", raw={"tool_input": {"path": "f"}, "tool_output": "done"})]
    )
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["tool_input"] == {"path": "f"}
    assert msgs[0]["tool_output"] == "done"


def test_orphan_result_is_kept_as_context_not_a_call():
    msgs = events_to_paired_messages([_ev("tool", "stray", tool="bash")])
    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool_name"] is None


def _write_omp_session(path):
    records = [
        {"type": "session", "version": 3, "id": "u1", "timestamp": "2026-11-09T00:00:00Z"},
        {
            "type": "message",
            "timestamp": "2026-11-09T00:00:01Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "find the book pdf"}]},
        },
        {
            "type": "message",
            "timestamp": "2026-11-09T00:00:02Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "c1",
                        "name": "bash",
                        "arguments": {"command": "curl -sO https://example.org/a.pdf"},
                    }
                ],
            },
        },
        {
            "type": "message",
            "timestamp": "2026-11-09T00:00:03Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "c1",
                "toolName": "bash",
                "content": [{"type": "text", "text": "saved"}],
            },
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_omp_file_outside_the_store_distills(tmp_path):
    session = tmp_path / ".omp" / "agent" / "sessions" / "-x" / "2026-11-09T00-00-00Z_u1.jsonl"
    _write_omp_session(session)

    messages, label = load_session_messages(str(session))

    assert label.startswith("omp:")
    distilled = distill_session(messages)
    assert distilled["stats"]["winning_steps"] == 1
    assert distilled["steps"][0]["summary"].startswith("Run: curl")


def test_unrecognised_file_fails_loudly(tmp_path):
    f = tmp_path / "other.jsonl"
    f.write_text('{"type": "user/message", "seq": 1}\n')
    with pytest.raises(SessionLoadError, match="0 messages parsed from 1 non-blank lines"):
        load_session_messages(str(f))


def test_missing_file_fails_loudly(tmp_path):
    with pytest.raises(SessionLoadError, match="File not found"):
        load_session_messages(str(tmp_path / "nope.jsonl"))


def test_latest_rejects_unlistable_harness():
    with pytest.raises(SessionLoadError, match="cannot list paperclip"):
        resolve_session(None, agent="paperclip")


def test_explicit_target_is_returned_unchanged():
    assert resolve_session("omp:abc", agent="pi") == "omp:abc"


def test_distiller_matches_tool_names_case_insensitively():
    msgs = [
        {"role": "assistant", "tool_name": "bash", "tool_input": '{"command": "ls"}'},
        {"role": "assistant", "tool_name": "Read", "tool_input": '{"file_path": "/a"}'},
    ]
    summaries = [s["summary"] for s in distill_session(msgs)["steps"]]
    assert summaries == ["Run: ls", "Read /a"]
