"""Unit tests for the session-skeleton discovery view + expand-by-UUID.

Covers the behaviour added so `sio search` is a session-level skeleton, not a
raw match count: dedup by session UUID, match classification
(discussed > edited > command > output), dropping search-noise (the agent
searching FOR the pattern), and full-transcript expansion by UUID.
"""

from __future__ import annotations

import json

import pytest

from sio.search import cli


def _line(role: str, blocks: list, ts: str = "2026-05-31T00:00:00", **extra) -> str:
    entry = {"type": role, "timestamp": ts, "message": {"role": role, "content": blocks}}
    entry.update(extra)
    return json.dumps(entry)


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """A synthetic ~/.claude/projects tree, one jsonl per session UUID."""
    proj = tmp_path / "-home-gyasis-Documents-code-Demo"
    proj.mkdir(parents=True)

    # aaaa: human prose mentions redpen -> discussed
    (proj / "aaaa.jsonl").write_text(
        _line("user", [{"type": "text", "text": "let's fix the redpen overlay"}])
    )
    # bbbb: ONLY an echoed search command -> search-noise -> dropped
    (proj / "bbbb.jsonl").write_text(
        _line(
            "assistant",
            [{"type": "tool_use", "name": "Bash",
              "input": {"command": 'session-search "redpen" --all'}}],
        )
    )
    # cccc: a Write whose file_path contains redpen -> edited
    (proj / "cccc.jsonl").write_text(
        _line(
            "assistant",
            [{"type": "tool_use", "name": "Write",
              "input": {"file_path": "/code/redpen/overlay.ts", "content": "x"}}],
        )
    )
    # dddd: redpen only in tool_result output -> output
    (proj / "dddd.jsonl").write_text(
        _line(
            "user",
            [{"type": "tool_result", "content": "drwx redpen-loop\ndrwx other"}],
        )
    )
    # eeee: no mention at all -> not yielded
    (proj / "eeee.jsonl").write_text(
        _line("user", [{"type": "text", "text": "unrelated work"}])
    )

    monkeypatch.setattr(cli, "CLAUDE_PROJECTS", tmp_path)
    return tmp_path


def test_skeleton_classifies_and_dedups(corpus):
    hits = {h.session_id: h for h in cli.iter_claude_session_hits("redpen", False, None)}

    # search-noise-only and no-mention sessions are excluded entirely
    assert "bbbb" not in hits, "echoed search command must not count as a hit"
    assert "eeee" not in hits

    assert hits["aaaa"].label == cli.CAT_DISCUSSED
    assert hits["cccc"].label == cli.CAT_EDITED
    assert hits["dddd"].label == cli.CAT_OUTPUT
    assert hits["aaaa"].project == "Demo"
    assert hits["aaaa"].snippet  # snippet captured


def test_discussed_wins_over_output_in_same_session(corpus):
    # a session with both an ls-output mention AND real prose -> discussed
    (corpus / "-home-gyasis-Documents-code-Demo" / "ffff.jsonl").write_text(
        _line("user", [{"type": "tool_result", "content": "redpen-loop/"}])
        + "\n"
        + _line("assistant", [{"type": "text", "text": "redpen needs a grid"}])
    )
    hits = {h.session_id: h for h in cli.iter_claude_session_hits("redpen", False, None)}
    assert hits["ffff"].label == cli.CAT_DISCUSSED


def test_tooluseresult_top_level_field_is_read(corpus):
    # redpen only in the top-level toolUseResult (outside message.content)
    (corpus / "-home-gyasis-Documents-code-Demo" / "gggg.jsonl").write_text(
        _line("user", [{"type": "text", "text": "ok"}],
              toolUseResult={"stdout": "found redpen here"})
    )
    hits = {h.session_id: h for h in cli.iter_claude_session_hits("redpen", False, None)}
    assert "gggg" in hits
    assert hits["gggg"].label == cli.CAT_OUTPUT


def test_expand_by_uuid(corpus, capsys):
    rc = cli.expand_sessions(["aaaa"], clean=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "session aaaa" in out
    assert "redpen overlay" in out


def test_expand_unknown_uuid_returns_2(corpus, capsys):
    rc = cli.expand_sessions(["nope-not-here"], clean=False)
    assert rc == 2
