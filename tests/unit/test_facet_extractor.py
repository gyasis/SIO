"""Unit tests for sio.mining.facet_extractor — T081 [US9].

Tests extract_facets() with keyword-based heuristics for four categories:
  - tool_mastery
  - error_prone_area
  - user_satisfaction
  - session_complexity

Also tests file-hash caching (FR-050).

Acceptance criteria: FR-049, FR-050.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sio.mining.facet_extractor import (
    _hash_content,
    extract_facets,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_messages(
    *,
    tools: list[str] | None = None,
    approved_flags: list[bool | None] | None = None,
    error_types: list[str] | None = None,
    sentiment_scores: list[float | None] | None = None,
    count: int = 10,
    input_tokens: int = 500,
    output_tokens: int = 300,
) -> list[dict[str, Any]]:
    """Build a list of synthetic parsed messages."""
    msgs: list[dict[str, Any]] = []
    for i in range(count):
        msg: dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        if tools and i < len(tools):
            msg["tool_name"] = tools[i]
        if approved_flags and i < len(approved_flags):
            msg["approved"] = approved_flags[i]
        if error_types and i < len(error_types):
            msg["error_type"] = error_types[i]
        if sentiment_scores and i < len(sentiment_scores):
            msg["sentiment_score"] = sentiment_scores[i]
        msgs.append(msg)
    return msgs


def _make_metrics(
    *,
    total_input: int = 50000,
    total_output: int = 30000,
    error_type_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "error_type_counts": error_type_counts,
    }


# ---------------------------------------------------------------------------
# T081-1: extract_facets returns dict with 4 categories
# ---------------------------------------------------------------------------


class TestExtractFacetsStructure:
    """Verify extract_facets returns a dict with exactly 4 keys."""

    def test_returns_four_categories(self):
        msgs = _make_messages()
        result = extract_facets(msgs)
        assert isinstance(result, dict)
        expected_keys = {
            "tool_mastery",
            "error_prone_area",
            "user_satisfaction",
            "session_complexity",
        }
        assert set(result.keys()) == expected_keys

    def test_empty_messages(self):
        result = extract_facets([])
        assert isinstance(result, dict)
        assert len(result) == 4

    def test_with_session_metrics(self):
        msgs = _make_messages()
        metrics = _make_metrics()
        result = extract_facets(msgs, session_metrics=metrics)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# T081-2: tool_mastery — high diversity + high approval = high mastery
# ---------------------------------------------------------------------------


class TestToolMastery:
    """Verify tool_mastery facet logic."""

    def test_high_mastery(self):
        """5+ distinct tools with >80% approval -> high."""
        tools = ["Bash", "Read", "Edit", "Grep", "Write", "Glob"]
        approved = [True] * 6
        msgs = _make_messages(tools=tools, approved_flags=approved, count=6)
        result = extract_facets(msgs)
        tm = result["tool_mastery"]
        assert tm["level"] == "high"
        assert tm["distinct_tools"] >= 5
        assert tm["approval_rate"] >= 0.8

    def test_medium_mastery(self):
        """3-4 distinct tools with moderate approval -> medium."""
        tools = ["Bash", "Read", "Edit"]
        approved = [True, True, False]
        msgs = _make_messages(tools=tools, approved_flags=approved, count=3)
        result = extract_facets(msgs)
        tm = result["tool_mastery"]
        assert tm["level"] == "medium"

    def test_low_mastery(self):
        """1-2 tools or low approval -> low."""
        tools = ["Bash", "Bash"]
        approved = [False, False]
        msgs = _make_messages(tools=tools, approved_flags=approved, count=2)
        result = extract_facets(msgs)
        tm = result["tool_mastery"]
        assert tm["level"] == "low"

    def test_no_tool_calls(self):
        """Messages with no tool_name -> low mastery."""
        msgs = _make_messages(count=5)
        result = extract_facets(msgs)
        tm = result["tool_mastery"]
        assert tm["level"] == "low"
        assert tm["distinct_tools"] == 0


# ---------------------------------------------------------------------------
# T081-3: error_prone_area — most frequent error type identified
# ---------------------------------------------------------------------------


class TestErrorProneArea:
    """Verify error_prone_area facet logic."""

    def test_from_session_metrics(self):
        """Uses error_type_counts from session_metrics when available."""
        metrics = _make_metrics(
            error_type_counts={
                "tool_failure": 10,
                "user_correction": 3,
            },
        )
        result = extract_facets([], session_metrics=metrics)
        epa = result["error_prone_area"]
        assert epa["area"] == "tool_failure"
        assert epa["error_type"] == "tool_failure"
        assert epa["count"] == 10

    def test_from_parsed_messages_fallback(self):
        """Falls back to scanning messages when metrics lack counts."""
        msgs = _make_messages(
            error_types=["undo", "undo", "tool_failure"],
            count=3,
        )
        result = extract_facets(msgs)
        epa = result["error_prone_area"]
        assert epa["area"] == "undo"
        assert epa["count"] == 2

    def test_no_errors(self):
        """No errors -> area is 'none'."""
        result = extract_facets(_make_messages(count=3))
        epa = result["error_prone_area"]
        assert epa["area"] == "none"
        assert epa["error_type"] is None


# ---------------------------------------------------------------------------
# T081-4: user_satisfaction — average sentiment score
# ---------------------------------------------------------------------------


class TestUserSatisfaction:
    """Verify user_satisfaction facet logic."""

    def test_positive_sentiment(self):
        msgs = _make_messages(
            sentiment_scores=[0.8, 0.5, 0.6, 0.9],
            count=4,
        )
        result = extract_facets(msgs)
        us = result["user_satisfaction"]
        assert us["level"] == "positive"
        assert us["avg_score"] > 0.3

    def test_negative_sentiment(self):
        msgs = _make_messages(
            sentiment_scores=[-0.7, -0.5, -0.8],
            count=3,
        )
        result = extract_facets(msgs)
        us = result["user_satisfaction"]
        assert us["level"] == "negative"
        assert us["avg_score"] < -0.3

    def test_neutral_sentiment(self):
        msgs = _make_messages(
            sentiment_scores=[0.1, -0.1, 0.0],
            count=3,
        )
        result = extract_facets(msgs)
        us = result["user_satisfaction"]
        assert us["level"] == "neutral"

    def test_no_scores(self):
        """Messages without sentiment_score -> neutral default."""
        result = extract_facets(_make_messages(count=5))
        us = result["user_satisfaction"]
        assert us["level"] == "neutral"
        assert us["avg_score"] == 0.0
        assert us["scored_messages"] == 0


# ---------------------------------------------------------------------------
# T081-5: session_complexity — based on message count + token count
# ---------------------------------------------------------------------------


class TestSessionComplexity:
    """Verify session_complexity facet logic."""

    def test_complex_session(self):
        """Many messages + many tokens -> complex."""
        metrics = _make_metrics(total_input=500000, total_output=200000)
        msgs = _make_messages(count=50)
        result = extract_facets(msgs, session_metrics=metrics)
        sc = result["session_complexity"]
        assert sc["level"] == "complex"
        assert sc["score"] >= 200

    def test_simple_session(self):
        """Few messages + few tokens -> simple."""
        metrics = _make_metrics(total_input=10, total_output=5)
        msgs = _make_messages(
            count=2, input_tokens=5, output_tokens=3,
        )
        result = extract_facets(msgs, session_metrics=metrics)
        sc = result["session_complexity"]
        assert sc["level"] == "simple"
        assert sc["score"] < 50

    def test_moderate_session(self):
        """Moderate message count and tokens."""
        metrics = _make_metrics(total_input=1000, total_output=500)
        msgs = _make_messages(count=10)
        result = extract_facets(msgs, session_metrics=metrics)
        sc = result["session_complexity"]
        assert sc["level"] == "moderate"

    def test_zero_tokens_no_crash(self):
        """Zero tokens should not crash (log(0) guard)."""
        result = extract_facets(
            [],
            session_metrics=_make_metrics(total_input=0, total_output=0),
        )
        sc = result["session_complexity"]
        assert sc["level"] == "simple"
        assert sc["score"] == 0.0


# ---------------------------------------------------------------------------
# T081-6: caching — same file hash returns cached result
# ---------------------------------------------------------------------------


class TestFacetCaching:
    """Verify file-hash-based caching."""

    def test_cache_roundtrip(self, tmp_path, monkeypatch):
        """Cached result is returned for same hash on second call."""
        cache_dir = str(tmp_path / "facets")
        monkeypatch.setattr(
            "sio.mining.facet_extractor._FACETS_DIR", cache_dir,
        )

        msgs = _make_messages(
            tools=["Bash", "Read", "Edit", "Grep", "Write"],
            approved_flags=[True] * 5,
            sentiment_scores=[0.5, 0.6],
            count=5,
        )
        fhash = _hash_content("test-session-content")

        # First call computes and caches
        result1 = extract_facets(msgs, file_hash=fhash)

        # Verify cache file exists
        cache_file = Path(cache_dir) / f"{fhash}.json"
        assert cache_file.exists()

        # Second call should return cached value even with different messages
        result2 = extract_facets([], file_hash=fhash)
        assert result2 == result1

    def test_no_cache_without_hash(self, tmp_path, monkeypatch):
        """Without file_hash, no cache file is created."""
        cache_dir = str(tmp_path / "facets_nocache")
        monkeypatch.setattr(
            "sio.mining.facet_extractor._FACETS_DIR", cache_dir,
        )

        extract_facets(_make_messages(count=2))
        # No files should be created
        cache_path = Path(cache_dir)
        if cache_path.exists():
            assert list(cache_path.iterdir()) == []

    def test_corrupt_cache_recomputes(self, tmp_path, monkeypatch):
        """Corrupted cache file causes recomputation."""
        cache_dir = str(tmp_path / "facets_corrupt")
        monkeypatch.setattr(
            "sio.mining.facet_extractor._FACETS_DIR", cache_dir,
        )

        fhash = _hash_content("corrupt-test")
        cache_file = Path(cache_dir) / f"{fhash}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("{invalid json")

        msgs = _make_messages(count=3)
        result = extract_facets(msgs, file_hash=fhash)
        assert "tool_mastery" in result
