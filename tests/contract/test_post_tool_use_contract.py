"""T021 [US1] PostToolUse hook contract tests.

Tests for handle_post_tool_use which receives a JSON string from stdin,
logs the invocation, and returns a JSON string with {"action": "allow"}.
"""

from __future__ import annotations

import json

from sio.adapters.claude_code.hooks.post_tool_use import handle_post_tool_use

# --- Valid payload used across multiple tests ---
_VALID_PAYLOAD = {
    "session_id": "contract-session-001",
    "tool_name": "Read",
    "tool_input": {"file_path": "/tmp/test.py"},
    "tool_output": "file contents here",
    "error": None,
    "user_message": "Read the test file",
}


class TestParsesValidJson:
    """handle_post_tool_use should parse valid JSON and return allow."""

    def test_parses_valid_json(self):
        stdin_json = json.dumps(_VALID_PAYLOAD)
        result_str = handle_post_tool_use(stdin_json)

        result = json.loads(result_str)
        assert result["action"] == "allow"


class TestHandlesErrorField:
    """Even when tool_output includes an error, hook still returns allow."""

    def test_handles_error_field(self):
        payload = {**_VALID_PAYLOAD, "error": "file not found"}
        stdin_json = json.dumps(payload)
        result_str = handle_post_tool_use(stdin_json)

        result = json.loads(result_str)
        assert result["action"] == "allow", (
            "PostToolUse hook must always allow -- it is observational, not blocking."
        )


class TestSessionIdPropagation:
    """The logged record should carry the session_id from the input payload."""

    def test_session_id_propagation(self, tmp_db):
        payload = {**_VALID_PAYLOAD, "session_id": "propagation-session-xyz"}
        stdin_json = json.dumps(payload)

        # The hook should log into the DB; we verify the session_id was stored.
        handle_post_tool_use(stdin_json, conn=tmp_db)

        rows = tmp_db.execute(
            "SELECT session_id FROM behavior_invocations WHERE session_id = ?",
            ("propagation-session-xyz",),
        ).fetchall()
        assert len(rows) >= 1, "handle_post_tool_use must persist the invocation to the DB"
        assert rows[0]["session_id"] == "propagation-session-xyz"


class TestReturnsAllowOnInvalidJson:
    """Malformed JSON should not crash the hook; it must degrade to allow."""

    def test_returns_allow_on_invalid_json(self):
        result_str = handle_post_tool_use("this is not json {{{")

        result = json.loads(result_str)
        assert result["action"] == "allow", (
            "Hook must never block on malformed input; always return allow."
        )

    def test_returns_allow_on_empty_string(self):
        result_str = handle_post_tool_use("")

        result = json.loads(result_str)
        assert result["action"] == "allow"

    def test_returns_allow_on_missing_fields(self):
        # Partial payload -- missing required fields
        result_str = handle_post_tool_use(json.dumps({"tool_name": "Read"}))

        result = json.loads(result_str)
        assert result["action"] == "allow"
