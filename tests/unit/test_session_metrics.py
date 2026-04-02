"""Unit tests for session metrics computation — T015 [US3].

Tests _compute_session_metrics and _parse_iso_timestamp from
sio.mining.pipeline, covering:

- Inter-message latency / session_duration_seconds from known timestamps
- cache_hit_ratio calculation from known token counts
- Edge cases: single message, no timestamps, zero denominators
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sio.mining.pipeline import _compute_session_metrics, _parse_iso_timestamp


# ---------------------------------------------------------------------------
# Fixtures: message factories
# ---------------------------------------------------------------------------

_FAKE_PATH = Path("/tmp/test-session.jsonl")
_FAKE_HASH = "a" * 64


def _msg(
    role: str = "user",
    content: str = "",
    timestamp: str | None = None,
    tool_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    cost_usd: float | None = None,
    stop_reason: str | None = None,
    model: str | None = None,
    is_sidechain: bool | None = None,
    error: str | None = None,
) -> dict:
    """Build a minimal message dict matching the pipeline's expectations."""
    return {
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "tool_name": tool_name,
        "tool_input": None,
        "tool_output": None,
        "error": error,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cost_usd": cost_usd,
        "stop_reason": stop_reason,
        "model": model,
        "is_sidechain": is_sidechain,
    }


# ---------------------------------------------------------------------------
# _parse_iso_timestamp tests
# ---------------------------------------------------------------------------


class TestParseIsoTimestamp:
    """Tests for the ISO-8601 timestamp parser helper."""

    def test_standard_iso(self) -> None:
        dt = _parse_iso_timestamp("2026-03-01T10:00:00+00:00")
        assert dt is not None
        assert dt.hour == 10
        assert dt.minute == 0

    def test_z_suffix(self) -> None:
        dt = _parse_iso_timestamp("2026-03-01T10:00:00Z")
        assert dt is not None
        assert dt.hour == 10

    def test_none_input(self) -> None:
        assert _parse_iso_timestamp(None) is None

    def test_empty_string(self) -> None:
        assert _parse_iso_timestamp("") is None

    def test_malformed_string(self) -> None:
        assert _parse_iso_timestamp("not-a-date") is None

    def test_partial_iso(self) -> None:
        # fromisoformat handles date-only strings in Python 3.11+
        dt = _parse_iso_timestamp("2026-03-01")
        assert dt is not None

    def test_milliseconds(self) -> None:
        dt = _parse_iso_timestamp("2026-03-01T10:00:30.500Z")
        assert dt is not None
        assert dt.second == 30


# ---------------------------------------------------------------------------
# Session duration tests
# ---------------------------------------------------------------------------


