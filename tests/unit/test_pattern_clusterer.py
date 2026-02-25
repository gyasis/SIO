"""T022 [US2] Unit tests for sio.clustering.pattern_clusterer — embedding-based error grouping.

Tests cover cluster_errors(errors, threshold) which takes a list of error record
dicts (with an ``error_text`` field) and returns a list of pattern dicts grouped
by semantic similarity.

These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sio.clustering.pattern_clusterer import cluster_errors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_PATTERN_KEYS = frozenset(
    {
        "pattern_id",
        "description",
        "tool_name",
        "error_count",
        "session_count",
        "first_seen",
        "last_seen",
        "rank_score",
        "error_ids",
    }
)


def _make_error(
    error_text: str,
    *,
    id: int = 1,
    session_id: str = "session-001",
    tool_name: str | None = "Read",
    timestamp: str | None = None,
) -> dict:
    """Construct a minimal error record dict matching the error_records schema."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "id": id,
        "session_id": session_id,
        "timestamp": timestamp,
        "tool_name": tool_name,
        "error_text": error_text,
    }


def _make_errors(texts: list[str], *, base_session: str = "session-001") -> list[dict]:
    """Build a list of error records from a list of text strings."""
    return [
        _make_error(text, id=i + 1, session_id=base_session)
        for i, text in enumerate(texts)
    ]


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestIdenticalErrorsClusterTogether:
    """Five errors with the same text must collapse into exactly one pattern."""

    def test_identical_errors_cluster_together(self):
        repeated_text = "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/foo.py'"
        errors = [_make_error(repeated_text, id=i + 1) for i in range(5)]

        patterns = cluster_errors(errors)

        assert len(patterns) == 1
        assert patterns[0]["error_count"] == 5


class TestSimilarErrorsClusterTogether:
    """Errors that differ only in the path suffix should land in the same cluster."""

    def test_similar_errors_cluster_together(self):
        errors = [
            _make_error("File not found: /tmp/a.py", id=1),
            _make_error("File not found: /tmp/b.py", id=2),
        ]

        patterns = cluster_errors(errors)

        # Both are near-identical — expect a single pattern.
        assert len(patterns) == 1

    def test_similar_errors_ids_captured(self):
        errors = [
            _make_error("File not found: /tmp/a.py", id=1),
            _make_error("File not found: /tmp/b.py", id=2),
        ]

        patterns = cluster_errors(errors)

        assert set(patterns[0]["error_ids"]) == {1, 2}


class TestDifferentErrorsStaySeparate:
    """Semantically distinct error categories must produce separate patterns."""

    def test_different_errors_stay_separate(self):
        errors = [
            _make_error("FileNotFoundError: No such file or directory: '/tmp/x.py'", id=1),
            _make_error("FileNotFoundError: No such file or directory: '/tmp/y.py'", id=2),
            _make_error("CommandTimeoutError: tool execution exceeded 30s limit", id=3),
            _make_error("CommandTimeoutError: command timed out after 60 seconds", id=4),
        ]

        patterns = cluster_errors(errors)

        assert len(patterns) == 2

    def test_different_errors_ids_disjoint(self):
        errors = [
            _make_error("FileNotFoundError: No such file or directory: '/tmp/x.py'", id=1),
            _make_error("FileNotFoundError: No such file or directory: '/tmp/y.py'", id=2),
            _make_error("CommandTimeoutError: tool execution exceeded 30s limit", id=3),
            _make_error("CommandTimeoutError: command timed out after 60 seconds", id=4),
        ]

        patterns = cluster_errors(errors)

        id_sets = [set(p["error_ids"]) for p in patterns]
        # Confirm the two clusters are disjoint.
        assert id_sets[0].isdisjoint(id_sets[1])

    def test_all_error_ids_accounted_for(self):
        errors = [
            _make_error("FileNotFoundError: No such file or directory: '/tmp/x.py'", id=1),
            _make_error("FileNotFoundError: No such file or directory: '/tmp/y.py'", id=2),
            _make_error("CommandTimeoutError: tool execution exceeded 30s limit", id=3),
            _make_error("CommandTimeoutError: command timed out after 60 seconds", id=4),
        ]

        patterns = cluster_errors(errors)

        all_ids: set[int] = set()
        for p in patterns:
            all_ids.update(p["error_ids"])
        assert all_ids == {1, 2, 3, 4}


