"""T023 [US2] Unit tests for sio.clustering.ranker — pattern frequency × recency scoring.

Tests cover rank_patterns(patterns) which accepts a list of pattern dicts and
returns them sorted by a combined frequency × recency score, highest first.

These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sio.clustering.ranker import rank_patterns

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(days_ago: float = 0) -> str:
    """Return an ISO-8601 timestamp string *days_ago* days before now."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.isoformat()


def _make_pattern(
    *,
    pattern_id: str = "pat-001",
    description: str = "A test pattern",
    tool_name: str | None = "Read",
    error_count: int = 1,
    session_count: int = 1,
    days_ago: float = 0.0,
    error_ids: list[int] | None = None,
    rank_score: float = 0.0,
) -> dict:
    """Build a minimal pattern dict with sensible defaults."""
    last_seen = _ts(days_ago)
    return {
        "pattern_id": pattern_id,
        "description": description,
        "tool_name": tool_name,
        "error_count": error_count,
        "session_count": session_count,
        "first_seen": last_seen,   # simplified: first_seen == last_seen for most tests
        "last_seen": last_seen,
        "rank_score": rank_score,
        "error_ids": error_ids if error_ids is not None else list(range(error_count)),
    }


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestFrequencyAffectsRanking:
    """A pattern with more errors should rank above one with fewer, given equal recency."""

    def test_high_count_beats_low_count_same_recency(self):
        patterns = [
            _make_pattern(pattern_id="low",  error_count=2,  days_ago=1.0),
            _make_pattern(pattern_id="high", error_count=10, days_ago=1.0),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[0]["pattern_id"] == "high"

    def test_frequency_ordering_preserved_across_three_patterns(self):
        patterns = [
            _make_pattern(pattern_id="small",  error_count=1,  days_ago=0.5),
            _make_pattern(pattern_id="medium", error_count=5,  days_ago=0.5),
            _make_pattern(pattern_id="large",  error_count=20, days_ago=0.5),
        ]
        ranked = rank_patterns(patterns)
        ids = [p["pattern_id"] for p in ranked]
        assert ids.index("large") < ids.index("medium")
        assert ids.index("medium") < ids.index("small")

    def test_double_count_improves_rank(self):
        """Doubling error_count should move a pattern up in the ranking."""
        base = _make_pattern(pattern_id="base", error_count=5, days_ago=2.0)
        double = _make_pattern(pattern_id="double", error_count=10, days_ago=2.0)
        ranked = rank_patterns([base, double])
        assert ranked[0]["pattern_id"] == "double"


class TestRecencyAffectsRanking:
    """A more recent pattern should rank above an older one with the same count."""

    def test_recent_beats_old_same_count(self):
        patterns = [
            _make_pattern(pattern_id="old",    error_count=5, days_ago=30.0),
            _make_pattern(pattern_id="recent", error_count=5, days_ago=0.5),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[0]["pattern_id"] == "recent"

    def test_recency_ordering_preserved_across_three(self):
        patterns = [
            _make_pattern(pattern_id="oldest", error_count=3, days_ago=60.0),
            _make_pattern(pattern_id="middle", error_count=3, days_ago=7.0),
            _make_pattern(pattern_id="newest", error_count=3, days_ago=0.0),
        ]
        ranked = rank_patterns(patterns)
        ids = [p["pattern_id"] for p in ranked]
        assert ids.index("newest") < ids.index("middle")
        assert ids.index("middle") < ids.index("oldest")

    def test_very_old_pattern_ranked_last(self):
        patterns = [
            _make_pattern(pattern_id="ancient", error_count=4, days_ago=365.0),
            _make_pattern(pattern_id="today",   error_count=4, days_ago=0.0),
            _make_pattern(pattern_id="week",    error_count=4, days_ago=7.0),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[-1]["pattern_id"] == "ancient"


class TestRecentFrequentHighest:
    """The pattern that is both recent AND frequent must rank above all alternatives."""

    def test_recent_frequent_beats_old_frequent(self):
        patterns = [
            _make_pattern(pattern_id="old-heavy",    error_count=20, days_ago=60.0),
            _make_pattern(pattern_id="recent-heavy", error_count=20, days_ago=0.5),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[0]["pattern_id"] == "recent-heavy"

    def test_recent_frequent_beats_recent_infrequent(self):
        patterns = [
            _make_pattern(pattern_id="recent-light",  error_count=2,  days_ago=0.5),
            _make_pattern(pattern_id="recent-heavy",  error_count=20, days_ago=0.5),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[0]["pattern_id"] == "recent-heavy"

    def test_recent_frequent_top_of_four(self):
        """Recent + frequent beats every combination of stale/low-count."""
        patterns = [
            _make_pattern(pattern_id="old-light",    error_count=2,  days_ago=30.0),
            _make_pattern(pattern_id="old-heavy",    error_count=20, days_ago=30.0),
            _make_pattern(pattern_id="recent-light", error_count=2,  days_ago=0.5),
            _make_pattern(pattern_id="recent-heavy", error_count=20, days_ago=0.5),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[0]["pattern_id"] == "recent-heavy"

    def test_recent_frequent_top_score(self):
        """The winner's rank_score must be strictly greater than all others."""
        patterns = [
            _make_pattern(pattern_id="old-light",    error_count=2,  days_ago=30.0),
            _make_pattern(pattern_id="old-heavy",    error_count=20, days_ago=30.0),
            _make_pattern(pattern_id="recent-light", error_count=2,  days_ago=0.5),
            _make_pattern(pattern_id="recent-heavy", error_count=20, days_ago=0.5),
        ]
        ranked = rank_patterns(patterns)
        top_score = ranked[0]["rank_score"]
        for p in ranked[1:]:
            assert top_score > p["rank_score"], (
                f"Winner score {top_score} not > {p['rank_score']} for {p['pattern_id']}"
            )


class TestTiesBrokenByRecency:
    """When error_count is identical, the more recent pattern wins."""

    def test_same_count_more_recent_wins(self):
        patterns = [
            _make_pattern(pattern_id="older",  error_count=8, days_ago=14.0),
            _make_pattern(pattern_id="newer",  error_count=8, days_ago=1.0),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[0]["pattern_id"] == "newer"

    def test_same_count_three_way_tie_recency_order(self):
        patterns = [
            _make_pattern(pattern_id="a", error_count=5, days_ago=30.0),
            _make_pattern(pattern_id="b", error_count=5, days_ago=7.0),
            _make_pattern(pattern_id="c", error_count=5, days_ago=0.0),
        ]
        ranked = rank_patterns(patterns)
        ids = [p["pattern_id"] for p in ranked]
        assert ids[0] == "c"
        assert ids[-1] == "a"

    def test_same_count_today_vs_yesterday(self):
        patterns = [
            _make_pattern(pattern_id="yesterday", error_count=3, days_ago=1.0),
            _make_pattern(pattern_id="today",     error_count=3, days_ago=0.0),
        ]
        ranked = rank_patterns(patterns)
        assert ranked[0]["pattern_id"] == "today"


class TestEmptyInput:
    """An empty pattern list must return an empty list, not raise."""

    def test_empty_input_returns_empty_list(self):
        result = rank_patterns([])
        assert result == []

    def test_empty_input_returns_list_type(self):
        result = rank_patterns([])
        assert isinstance(result, list)


class TestRankScoreAssigned:
    """Every pattern in the output must carry a rank_score float field."""

    def test_rank_score_assigned_single_pattern(self):
        patterns = [_make_pattern(pattern_id="only", error_count=3, days_ago=2.0)]
        ranked = rank_patterns(patterns)
        assert "rank_score" in ranked[0]
        assert isinstance(ranked[0]["rank_score"], float)

    def test_rank_score_assigned_all_patterns(self):
        patterns = [
            _make_pattern(pattern_id=f"p{i}", error_count=i + 1, days_ago=float(i))
            for i in range(5)
        ]
        ranked = rank_patterns(patterns)
        for p in ranked:
            assert "rank_score" in p, f"rank_score missing from pattern {p.get('pattern_id')}"
            assert isinstance(p["rank_score"], float)

    def test_rank_score_positive(self):
        """rank_score should be positive for any non-trivial pattern."""
        patterns = [_make_pattern(pattern_id="pos", error_count=1, days_ago=0.0)]
        ranked = rank_patterns(patterns)
        assert ranked[0]["rank_score"] > 0.0

    def test_rank_score_decreasing_order(self):
        """The output list is sorted so rank_scores are non-increasing."""
        patterns = [
            _make_pattern(pattern_id=f"p{i}", error_count=(i % 5) + 1, days_ago=float(i % 10))
            for i in range(8)
        ]
        ranked = rank_patterns(patterns)
        scores = [p["rank_score"] for p in ranked]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"rank_score not non-increasing at index {i}: {scores[i]} < {scores[i + 1]}"
            )

    def test_input_patterns_not_mutated_structurally(self):
        """rank_patterns should not drop or add patterns — output count == input count."""
        patterns = [
            _make_pattern(pattern_id=f"p{i}", error_count=i + 1, days_ago=float(i))
            for i in range(6)
        ]
        ranked = rank_patterns(patterns)
        assert len(ranked) == len(patterns)

    def test_all_pattern_ids_preserved(self):
        """No pattern is lost or duplicated during ranking."""
        ids_in = {f"p{i}" for i in range(6)}
        patterns = [
            _make_pattern(pattern_id=pid, error_count=2, days_ago=1.0)
            for pid in ids_in
        ]
        ranked = rank_patterns(patterns)
        ids_out = {p["pattern_id"] for p in ranked}
        assert ids_in == ids_out
