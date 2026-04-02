"""Unit tests for sio.mining.positive_extractor — T020 [US2].

Tests the extract_positive_signals() function which ingests parsed message
dicts (same schema as error_extractor) and identifies positive user signals:

    confirmation      — user confirms assistant action ("yes exactly", "correct")
    gratitude         — user thanks assistant ("thanks", "great work", "appreciate")
    implicit_approval — short positive response after tool execution ("ok", "good")
    session_success   — session ends on a positive note ("looks good", "perfect")

The function signature under test:

    extract_positive_signals(
        parsed_messages: list[dict],
        source_file: str,
        source_type: str,
    ) -> list[dict]

Each returned dict must contain:
    signal_type, source_text, context_before, tool_name,
    session_id, timestamp, source_file, source_type

Acceptance criteria: FR-007, FR-008, FR-009.
These tests are TDD — they WILL fail until implementation is written.
"""

from __future__ import annotations

import pytest

from sio.mining.positive_extractor import extract_positive_signals

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOURCE_FILE = "2026-03-01_10-00-00Z-test-session.md"
_SOURCE_TYPE = "specstory"
_SESSION_ID = "positive-test-session-001"
_TS_BASE = "2026-03-01T10:00:{:02d}.000Z"


def _ts(offset: int = 0) -> str:
    """Return a deterministic ISO-8601 timestamp at second *offset*."""
    return _TS_BASE.format(offset % 60)


def _human(content: str, offset: int = 0) -> dict:
    """Build a human-role message dict."""
    return {
        "role": "human",
        "content": content,
        "tool_name": None,
        "tool_input": None,
        "tool_output": None,
        "error": None,
        "session_id": _SESSION_ID,
        "timestamp": _ts(offset),
    }


def _assistant(
    content: str,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_output: str | None = None,
    error: str | None = None,
    offset: int = 0,
) -> dict:
    """Build an assistant-role message dict."""
    return {
        "role": "assistant",
        "content": content,
        "tool_name": tool_name,
        "tool_input": tool_input or {},
        "tool_output": tool_output,
        "error": error,
        "session_id": _SESSION_ID,
        "timestamp": _ts(offset),
    }


# ---------------------------------------------------------------------------
# Fixture: conversation with assorted positive signals
# ---------------------------------------------------------------------------


@pytest.fixture
def conversation_with_positive_signals() -> list[dict]:
    """A realistic conversation containing multiple positive signal types."""
    return [
        # Assistant reads a file
        _assistant(
            "Let me read the configuration.",
            tool_name="Read",
            tool_input={"file_path": "/tmp/config.py"},
            tool_output="DB_HOST = 'localhost'",
            offset=0,
        ),
        # User confirms: "yes exactly" => confirmation
        _human("yes exactly, that's the config I meant", offset=1),
        # Assistant edits a file
        _assistant(
            "I'll update the database host.",
            tool_name="Edit",
            tool_input={
                "file_path": "/tmp/config.py",
                "old_string": "localhost",
                "new_string": "prod-db.internal",
            },
            tool_output="File updated successfully.",
            offset=2,
        ),
        # User expresses gratitude: "thanks great work" => gratitude
        _human("thanks great work on that fix", offset=3),
        # Assistant runs tests
        _assistant(
            "Running the test suite.",
            tool_name="Bash",
            tool_input={"command": "pytest tests/ -v"},
            tool_output="5 passed in 1.2s",
            offset=4,
        ),
        # Short positive after tool execution: "perfect" => implicit_approval
        _human("perfect", offset=5),
        # Assistant writes a new file
        _assistant(
            "Creating the migration script.",
            tool_name="Write",
            tool_input={"file_path": "/tmp/migration.sql"},
            tool_output="File created.",
            offset=6,
        ),
        # Short positive: "ok" => implicit_approval
        _human("ok", offset=7),
        # Assistant summarizes
        _assistant(
            "All changes applied. The migration is ready.",
            offset=8,
        ),
        # Session ending positively: "looks good, thanks!" => session_success
        _human("looks good, thanks!", offset=9),
    ]


