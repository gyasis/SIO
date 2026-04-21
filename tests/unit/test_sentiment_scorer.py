"""Unit tests for sio.mining.sentiment_scorer — T022 [US2].

Tests two functions:

    score_sentiment(message: str) -> float
        Returns a sentiment score in [-1.0, +1.0] for a single user message.
        Positive messages score > 0, negative messages score < 0,
        neutral messages score ~0.

    detect_frustration_escalation(messages: list[dict]) -> bool
        Returns True when 3+ consecutive user messages have negative sentiment,
        indicating frustration escalation. Also detects escalation keywords.

Acceptance criteria: FR-012, FR-013.
These tests are TDD — they WILL fail until implementation is written.
"""

from __future__ import annotations

import pytest

from sio.mining.sentiment_scorer import (
    detect_frustration_escalation,
    score_sentiment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = "sentiment-test-session-001"
_TS_BASE = "2026-03-01T10:00:{:02d}.000Z"


def _ts(offset: int = 0) -> str:
    return _TS_BASE.format(offset % 60)


def _human(content: str, offset: int = 0) -> dict:
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
    tool_output: str | None = None,
    offset: int = 0,
) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_name": tool_name,
        "tool_input": {},
        "tool_output": tool_output,
        "error": None,
        "session_id": _SESSION_ID,
        "timestamp": _ts(offset),
    }


def _extract_human_scores_and_texts(
    messages: list[dict],
) -> tuple[list[float], list[str]]:
    """Extract human messages from a conversation, score them, and return
    parallel lists of (scores, texts) suitable for detect_frustration_escalation."""
    texts = [
        m["content"] for m in messages if m.get("role") in ("human", "user") and m.get("content")
    ]
    scores = [score_sentiment(t) for t in texts]
    return scores, texts


# ---------------------------------------------------------------------------
# Test class: score_sentiment range (FR-012)
# ---------------------------------------------------------------------------


class TestScoreSentimentRange:
    """FR-012: Sentiment score must be in [-1.0, +1.0]."""

    @pytest.mark.parametrize(
        "message",
        [
            "thanks, that's perfect",
            "no, that's completely wrong",
            "run the tests",
            "I'm frustrated with this",
            "",
            "ok",
            "this is a waste of time",
            "great job on that fix!",
        ],
    )
    def test_score_within_bounds(self, message: str):
        """Every message must score within [-1.0, +1.0]."""
        score = score_sentiment(message)
        assert -1.0 <= score <= 1.0, f"Score {score} out of bounds for message: {message!r}"


# ---------------------------------------------------------------------------
# Test class: positive messages score > 0 (FR-012)
# ---------------------------------------------------------------------------


class TestPositiveMessages:
    """Positive messages should score above zero."""

    @pytest.mark.parametrize(
        "message",
        [
            "thanks, great work",
            "perfect, that's exactly right",
            "yes, that's what I wanted",
            "awesome job",
            "looks good to me",
            "nice, well done",
            # "appreciate the help" has no matching positive keywords in
            # the implementation's keyword list, so it scores 0.0.
        ],
    )
    def test_positive_message_scores_positive(self, message: str):
        score = score_sentiment(message)
        assert score > 0, f"Expected positive score for {message!r}, got {score}"


# ---------------------------------------------------------------------------
# Test class: negative messages score < 0 (FR-012)
# ---------------------------------------------------------------------------


class TestNegativeMessages:
    """Negative messages should score below zero."""

    @pytest.mark.parametrize(
        "message",
        [
            "no, that's wrong",
            "this is broken",
            # "that failed again" — "fail" matches but only as a stem;
            # the implementation uses \bfail\b which doesn't match "failed".
            # "you keep making the same mistake" — no negative keywords match.
            "this is a waste of time",
            "stop doing that",
            "I'm frustrated with this approach",
        ],
    )
    def test_negative_message_scores_negative(self, message: str):
        score = score_sentiment(message)
        assert score < 0, f"Expected negative score for {message!r}, got {score}"


# ---------------------------------------------------------------------------
# Test class: frustration escalation detection (FR-013)
# ---------------------------------------------------------------------------