class TestConfigurableThreshold:
    """The threshold parameter controls cluster tightness."""

    # Three errors: two near-identical, one moderately similar.
    _ERRORS = [
        _make_error("File not found: /tmp/a.py", id=1),
        _make_error("File not found: /tmp/b.py", id=2),
        _make_error("Could not locate file /home/user/config.yaml", id=3),
    ]

    def test_tight_threshold_produces_more_patterns(self):
        """threshold=0.99 separates strings that differ even slightly."""
        patterns_tight = cluster_errors(self._ERRORS, threshold=0.99)
        patterns_loose = cluster_errors(self._ERRORS, threshold=0.50)
        assert len(patterns_tight) >= len(patterns_loose)

    def test_loose_threshold_groups_more(self):
        """threshold=0.50 allows more aggressive grouping."""
        errors = [
            _make_error("File not found: /tmp/a.py", id=1),
            _make_error("File not found: /tmp/b.py", id=2),
            _make_error("File not found: /tmp/c.py", id=3),
            _make_error("CommandTimeoutError: tool execution exceeded 30s limit", id=4),
            _make_error("CommandTimeoutError: command timed out after 60 seconds", id=5),
        ]
        patterns = cluster_errors(errors, threshold=0.50)
        # At a loose threshold the two semantically distinct groups still separate,
        # but we confirm the total is at most 2 rather than 5.
        assert len(patterns) <= 2

    def test_strict_threshold_separates_exact_duplicates_less(self):
        """Even at threshold=0.99, truly identical strings must stay together."""
        same_text = "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/x.py'"
        errors = [_make_error(same_text, id=i + 1) for i in range(3)]
        patterns = cluster_errors(errors, threshold=0.99)
        # Identical embeddings produce cosine similarity = 1.0 > any threshold.
        assert len(patterns) == 1


class TestEmptyInput:
    """An empty error list must return an empty pattern list."""

    def test_empty_input_returns_empty_list(self):
        result = cluster_errors([])
        assert result == []

    def test_empty_input_returns_list_type(self):
        result = cluster_errors([])
        assert isinstance(result, list)


class TestPatternHasRequiredKeys:
    """Every returned pattern dict must carry all documented fields."""

    def test_pattern_has_required_keys(self):
        errors = _make_errors(["File not found: /tmp/a.py", "File not found: /tmp/b.py"])
        patterns = cluster_errors(errors)
        for pattern in patterns:
            missing = _REQUIRED_PATTERN_KEYS - set(pattern.keys())
            assert not missing, f"Pattern missing keys: {missing}"

    def test_pattern_id_is_str(self):
        errors = _make_errors(["File not found: /tmp/a.py"])
        patterns = cluster_errors(errors)
        assert isinstance(patterns[0]["pattern_id"], str)

    def test_error_ids_is_list(self):
        errors = _make_errors(["File not found: /tmp/a.py"])
        patterns = cluster_errors(errors)
        assert isinstance(patterns[0]["error_ids"], list)

    def test_rank_score_is_float(self):
        errors = _make_errors(["File not found: /tmp/a.py"])
        patterns = cluster_errors(errors)
        assert isinstance(patterns[0]["rank_score"], float)

    def test_error_count_is_int(self):
        errors = _make_errors(["File not found: /tmp/a.py"])
        patterns = cluster_errors(errors)
        assert isinstance(patterns[0]["error_count"], int)

    def test_session_count_is_int(self):
        errors = _make_errors(["File not found: /tmp/a.py"])
        patterns = cluster_errors(errors)
        assert isinstance(patterns[0]["session_count"], int)

    def test_tool_name_is_str_or_none(self):
        errors = _make_errors(["File not found: /tmp/a.py"])
        patterns = cluster_errors(errors)
        assert patterns[0]["tool_name"] is None or isinstance(patterns[0]["tool_name"], str)

    def test_timestamps_are_str(self):
        errors = _make_errors(["File not found: /tmp/a.py"])
        patterns = cluster_errors(errors)
        assert isinstance(patterns[0]["first_seen"], str)
        assert isinstance(patterns[0]["last_seen"], str)