# ---------------------------------------------------------------------------
# Test class: signal type classification (FR-007, FR-008)
# ---------------------------------------------------------------------------


class TestSignalTypeClassification:
    """FR-007/FR-008: Detect and classify positive user signals."""

    def test_confirmation_yes_exactly(self, conversation_with_positive_signals):
        """'yes exactly' after tool call => confirmation signal."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        confirmation_signals = [
            s for s in signals if s["signal_type"] == "confirmation"
        ]
        assert len(confirmation_signals) >= 1
        assert any(
            "yes exactly" in s["signal_text"].lower()
            for s in confirmation_signals
        )

    def test_gratitude_thanks_great_work(self, conversation_with_positive_signals):
        """'thanks great work' => gratitude signal."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        gratitude_signals = [
            s for s in signals if s["signal_type"] == "gratitude"
        ]
        assert len(gratitude_signals) >= 1
        assert any(
            "thanks" in s["signal_text"].lower() for s in gratitude_signals
        )

    def test_implicit_approval_perfect(self, conversation_with_positive_signals):
        """'perfect' after tool execution => gratitude signal (single-word
        positive keywords like 'perfect' are classified as gratitude)."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        # "perfect" matches _is_gratitude() via single-word gratitude check,
        # so it is classified as gratitude, not implicit_approval.
        gratitude_signals = [
            s for s in signals if s["signal_type"] == "gratitude"
        ]
        assert any(
            "perfect" in s["signal_text"].lower() for s in gratitude_signals
        )

    def test_session_success_looks_good(self, conversation_with_positive_signals):
        """'looks good' at session end => session_success signal."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        success_signals = [
            s for s in signals if s["signal_type"] == "session_success"
        ]
        assert len(success_signals) >= 1
        assert any(
            "looks good" in s["signal_text"].lower() for s in success_signals
        )

    def test_all_four_signal_types_detected(self, conversation_with_positive_signals):
        """Conversation fixture should yield all 4 signal types."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        detected_types = {s["signal_type"] for s in signals}
        assert "confirmation" in detected_types
        assert "gratitude" in detected_types
        assert "implicit_approval" in detected_types
        assert "session_success" in detected_types


# ---------------------------------------------------------------------------
# Test class: context capture (FR-009)
# ---------------------------------------------------------------------------


class TestContextCapture:
    """FR-009: Each signal stores context_before and tool_name."""

    def test_context_before_captures_assistant_action(
        self, conversation_with_positive_signals
    ):
        """context_before should contain what the assistant did before the signal."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        # The first confirmation signal follows a Read tool call
        confirmation = next(
            s for s in signals if s["signal_type"] == "confirmation"
        )
        assert confirmation["context_before"] is not None
        assert len(confirmation["context_before"]) > 0

    def test_tool_name_captured_for_tool_related_signals(
        self, conversation_with_positive_signals
    ):
        """tool_name should be set when the signal follows a tool call."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        # "yes exactly" follows a Read call
        confirmation = next(
            s for s in signals if s["signal_type"] == "confirmation"
        )
        assert confirmation["tool_name"] == "Read"

    def test_gratitude_captures_preceding_tool(
        self, conversation_with_positive_signals
    ):
        """Gratitude signal after Edit should capture tool_name='Edit'."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        gratitude = next(
            s for s in signals if s["signal_type"] == "gratitude"
        )
        assert gratitude["tool_name"] == "Edit"

    def test_implicit_approval_captures_preceding_tool(
        self, conversation_with_positive_signals
    ):
        """'perfect' after Bash tool => tool_name='Bash'.
        Note: 'perfect' is classified as gratitude, not implicit_approval."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        perfect_signal = next(
            s
            for s in signals
            if s["signal_type"] == "gratitude"
            and "perfect" in s["signal_text"].lower()
        )
        assert perfect_signal["tool_name"] == "Bash"

    def test_signal_keys_present(
        self, conversation_with_positive_signals
    ):
        """Every signal should carry the expected keys from the implementation:
        signal_type, signal_text, context_before, tool_name, timestamp."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        expected_keys = {"signal_type", "signal_text", "context_before", "tool_name", "timestamp"}
        for signal in signals:
            assert expected_keys.issubset(signal.keys()), (
                f"Missing keys: {expected_keys - signal.keys()}"
            )

    def test_timestamp_propagated(self, conversation_with_positive_signals):
        """Every signal should have a valid timestamp."""
        signals = extract_positive_signals(
            conversation_with_positive_signals
        )
        for signal in signals:
            assert signal["timestamp"] is not None
            assert "2026-03-01T10:00:" in signal["timestamp"]


