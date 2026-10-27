"""The hook must read the payload Claude Code actually sends: `tool_response`.

Measured 2026-10-27 on razer: all 55,348 captured rows carried
activated=1, correct_action=1, correct_outcome=0 — which is not three constants but one
cause. `post_tool_use.py` read `payload.get("tool_output", "")` and `payload.get("error")`,
neither of which Claude Code sends. auto_label() therefore always saw output="" and
error=None:

    activated       = 1   because "" is not None
    correct_action  = 1   because error was always None
    correct_outcome = 0   because output was always ""

So the corpus asserted that 99.99% of tool calls produced no useful output. Training on
that teaches a model that everything fails.

Claude Code sends `tool_response`. Other hooks in this very environment already read it:
  ~/.claude/hooks/graphiti-precheck/post-tool-use.sh  -> .tool_response.stdout/.stderr/.is_error
  ~/.claude/hooks/prd-cadence/post-tool-use.sh        -> .tool_response

The existing contract test could not catch this: its _VALID_PAYLOAD invents `tool_output`
and `error`, so it validates the hook against a shape that is never produced.
"""

from __future__ import annotations

import json

from sio.adapters.claude_code.hooks.post_tool_use import handle_post_tool_use

BASE = {
    "session_id": "real-payload-001",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hi"},
    "user_message": "say hi",
}


def _labels(conn, session_id):
    row = conn.execute(
        "SELECT activated, correct_action, correct_outcome FROM behavior_invocations "
        "WHERE session_id LIKE ? ORDER BY id DESC LIMIT 1",
        (f"%{session_id}%",),
    ).fetchone()
    assert row is not None, f"no row logged for {session_id}"
    return {"activated": row[0], "correct_action": row[1], "correct_outcome": row[2]}


def test_successful_bash_is_labelled_a_success(tmp_db):
    """A command that ran and produced stdout is a correct outcome, not a failure."""
    payload = {**BASE, "session_id": "ok-001",
               "tool_response": {"stdout": "hi\n", "stderr": "", "is_error": False,
                                 "exit_code": 0}}
    handle_post_tool_use(json.dumps(payload), conn=tmp_db)
    assert _labels(tmp_db, "ok-001") == {"activated": 1, "correct_action": 1,
                                         "correct_outcome": 1}


def test_failed_bash_is_labelled_a_failure(tmp_db):
    """is_error must reach correct_action; today nothing ever sets it."""
    payload = {**BASE, "session_id": "err-001",
               "tool_response": {"stdout": "", "stderr": "boom", "is_error": True,
                                 "exit_code": 1}}
    handle_post_tool_use(json.dumps(payload), conn=tmp_db)
    labels = _labels(tmp_db, "err-001")
    assert labels["correct_action"] == 0, "an is_error response must not read as correct"
    assert labels["correct_outcome"] == 0


def test_nonzero_exit_code_is_a_failure(tmp_db):
    """Some responses carry exit_code without is_error."""
    payload = {**BASE, "session_id": "exit-001",
               "tool_response": {"stdout": "", "stderr": "nope", "exit_code": 2}}
    handle_post_tool_use(json.dumps(payload), conn=tmp_db)
    assert _labels(tmp_db, "exit-001")["correct_action"] == 0


def test_tool_response_as_a_plain_string(tmp_db):
    """Non-Bash tools return a bare string; it is still real output."""
    payload = {**BASE, "session_id": "str-001", "tool_name": "Read",
               "tool_response": "line one\nline two"}
    handle_post_tool_use(json.dumps(payload), conn=tmp_db)
    assert _labels(tmp_db, "str-001") == {"activated": 1, "correct_action": 1,
                                          "correct_outcome": 1}


def test_labels_are_not_all_identical_across_mixed_traffic(tmp_db):
    """The regression guard: a mix of good and bad calls must produce a mix of labels.

    This is what 55,348 identical rows should have tripped.
    """
    good = {**BASE, "session_id": "mix-ok",
            "tool_response": {"stdout": "fine", "is_error": False, "exit_code": 0}}
    bad = {**BASE, "session_id": "mix-bad",
           "tool_response": {"stdout": "", "stderr": "fail", "is_error": True,
                             "exit_code": 1}}
    handle_post_tool_use(json.dumps(good), conn=tmp_db)
    handle_post_tool_use(json.dumps(bad), conn=tmp_db)
    outcomes = {r[0] for r in tmp_db.execute(
        "SELECT correct_outcome FROM behavior_invocations WHERE session_id LIKE '%mix-%'")}
    assert outcomes == {0, 1}, f"expected both outcomes, got {outcomes}"


def test_legacy_tool_output_still_honoured(tmp_db):
    """Other harnesses/tests pass tool_output; do not break them while fixing this."""
    payload = {**BASE, "session_id": "legacy-001",
               "tool_output": "some output", "error": None}
    handle_post_tool_use(json.dumps(payload), conn=tmp_db)
    assert _labels(tmp_db, "legacy-001")["correct_outcome"] == 1


def test_transcript_path_becomes_the_evidence_pointer(tmp_db):
    """conversation_pointer / history_file must be populated — they were always NULL.

    Claude Code sends `transcript_path`, and SIO already reads it in pre_compact.py and
    stop.py; post_tool_use.py simply never did. The audit note at logger.py says
    "tool_name / tool_input / conversation_pointer MUST be populated" four lines above the
    dict that hardcodes conversation_pointer to None. Without it no row can be walked back
    to the transcript that produced it, which is Principle XIII (Transparent Machine) in
    name only.
    """
    payload = {**BASE, "session_id": "ptr-001",
               "transcript_path": "/home/u/.claude/projects/p/abc.jsonl",
               "tool_response": {"stdout": "ok", "exit_code": 0}}
    handle_post_tool_use(json.dumps(payload), conn=tmp_db)
    row = tmp_db.execute(
        "SELECT history_file, conversation_pointer FROM behavior_invocations "
        "WHERE session_id LIKE '%ptr-001%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == "/home/u/.claude/projects/p/abc.jsonl", "history_file not recorded"
    assert row[1] and "abc.jsonl" in row[1], "conversation_pointer not recorded"


def test_mcp_tools_are_not_filed_as_skill(tmp_db):
    """behavior_type was hardcoded 'skill' for every row, including MCP calls.

    `mcp_tool` is already a legal CHECK value, so this needs no migration. A plain tool
    call (Bash/Read/Edit) still has no correct value in the CHECK domain — that needs a
    schema change and is deliberately left to its own commit (PRD F5).
    """
    payload = {**BASE, "session_id": "mcp-001", "tool_name": "mcp__graphiti__search_nodes",
               "tool_response": {"stdout": "[]", "exit_code": 0}}
    handle_post_tool_use(json.dumps(payload), conn=tmp_db)
    row = tmp_db.execute(
        "SELECT behavior_type FROM behavior_invocations "
        "WHERE session_id LIKE '%mcp-001%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "mcp_tool", f"MCP call filed as {row[0]!r}"
