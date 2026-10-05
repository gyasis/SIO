"""Per-match `sio search` finds Claude tool calls and their output, live and archived.

It used to read only ``text`` blocks, so a command, its output or a file path the
agent wrote was unfindable — the work itself was invisible to search.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import sio.search.cli as _cli


def _write(path: Path, *entries: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


ASSISTANT_TOOL_CALL = {
    "type": "assistant", "timestamp": "2026-06-01T00:00:00Z",
    "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Checking the containers."},
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "docker exec infra-broker-1 ls /data"}},
    ]},
}
USER_TOOL_RESULT = {
    "type": "user", "timestamp": "2026-06-01T00:00:01Z",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": "broker.db  RESULTNEEDLE.log"}]},
    ]},
    "toolUseResult": {"stdout": "STDOUTNEEDLE", "stderr": ""},
}


@pytest.fixture()
def live(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setattr(_cli, "CLAUDE_PROJECTS", root)
    return root


def _hits(pattern: str):
    return list(_cli.search_claude(pattern, False, None))


def test_tool_use_input_is_searchable(live):
    _write(live / "-p" / "s.jsonl", ASSISTANT_TOOL_CALL)
    [hit] = _hits("infra-broker-1")
    assert hit.metadata["matched_in"] == ["tool_use"]
    assert "[tool_use Bash]" in hit.content and "infra-broker-1" in hit.content


def test_tool_result_and_tool_use_result_are_searchable(live):
    _write(live / "-p" / "s.jsonl", USER_TOOL_RESULT)
    assert _hits("RESULTNEEDLE")[0].metadata["matched_in"] == ["tool_result"]
    assert _hits("STDOUTNEEDLE")[0].metadata["matched_in"] == ["tool_result"]


def test_text_still_matches_and_is_tagged(live):
    _write(live / "-p" / "s.jsonl", ASSISTANT_TOOL_CALL)
    [hit] = _hits("Checking the containers")
    assert hit.metadata["matched_in"] == ["text"]


def test_snippet_contains_a_match_deep_in_long_output(live):
    long_out = "x" * 10_000 + " DEEPNEEDLE " + "y" * 10_000
    entry = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": long_out}]}}
    _write(live / "-p" / "s.jsonl", entry)
    [hit] = _hits("DEEPNEEDLE")
    assert "DEEPNEEDLE" in hit.content and len(hit.content) <= 2000


def test_archived_only_session_tool_calls_are_searchable(live):
    arch = Path(os.environ["SIO_ARCHIVE_DIR"]) / "claude" / "projects"
    _write(arch / "-p" / "deleted.jsonl", ASSISTANT_TOOL_CALL)  # gone from live
    [hit] = _hits("infra-broker-1")
    assert hit.metadata["source_kind"] == "archive"
    assert hit.metadata["matched_in"] == ["tool_use"]
