"""Contract tests for SIO hook JSON schemas per hook-contracts.md."""

from __future__ import annotations

import json

# Schema definitions from hook-contracts.md
POST_TOOL_USE_INPUT_REQUIRED = {
    "session_id": (str,), "tool_name": (str,), "tool_input": (dict,), "tool_output": (str,),
}
POST_TOOL_USE_INPUT_NULLABLE = {
    "error": (str, type(None)), "user_message": (str, type(None)),
}
PRE_TOOL_USE_INPUT_REQUIRED = {
    "session_id": (str,), "tool_name": (str,), "tool_input": (dict,),
}
NOTIFICATION_INPUT_REQUIRED = {"session_id": (str,), "message": (str,)}
HOOK_OUTPUT_ACTIONS = {"allow", "block", "modify"}

# Sample payloads
POST_TOOL_USE_INPUT_SAMPLE = {
    "session_id": "abc-123-def", "tool_name": "Read",
    "tool_input": {"file_path": "/path/to/file"}, "tool_output": "file contents...",
    "error": None, "user_message": "Read the config file at /path/to/file",
}
PRE_TOOL_USE_INPUT_SAMPLE = {
    "session_id": "abc-123-def", "tool_name": "WebSearch", "tool_input": {"query": "DSPy documentation"},
}
NOTIFICATION_INPUT_SAMPLE = {"session_id": "abc-123-def", "message": "-- should have used gemini_research"}


def _validate_required(payload, required):
    errors = []
    for field, types in required.items():
        if field not in payload:
            errors.append(f"Missing: {field}")
        elif not isinstance(payload[field], types):
            errors.append(f"{field} type mismatch")
    return errors


def _validate_nullable(payload, nullable):
    errors = []
    for field, types in nullable.items():
        if field in payload and not isinstance(payload[field], types):
            errors.append(f"{field} type mismatch")
    return errors


def _validate_output(payload):
    if "action" not in payload:
        return ["Missing: action"]
    if payload["action"] not in HOOK_OUTPUT_ACTIONS:
        return [f"Invalid action: {payload['action']}"]
    return []


class TestPostToolUseInputSchema:
    def test_post_tool_use_input_schema(self):
        assert _validate_required(POST_TOOL_USE_INPUT_SAMPLE, POST_TOOL_USE_INPUT_REQUIRED) == []

    def test_roundtrips_through_json(self):
        restored = json.loads(json.dumps(POST_TOOL_USE_INPUT_SAMPLE))
        assert _validate_required(restored, POST_TOOL_USE_INPUT_REQUIRED) == []


class TestPostToolUseOutputSchema:
    def test_post_tool_use_output_schema(self):
        assert _validate_output({"action": "allow"}) == []

    def test_rejects_missing_action(self):
        assert len(_validate_output({})) > 0


class TestPreToolUseInputSchema:
    def test_pre_tool_use_input_schema(self):
        assert _validate_required(PRE_TOOL_USE_INPUT_SAMPLE, PRE_TOOL_USE_INPUT_REQUIRED) == []


class TestPreToolUseOutputSchema:
    def test_pre_tool_use_output_schema(self):
        assert _validate_output({"action": "allow"}) == []


class TestNotificationInputSchema:
    def test_notification_input_schema(self):
        assert _validate_required(NOTIFICATION_INPUT_SAMPLE, NOTIFICATION_INPUT_REQUIRED) == []


class TestNotificationOutputSchema:
    def test_notification_output_schema(self):
        assert _validate_output({"action": "allow"}) == []


class TestPostToolUseNullableFields:
    def test_post_tool_use_error_field_nullable(self):
        assert _validate_nullable({**POST_TOOL_USE_INPUT_SAMPLE, "error": None}, POST_TOOL_USE_INPUT_NULLABLE) == []
        assert _validate_nullable({**POST_TOOL_USE_INPUT_SAMPLE, "error": "file not found"}, POST_TOOL_USE_INPUT_NULLABLE) == []

    def test_post_tool_use_user_message_nullable(self):
        assert _validate_nullable({**POST_TOOL_USE_INPUT_SAMPLE, "user_message": None}, POST_TOOL_USE_INPUT_NULLABLE) == []
        assert _validate_nullable({**POST_TOOL_USE_INPUT_SAMPLE, "user_message": "[UNAVAILABLE]"}, POST_TOOL_USE_INPUT_NULLABLE) == []