class TestSessionDuration:
    """Tests for session_duration_seconds computation."""

    def test_known_timestamps_180_seconds(self) -> None:
        """Three messages at 00:00, 01:30, 03:00 -> duration = 180s."""
        messages = [
            _msg(
                role="user",
                content="hello",
                timestamp="2026-03-01T00:00:00Z",
            ),
            _msg(
                role="assistant",
                content="hi",
                timestamp="2026-03-01T00:01:30Z",
            ),
            _msg(
                role="user",
                content="bye",
                timestamp="2026-03-01T00:03:00Z",
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["session_duration_seconds"] == pytest.approx(180.0)

    def test_single_message_duration_none(self) -> None:
        """A single timestamped message cannot compute a duration."""
        messages = [
            _msg(
                role="user",
                content="only one",
                timestamp="2026-03-01T00:00:00Z",
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        # With only one timestamp, duration should be None (not 0)
        assert metrics["session_duration_seconds"] is None

    def test_no_timestamps_duration_none(self) -> None:
        """Messages without any timestamps -> duration is None."""
        messages = [
            _msg(role="user", content="no ts"),
            _msg(role="assistant", content="also no ts"),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["session_duration_seconds"] is None

    def test_mixed_timestamps(self) -> None:
        """Some messages have timestamps, some don't — use only valid ones."""
        messages = [
            _msg(
                role="user",
                content="first",
                timestamp="2026-03-01T12:00:00Z",
            ),
            _msg(role="assistant", content="middle"),  # no timestamp
            _msg(
                role="user",
                content="last",
                timestamp="2026-03-01T12:05:00Z",
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["session_duration_seconds"] == pytest.approx(300.0)

    def test_unordered_timestamps_still_correct(self) -> None:
        """Timestamps out of order are sorted before computing delta."""
        messages = [
            _msg(
                role="user",
                content="late",
                timestamp="2026-03-01T10:10:00Z",
            ),
            _msg(
                role="assistant",
                content="early",
                timestamp="2026-03-01T10:00:00Z",
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["session_duration_seconds"] == pytest.approx(600.0)


# ---------------------------------------------------------------------------
# Cache hit ratio tests
# ---------------------------------------------------------------------------


class TestCacheHitRatio:
    """Tests for cache_hit_ratio = cache_read / (cache_read + input_tokens)."""

    def test_known_ratio(self) -> None:
        """cache_read=80, input=20 -> ratio = 80/100 = 0.8."""
        messages = [
            _msg(
                role="assistant",
                content="resp",
                input_tokens=20,
                cache_read_input_tokens=80,
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["cache_hit_ratio"] == pytest.approx(0.8)

    def test_multiple_messages_aggregated(self) -> None:
        """Ratio computed from totals across all messages."""
        messages = [
            _msg(
                role="assistant",
                input_tokens=50,
                cache_read_input_tokens=50,
            ),
            _msg(
                role="assistant",
                input_tokens=50,
                cache_read_input_tokens=150,
            ),
        ]
        # totals: cache_read=200, input=100, ratio=200/300
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["cache_hit_ratio"] == pytest.approx(200.0 / 300.0)

    def test_zero_denominator_ratio_none(self) -> None:
        """When both cache_read and input are 0, ratio should be None."""
        messages = [
            _msg(role="user", content="no tokens"),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["cache_hit_ratio"] is None

    def test_no_cache_reads_ratio_zero(self) -> None:
        """cache_read=0, input=100 -> ratio = 0.0."""
        messages = [
            _msg(
                role="assistant",
                input_tokens=100,
                cache_read_input_tokens=0,
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["cache_hit_ratio"] == pytest.approx(0.0)

    def test_all_cache_ratio_one(self) -> None:
        """cache_read=100, input=0 -> ratio = 1.0 (all from cache)."""
        messages = [
            _msg(
                role="assistant",
                input_tokens=0,
                cache_read_input_tokens=100,
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["cache_hit_ratio"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Aggregate count tests
# ---------------------------------------------------------------------------


class TestAggregateCounts:
    """Tests for message_count, tool_call_count, error_count, sidechain_count."""

    def test_message_count(self) -> None:
        messages = [_msg(), _msg(), _msg()]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["message_count"] == 3

    def test_tool_call_count(self) -> None:
        messages = [
            _msg(role="assistant", tool_name="Read"),
            _msg(role="assistant", tool_name="Edit"),
            _msg(role="user", content="ok"),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["tool_call_count"] == 2

    def test_error_count(self) -> None:
        errors = [{"error_text": "err1"}, {"error_text": "err2"}]
        metrics = _compute_session_metrics(
            [_msg()], errors, _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["error_count"] == 2

    def test_sidechain_count(self) -> None:
        messages = [
            _msg(is_sidechain=True),
            _msg(is_sidechain=True),
            _msg(is_sidechain=False),
            _msg(),  # None
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["sidechain_count"] == 2

    def test_empty_messages(self) -> None:
        metrics = _compute_session_metrics(
            [], [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["message_count"] == 0
        assert metrics["tool_call_count"] == 0
        assert metrics["error_count"] == 0
        assert metrics["session_duration_seconds"] is None
        assert metrics["cache_hit_ratio"] is None


# ---------------------------------------------------------------------------
# Token aggregation tests
# ---------------------------------------------------------------------------


class TestTokenAggregation:
    """Tests for total token and cost aggregation."""

    def test_token_sums(self) -> None:
        messages = [
            _msg(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=30,
                cache_creation_input_tokens=10,
                cost_usd=0.05,
            ),
            _msg(
                input_tokens=200,
                output_tokens=100,
                cache_read_input_tokens=70,
                cache_creation_input_tokens=20,
                cost_usd=0.10,
            ),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["total_input_tokens"] == 300
        assert metrics["total_output_tokens"] == 150
        assert metrics["total_cache_read_tokens"] == 100
        assert metrics["total_cache_create_tokens"] == 30
        assert metrics["total_cost_usd"] == pytest.approx(0.15)

    def test_none_tokens_treated_as_zero(self) -> None:
        """Messages with None tokens should not break aggregation."""
        messages = [
            _msg(input_tokens=None, output_tokens=None),
            _msg(input_tokens=50, output_tokens=25),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["total_input_tokens"] == 50
        assert metrics["total_output_tokens"] == 25


# ---------------------------------------------------------------------------
# Model and stop_reason tests
# ---------------------------------------------------------------------------


class TestModelAndStopReason:
    """Tests for model_used and stop_reason_distribution derivation."""

    def test_model_most_common(self) -> None:
        messages = [
            _msg(model="claude-3-opus"),
            _msg(model="claude-3-opus"),
            _msg(model="claude-3-sonnet"),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["model_used"] == "claude-3-opus"

    def test_no_model(self) -> None:
        messages = [_msg()]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["model_used"] is None

    def test_stop_reason_distribution(self) -> None:
        import json

        messages = [
            _msg(stop_reason="end_turn"),
            _msg(stop_reason="end_turn"),
            _msg(stop_reason="tool_use"),
        ]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        dist = json.loads(metrics["stop_reason_distribution"])
        assert dist == {"end_turn": 2, "tool_use": 1}

    def test_no_stop_reasons(self) -> None:
        messages = [_msg()]
        metrics = _compute_session_metrics(
            messages, [], _FAKE_PATH, _FAKE_HASH,
        )
        assert metrics["stop_reason_distribution"] is None


# ---------------------------------------------------------------------------
# Session ID derivation test
# ---------------------------------------------------------------------------


class TestSessionId:
    """Tests for session_id derivation from file_path and file_hash."""

    def test_session_id_format(self) -> None:
        metrics = _compute_session_metrics(
            [_msg()], [], _FAKE_PATH, _FAKE_HASH,
        )
        expected = f"{_FAKE_PATH}:{_FAKE_HASH[:16]}"
        assert metrics["session_id"] == expected