class TestErrorCountMatches:
    """pattern error_count must equal the number of error_ids in the cluster."""

    def test_error_count_matches_single_cluster(self):
        errors = [_make_error("FileNotFoundError: /tmp/a.py", id=i + 1) for i in range(7)]
        patterns = cluster_errors(errors)
        for pattern in patterns:
            assert pattern["error_count"] == len(pattern["error_ids"])

    def test_error_count_matches_multiple_clusters(self):
        errors = [
            _make_error("File not found: /tmp/a.py", id=1),
            _make_error("File not found: /tmp/b.py", id=2),
            _make_error("CommandTimeoutError: tool execution exceeded 30s limit", id=3),
            _make_error("CommandTimeoutError: command timed out after 60 seconds", id=4),
        ]
        patterns = cluster_errors(errors)
        for pattern in patterns:
            assert pattern["error_count"] == len(pattern["error_ids"])

    def test_total_error_count_equals_input_size(self):
        """Sum of all cluster error_counts must equal total input errors."""
        errors = [
            _make_error("File not found: /tmp/a.py", id=1),
            _make_error("File not found: /tmp/b.py", id=2),
            _make_error("CommandTimeoutError: tool execution exceeded 30s limit", id=3),
        ]
        patterns = cluster_errors(errors)
        total = sum(p["error_count"] for p in patterns)
        assert total == len(errors)


class TestSessionCount:
    """session_count must reflect how many distinct sessions contributed to a cluster."""

    def test_session_count_three_sessions(self):
        """Errors from 3 distinct sessions → session_count == 3."""
        errors = [
            _make_error("File not found: /tmp/a.py", id=1, session_id="session-001"),
            _make_error("File not found: /tmp/b.py", id=2, session_id="session-002"),
            _make_error("File not found: /tmp/c.py", id=3, session_id="session-003"),
        ]
        patterns = cluster_errors(errors)
        assert len(patterns) == 1
        assert patterns[0]["session_count"] == 3

    def test_session_count_same_session(self):
        """Multiple errors from one session → session_count == 1."""
        errors = [
            _make_error("File not found: /tmp/a.py", id=1, session_id="session-001"),
            _make_error("File not found: /tmp/b.py", id=2, session_id="session-001"),
            _make_error("File not found: /tmp/c.py", id=3, session_id="session-001"),
        ]
        patterns = cluster_errors(errors)
        assert len(patterns) == 1
        assert patterns[0]["session_count"] == 1

    def test_session_count_two_of_three_sessions(self):
        """Two errors from session-001, one from session-002 → session_count == 2."""
        errors = [
            _make_error("File not found: /tmp/a.py", id=1, session_id="session-001"),
            _make_error("File not found: /tmp/b.py", id=2, session_id="session-001"),
            _make_error("File not found: /tmp/c.py", id=3, session_id="session-002"),
        ]
        patterns = cluster_errors(errors)
        assert len(patterns) == 1
        assert patterns[0]["session_count"] == 2

    def test_session_count_per_cluster_independent(self):
        """session_count is computed per cluster, not globally."""
        errors = [
            # Cluster A — 2 sessions
            _make_error("File not found: /tmp/a.py", id=1, session_id="session-001"),
            _make_error("File not found: /tmp/b.py", id=2, session_id="session-002"),
            # Cluster B — 1 session
            _make_error("CommandTimeoutError: tool execution exceeded 30s limit", id=3, session_id="session-001"),
            _make_error("CommandTimeoutError: command timed out after 60 seconds", id=4, session_id="session-001"),
        ]
        patterns = cluster_errors(errors)
        assert len(patterns) == 2

        # Sort by session_count descending so we can assert predictably.
        by_sessions = sorted(patterns, key=lambda p: p["session_count"], reverse=True)
        assert by_sessions[0]["session_count"] == 2
        assert by_sessions[1]["session_count"] == 1