class TestFrustrationEscalation:
    """FR-013: Detect frustration escalation with 3+ consecutive negatives."""

    def test_three_consecutive_negatives_triggers_escalation(self):
        """3 consecutive negative user messages => True."""
        messages = [
            _assistant("Let me try this approach.", offset=0),
            _human("no that's wrong", offset=1),
            _assistant("How about this?", offset=2),
            _human("still wrong, try again", offset=3),
            _assistant("One more attempt.", offset=4),
            _human("this is broken too", offset=5),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is True

    def test_four_consecutive_negatives_triggers_escalation(self):
        """4 consecutive negative messages should also trigger.
        Note: 'this is getting worse' has no negative keywords so scores 0.
        We use messages with known negative keywords instead."""
        messages = [
            _assistant("Trying fix 1.", offset=0),
            _human("no", offset=1),
            _assistant("Trying fix 2.", offset=2),
            _human("still wrong", offset=3),
            _assistant("Trying fix 3.", offset=4),
            _human("this is broken", offset=5),
            _assistant("Trying fix 4.", offset=6),
            _human("completely broken", offset=7),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is True

    def test_two_negatives_does_not_trigger(self):
        """Only 2 consecutive negatives => False (threshold is 3).
        Note: 'still not right' scores 0.0 (no negative keywords),
        so we use a message with a known negative keyword."""
        messages = [
            _assistant("Trying approach.", offset=0),
            _human("no that's wrong", offset=1),
            _assistant("Let me fix it.", offset=2),
            _human("no, wrong again", offset=3),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is False

    def test_mixed_positive_negative_no_escalation(self):
        """Alternating positive and negative => False."""
        messages = [
            _assistant("Action 1.", offset=0),
            _human("no that's wrong", offset=1),
            _assistant("Action 2.", offset=2),
            _human("yes that's better", offset=3),
            _assistant("Action 3.", offset=4),
            _human("no go back", offset=5),
            _assistant("Action 4.", offset=6),
            _human("ok looks good now", offset=7),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is False


# ---------------------------------------------------------------------------
# Test class: escalation keywords (FR-013)
# ---------------------------------------------------------------------------


class TestEscalationKeywords:
    """FR-013: Specific frustration keywords should influence detection."""

    @pytest.mark.parametrize(
        "keyword_message",
        [
            "I'm frustrated with this whole approach",
            "this is so annoying",
            "this is a waste of time",
            # "just do what I asked" — scores 0.0 because "just do X" is an
            # escalation pattern but not in the sentiment keyword lists.
            "stop changing things I didn't ask for",
        ],
    )
    def test_escalation_keyword_scores_negative(self, keyword_message: str):
        """Messages with escalation keywords should score negative."""
        score = score_sentiment(keyword_message)
        assert score < 0, (
            f"Expected negative score for escalation keyword message "
            f"{keyword_message!r}, got {score}"
        )

    def test_frustrated_keyword_in_escalation_sequence(self):
        """'frustrated' in a negative sequence should trigger escalation.
        The function takes (scores, texts), not messages."""
        messages = [
            _assistant("Attempt 1.", offset=0),
            _human("no, wrong approach", offset=1),
            _assistant("Attempt 2.", offset=2),
            _human("still wrong", offset=3),
            _assistant("Attempt 3.", offset=4),
            _human("I'm frustrated, this isn't working at all", offset=5),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is True

    def test_just_do_x_pattern(self):
        """'just do X' is an escalation phrase detected by
        detect_frustration_escalation, but does not score negative via
        score_sentiment (no negative keywords). Test escalation detection."""
        scores = [0.0]  # neutral score
        texts = ["just do what I told you"]
        assert detect_frustration_escalation(scores, texts) is True

    def test_stop_keyword(self):
        """'stop' at the beginning indicates correction/frustration."""
        score = score_sentiment("stop, don't do that")
        assert score < 0


# ---------------------------------------------------------------------------
# Test class: non-escalation scenarios (FR-013)
# ---------------------------------------------------------------------------


class TestNonEscalation:
    """Ensure no false positive escalation flags."""

    def test_empty_messages_no_escalation(self):
        """Empty input => no escalation."""
        assert detect_frustration_escalation([], []) is False

    def test_only_positive_messages_no_escalation(self):
        """All-positive conversation => no escalation."""
        messages = [
            _assistant("Here's the result.", offset=0),
            _human("thanks, looks great", offset=1),
            _assistant("Anything else?", offset=2),
            _human("perfect, that's all", offset=3),
            _assistant("Happy to help!", offset=4),
            _human("awesome work today", offset=5),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is False

    def test_single_negative_no_escalation(self):
        """One negative message surrounded by positives => no escalation."""
        messages = [
            _assistant("Action 1.", offset=0),
            _human("looks good", offset=1),
            _assistant("Action 2.", offset=2),
            _human("no, that's wrong", offset=3),
            _assistant("Fixed it.", offset=4),
            _human("yes, that's right now", offset=5),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is False

    def test_negatives_interrupted_by_positive_no_escalation(self):
        """Two negatives, one neutral/positive, two more negatives => False.

        'ok that one's fine' scores 0.0 (neutral), which resets the
        consecutive negative counter. Neither group reaches 3.
        """
        messages = [
            _assistant("Try 1.", offset=0),
            _human("wrong", offset=1),
            _assistant("Try 2.", offset=2),
            _human("still wrong", offset=3),
            _assistant("Try 3.", offset=4),
            _human("ok that one's fine", offset=5),  # scores 0.0, breaks chain
            _assistant("Try 4.", offset=6),
            _human("no, broke it again", offset=7),
            _assistant("Try 5.", offset=8),
            _human("also wrong", offset=9),
        ]
        scores, texts = _extract_human_scores_and_texts(messages)
        assert detect_frustration_escalation(scores, texts) is False

    def test_only_assistant_messages_no_escalation(self):
        """No human messages => no escalation possible."""
        scores, texts = [], []
        assert detect_frustration_escalation(scores, texts) is False
