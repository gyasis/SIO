"""T022 [US1] Integration test for the full telemetry pipeline.

Simulates 20 PostToolUse hook calls with distinct tool_names and verifies
that the full pipeline (hook -> logger -> scrubber -> DB) produces 20
distinct rows with secrets properly scrubbed.
"""

from __future__ import annotations

import json

import pytest

from sio.adapters.claude_code.hooks.post_tool_use import handle_post_tool_use


# Tool names that represent a realistic mix of Claude Code tools
_TOOL_NAMES = [
    "Read", "Write", "Edit", "Bash", "Grep", "Glob",
    "WebSearch", "WebFetch", "NotebookEdit", "Skill",
    "Task", "TodoRead", "TodoWrite", "GitLog", "GitDiff",
    "GitStatus", "GitCommit", "ListDir", "MoveFile", "CopyFile",
]

assert len(_TOOL_NAMES) == 20, "Need exactly 20 distinct tool names"


def _make_payload(tool_name: str, index: int) -> str:
    """Build a PostToolUse JSON payload.

    Every even-indexed payload includes an api_key secret in user_message
    to verify scrubbing in the integration path.
    """
    user_message = f"Do something with {tool_name}"
    if index % 2 == 0:
        user_message += " using api_key=sk-secret-value-12345"

    payload = {
        "session_id": "integration-session-001",
        "tool_name": tool_name,
        "tool_input": {"arg": f"value-{index}"},
        "tool_output": f"output from {tool_name}",
        "error": None,
        "user_message": user_message,
    }
    return json.dumps(payload)


class TestTwentyHookCallsProduceTwentyRows:
    """End-to-end: 20 distinct PostToolUse calls -> 20 DB rows, secrets scrubbed."""

    def test_20_hook_calls_produce_20_rows(self, tmp_db):
        # Run 20 hook calls with distinct tool names
        results = []
        for i, tool_name in enumerate(_TOOL_NAMES):
            stdin_json = _make_payload(tool_name, i)
            result_str = handle_post_tool_use(stdin_json)
            result = json.loads(result_str)
            results.append(result)

        # All hooks must return allow
        for i, result in enumerate(results):
            assert result["action"] == "allow", (
                f"Hook call {i} ({_TOOL_NAMES[i]}) did not return allow"
            )

        # Verify exactly 20 rows in the database
        row_count = tmp_db.execute(
            "SELECT COUNT(*) FROM behavior_invocations "
            "WHERE session_id = 'integration-session-001'"
        ).fetchone()[0]
        assert row_count == 20, (
            f"Expected 20 rows for 20 distinct tool calls, got {row_count}"
        )

        # Verify no duplicates -- each tool_name should appear exactly once
        distinct_actions = tmp_db.execute(
            "SELECT DISTINCT actual_action FROM behavior_invocations "
            "WHERE session_id = 'integration-session-001'"
        ).fetchall()
        action_names = {row["actual_action"] for row in distinct_actions}
        assert len(action_names) == 20, (
            f"Expected 20 distinct actions, got {len(action_names)}: {action_names}"
        )

    def test_secrets_scrubbed_in_stored_messages(self, tmp_db):
        """Every user_message that had an api_key should be scrubbed in the DB."""
        # Run the 20 hook calls
        for i, tool_name in enumerate(_TOOL_NAMES):
            stdin_json = _make_payload(tool_name, i)
            handle_post_tool_use(stdin_json)

        # Check rows where user_message originally had a secret (even indices)
        rows = tmp_db.execute(
            "SELECT user_message, actual_action FROM behavior_invocations "
            "WHERE session_id = 'integration-session-001'"
        ).fetchall()

        for row in rows:
            assert "sk-secret-value-12345" not in row["user_message"], (
                f"Secret not scrubbed in row for {row['actual_action']}: "
                f"{row['user_message']}"
            )

    def test_all_rows_have_required_fields(self, tmp_db):
        """Every stored row must have non-null required fields."""
        for i, tool_name in enumerate(_TOOL_NAMES):
            stdin_json = _make_payload(tool_name, i)
            handle_post_tool_use(stdin_json)

        rows = tmp_db.execute(
            "SELECT * FROM behavior_invocations "
            "WHERE session_id = 'integration-session-001'"
        ).fetchall()

        for row in rows:
            assert row["session_id"] is not None
            assert row["timestamp"] is not None
            assert row["platform"] is not None
            assert row["user_message"] is not None
            assert row["behavior_type"] is not None