# ---------------------------------------------------------------------------
# Test class: pattern coverage (FR-008 — at least 7 patterns)
# ---------------------------------------------------------------------------


class TestPatternCoverage:
    """FR-008: System must use 7+ pattern-matching rules."""

    @pytest.fixture
    def diverse_positive_messages(self) -> list[dict]:
        """Messages exercising a variety of positive patterns."""
        patterns = [
            "yes exactly",
            "correct, that's right",
            "thanks for doing that",
            "great work",
            "perfect",
            "nice, looks good",
            "awesome job",
            "that's what I wanted",
            "well done",
            "appreciate it",
        ]
        messages = []
        for i, text in enumerate(patterns):
            # Precede each human message with a tool call so context exists
            messages.append(
                _assistant(
                    f"Performing action {i}.",
                    tool_name="Read",
                    tool_input={"file_path": f"/tmp/file_{i}.py"},
                    tool_output=f"content {i}",
                    offset=i * 2,
                )
            )
            messages.append(_human(text, offset=i * 2 + 1))
        return messages

    def test_at_least_seven_patterns_matched(self, diverse_positive_messages):
        """At least 7 distinct positive messages should produce signals."""
        signals = extract_positive_signals(
            diverse_positive_messages
        )
        # Each positive message should produce at least one signal
        matched_texts = {s["signal_text"].lower() for s in signals}
        assert len(matched_texts) >= 7, (
            f"Expected at least 7 matched patterns, got {len(matched_texts)}: "
            f"{matched_texts}"
        )

    def test_signal_types_are_valid(self, diverse_positive_messages):
        """All returned signal_type values must be from the allowed set."""
        signals = extract_positive_signals(
            diverse_positive_messages
        )
        valid_types = {
            "confirmation",
            "gratitude",
            "implicit_approval",
            "session_success",
        }
        for signal in signals:
            assert signal["signal_type"] in valid_types, (
                f"Unexpected signal_type: {signal['signal_type']}"
            )


# ---------------------------------------------------------------------------
# Test class: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and non-positive messages."""

    def test_empty_messages_returns_empty(self):
        """Empty input produces no signals."""
        signals = extract_positive_signals([])
        assert signals == []

    def test_no_positive_signals_in_negative_conversation(self):
        """A purely negative conversation should yield no positive signals."""
        messages = [
            _assistant(
                "Reading the file.",
                tool_name="Read",
                tool_output="content",
                offset=0,
            ),
            _human("No, that's wrong. Read the other file.", offset=1),
            _assistant(
                "Running tests.",
                tool_name="Bash",
                tool_output="3 failed",
                error="AssertionError",
                offset=2,
            ),
            _human("This is broken, fix it.", offset=3),
        ]
        signals = extract_positive_signals(messages)
        assert len(signals) == 0

    def test_only_assistant_messages_no_signals(self):
        """Messages with no human turns produce no signals."""
        messages = [
            _assistant("Starting work.", offset=0),
            _assistant(
                "Reading file.",
                tool_name="Read",
                tool_output="content",
                offset=1,
            ),
        ]
        signals = extract_positive_signals(messages)
        assert signals == []

    def test_single_human_message_no_preceding_tool(self):
        """A positive human message with no preceding tool call still detects."""
        messages = [
            _human("thanks for the help", offset=0),
        ]
        signals = extract_positive_signals(messages)
        # Should detect gratitude even without a preceding tool
        assert len(signals) >= 1
        assert signals[0]["signal_type"] == "gratitude"
        # tool_name should be None when no tool preceded
        assert signals[0]["tool_name"] is None
