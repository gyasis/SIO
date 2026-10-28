"""session_metrics carries real numbers, not a row of zeros.

514 rows read 0/0/0 because both hooks hardcoded them, and a zero there is
indistinguishable from "this session cost nothing".
"""

from __future__ import annotations

import json

from sio.adapters.claude_code.hooks._session_metrics import transcript_metrics


def _write(path, entries) -> str:
    with path.open("w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return str(path)


def _assistant(ts, *, inp, out, cread, ccreate, model="claude-opus-5", stop="end_turn"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "stop_reason": stop,
            "content": [{"type": "text", "text": "hi"}],
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": cread,
                "cache_creation_input_tokens": ccreate,
            },
        },
    }


def test_no_path_is_unknown_not_zero():
    assert transcript_metrics(None) == {}
    assert transcript_metrics("") == {}


def test_missing_file_is_unknown():
    assert transcript_metrics("/nonexistent/transcript.jsonl") == {}


def test_aggregates_tokens_duration_and_model(tmp_path):
    p = _write(
        tmp_path / "t.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-10-27T10:00:00.000Z",
                "message": {"role": "user", "content": "go"},
            },
            _assistant("2026-10-27T10:00:30.000Z", inp=10, out=100, cread=5000, ccreate=200),
            _assistant(
                "2026-10-27T10:01:00.000Z",
                inp=20,
                out=300,
                cread=7000,
                ccreate=0,
                stop="tool_use",
            ),
        ],
    )
    m = transcript_metrics(p)

    assert m["total_input_tokens"] == 30
    assert m["total_output_tokens"] == 400
    assert m["total_cache_read_tokens"] == 12000
    assert m["total_cache_create_tokens"] == 200
    assert m["cache_hit_ratio"] == 12000 / 12030
    assert m["session_duration_seconds"] == 60.0
    assert m["message_count"] >= 3
    assert m["model_used"] == "claude-opus-5"
    assert json.loads(m["stop_reason_distribution"]) == {"end_turn": 1, "tool_use": 1}


def test_cost_is_summed_when_a_harness_sends_one(tmp_path):
    """Claude Code sends no cost field at all -- 0 here is measured, not a placeholder."""
    entry = _assistant("2026-10-27T10:00:00.000Z", inp=1, out=1, cread=0, ccreate=0)
    p = _write(tmp_path / "with-cost.jsonl", [{**entry, "costUsd": 0.25}])
    assert transcript_metrics(p)["total_cost_usd"] == 0.25

    p2 = _write(tmp_path / "no-cost.jsonl", [entry])
    assert transcript_metrics(p2)["total_cost_usd"] == 0


def test_empty_transcript_is_unknown(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert transcript_metrics(str(p)) == {}


def test_unparseable_lines_do_not_raise(tmp_path):
    p = tmp_path / "junk.jsonl"
    p.write_text("not json\n{also not}\n")
    assert transcript_metrics(str(p)) == {}


def test_single_timestamp_leaves_duration_unknown(tmp_path):
    p = _write(
        tmp_path / "one.jsonl",
        [_assistant("2026-10-27T10:00:00.000Z", inp=1, out=2, cread=0, ccreate=0)],
    )
    m = transcript_metrics(p)
    assert m["session_duration_seconds"] is None
    assert m["total_output_tokens"] == 2
