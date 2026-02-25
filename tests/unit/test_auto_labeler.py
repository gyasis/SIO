"""T020 [US1] Tests for sio.core.telemetry.auto_labeler.auto_label.

auto_label produces automatic quality labels for a tool invocation based on
whether it activated, performed the correct action, and achieved the correct
outcome.
"""

from __future__ import annotations

import pytest

from sio.core.telemetry.auto_labeler import auto_label


class TestSuccessfulToolCall:
    """A tool call with non-empty output and no error should score all 1s."""

    def test_successful_tool_call(self):
        result = auto_label(
            tool_name="Read",
            tool_input='{"file_path": "/tmp/foo.py"}',
            tool_output="file contents here",
            error=None,
        )

        assert isinstance(result, dict)
        assert result["activated"] == 1
        assert result["correct_action"] == 1
        assert result["correct_outcome"] == 1


class TestErrorToolCall:
    """A tool call that returns output but also has an error."""

    def test_error_tool_call(self):
        result = auto_label(
            tool_name="Bash",
            tool_input='{"command": "cat /nonexistent"}',
            tool_output="cat: /nonexistent: No such file or directory",
            error="file not found",
        )

        assert result["activated"] == 1, (
            "Tool still activated (produced output)"
        )
        assert result["correct_action"] == 0, (
            "Action was not correct because there was an error"
        )
        assert result["correct_outcome"] == 0, (
            "Outcome was not correct because there was an error"
        )


class TestEmptyOutput:
    """A tool call that returns an empty string (activated but no useful outcome)."""

    def test_empty_output(self):
        result = auto_label(
            tool_name="Grep",
            tool_input='{"pattern": "nonexistent", "path": "/tmp"}',
            tool_output="",
            error=None,
        )

        assert result["activated"] == 1, (
            "Empty string is still 'activated' (tool ran and returned)"
        )
        assert result["correct_action"] == 1, (
            "No error means action was correct"
        )
        assert result["correct_outcome"] == 0, (
            "Empty output means outcome is not correct"
        )


class TestNoneOutput:
    """A tool call that returns None (tool did not activate / produce output)."""

    def test_none_output(self):
        result = auto_label(
            tool_name="Read",
            tool_input='{"file_path": "/tmp/missing.py"}',
            tool_output=None,
            error=None,
        )

        assert result["activated"] == 0, (
            "None output means the tool did not activate"
        )
        assert result["correct_action"] == 1, (
            "No error means action was technically correct"
        )
        assert result["correct_outcome"] == 0, (
            "None output means no correct outcome"
        )


class TestReturnType:
    """auto_label must always return a dict with exactly three integer keys."""

    def test_return_keys(self):
        result = auto_label(
            tool_name="Read",
            tool_input="{}",
            tool_output="ok",
            error=None,
        )
        assert set(result.keys()) == {"activated", "correct_action", "correct_outcome"}
        for key, value in result.items():
            assert isinstance(value, int), f"{key} should be int, got {type(value)}"
            assert value in (0, 1), f"{key} should be 0 or 1, got {value}"
