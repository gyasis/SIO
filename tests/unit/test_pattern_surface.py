"""Unit tests for sio.core.dspy.pattern_surface — T050b [US4].

Tests pattern surfacing from labeled invocation data, including
filtering by threshold, empty results, and summary content.
These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sio.core.db.queries import insert_invocation
from sio.core.dspy.pattern_surface import surface_patterns


def _insert_many(conn, factory, records):
    """Helper to bulk-insert invocation records."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestSurfacePatternsOutput:
    """surface_patterns(conn, skill_name) returns a list of pattern dicts."""

    def test_returns_list_of_pattern_dicts(self, tmp_db, sample_invocation):
        """Each pattern has description, count, and affected_sessions keys."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(5)
            ],
        )
        patterns = surface_patterns(tmp_db, skill_name="Read")

        assert isinstance(patterns, list)
        for pattern in patterns:
            assert "description" in pattern, "Pattern must include description"
            assert "count" in pattern, "Pattern must include count"
            assert "affected_sessions" in pattern, "Pattern must include affected_sessions"

    def test_count_reflects_occurrences(self, tmp_db, sample_invocation):
        """Pattern count matches the number of matching failure records."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "correct_action": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(4)
            ],
        )
        patterns = surface_patterns(tmp_db, skill_name="Read")

        if patterns:
            total_count = sum(p["count"] for p in patterns)
            assert total_count >= 4, (
                "Total pattern count should account for all failures"
            )

    def test_affected_sessions_are_distinct(self, tmp_db, sample_invocation):
        """affected_sessions contains unique session IDs only."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": "sess-dup",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "session_id": "sess-dup",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "session_id": "sess-other",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                },
            ],
        )
        patterns = surface_patterns(tmp_db, skill_name="Read")

        for pattern in patterns:
            sessions = pattern["affected_sessions"]
            assert len(sessions) == len(set(sessions)), (
                "affected_sessions must contain only unique session IDs"
            )


class TestSurfacePatternsEmpty:
    """surface_patterns() returns an empty list when no patterns exist."""

    def test_empty_db_returns_empty_list(self, tmp_db):
        """No invocations at all => empty list."""
        patterns = surface_patterns(tmp_db, skill_name="Read")

        assert patterns == []

    def test_no_failures_returns_empty_list(self, tmp_db, sample_invocation):
        """All successful invocations => no failure patterns."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 1,
                    "correct_action": 1,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(5)
            ],
        )
        patterns = surface_patterns(tmp_db, skill_name="Read")

        assert patterns == []


class TestPatternThresholdFiltering:
    """Patterns below the minimum occurrence threshold are excluded."""

    def test_below_threshold_excluded(self, tmp_db, sample_invocation):
        """Patterns with fewer than threshold occurrences are filtered out."""
        # Insert exactly 2 failures — below default threshold of 3
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(2)
            ],
        )
        patterns = surface_patterns(tmp_db, skill_name="Read", min_count=3)

        assert patterns == [], (
            "Patterns with count below min_count should be excluded"
        )

    def test_at_threshold_included(self, tmp_db, sample_invocation):
        """Patterns with exactly min_count occurrences are included."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(3)
            ],
        )
        patterns = surface_patterns(tmp_db, skill_name="Read", min_count=3)

        assert len(patterns) >= 1, (
            "Patterns at exactly min_count should be included"
        )

    def test_custom_threshold(self, tmp_db, sample_invocation):
        """min_count parameter controls the filtering threshold."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(4)
            ],
        )
        # Threshold of 5 should exclude these 4 occurrences
        patterns_high = surface_patterns(tmp_db, skill_name="Read", min_count=5)
        assert patterns_high == []

        # Threshold of 3 should include them
        patterns_low = surface_patterns(tmp_db, skill_name="Read", min_count=3)
        assert len(patterns_low) >= 1


class TestPatternSummaryContent:
    """Pattern summary includes skill name for context."""

    def test_pattern_includes_skill_name(self, tmp_db, sample_invocation):
        """Each pattern dict includes the skill_name it was surfaced for."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(5)
            ],
        )
        patterns = surface_patterns(tmp_db, skill_name="Read")

        for pattern in patterns:
            assert "skill_name" in pattern, "Pattern must include skill_name"
            assert pattern["skill_name"] == "Read"

    def test_different_skills_separated(self, tmp_db, sample_invocation):
        """Patterns are scoped to the requested skill_name only."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-read-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(4)
            ] + [
                {
                    "session_id": f"sess-bash-{i}",
                    "behavior_type": "skill",
                    "actual_action": "Bash",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(4)
            ],
        )
        read_patterns = surface_patterns(tmp_db, skill_name="Read")
        bash_patterns = surface_patterns(tmp_db, skill_name="Bash")

        for p in read_patterns:
            assert p["skill_name"] == "Read"
        for p in bash_patterns:
            assert p["skill_name"] == "Bash"
