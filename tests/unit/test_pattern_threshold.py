"""Unit tests for sio.core.telemetry.pattern_detector — T040 [US3].

Tests pattern counting and optimization candidacy detection.
These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

import pytest

from sio.core.db.queries import insert_invocation
from sio.core.telemetry.pattern_detector import (
    count_pattern_occurrences,
    is_optimization_candidate,
)


def _insert_many(conn, factory, records):
    """Helper to bulk-insert invocation records."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestCountPatternOccurrences:
    """count_pattern_occurrences(conn, behavior_type, failure_mode) -> int."""

    def test_count_occurrences(self, tmp_db, sample_invocation):
        """Counts records matching the given behavior_type + failure_mode."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {"behavior_type": "skill", "correct_outcome": 0},
                {"behavior_type": "skill", "correct_outcome": 0},
                {"behavior_type": "skill", "correct_outcome": 1},
                {"behavior_type": "mcp_tool", "correct_outcome": 0},
            ],
        )
        count = count_pattern_occurrences(
            tmp_db, behavior_type="skill", failure_mode="incorrect_outcome"
        )
        assert count == 2

    def test_count_zero_on_empty_db(self, tmp_db):
        """Empty database returns 0."""
        assert (
            count_pattern_occurrences(
                tmp_db,
                behavior_type="skill",
                failure_mode="incorrect_outcome",
            )
            == 0
        )

    def test_count_not_activated(self, tmp_db, sample_invocation):
        """Counts not-activated failures correctly."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {"behavior_type": "skill", "activated": 0},
                {"behavior_type": "skill", "activated": 1},
            ],
        )
        count = count_pattern_occurrences(
            tmp_db, behavior_type="skill", failure_mode="not_activated"
        )
        assert count == 1


class TestIsOptimizationCandidate:
    """is_optimization_candidate(conn, skill_name, threshold=3) -> bool."""

    def test_is_candidate_above_threshold(self, tmp_db, sample_invocation):
        """Same failure pattern across >=3 DISTINCT sessions => True."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": "sess-a",
                    "tool_name": "Read",
                    "correct_outcome": 0,
                },
                {
                    "session_id": "sess-b",
                    "tool_name": "Read",
                    "correct_outcome": 0,
                },
                {
                    "session_id": "sess-c",
                    "tool_name": "Read",
                    "correct_outcome": 0,
                },
            ],
        )
        assert is_optimization_candidate(tmp_db, skill_name="Read") is True

    def test_not_candidate_single_session(self, tmp_db, sample_invocation):
        """All failures in 1 session => False (not cross-session pattern)."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": "sess-only",
                    "tool_name": "Read",
                    "correct_outcome": 0,
                },
                {
                    "session_id": "sess-only",
                    "tool_name": "Read",
                    "correct_outcome": 0,
                },
                {
                    "session_id": "sess-only",
                    "tool_name": "Read",
                    "correct_outcome": 0,
                },
            ],
        )
        assert is_optimization_candidate(tmp_db, skill_name="Read") is False

    def test_configurable_threshold(self, tmp_db, sample_invocation):
        """Threshold can be raised; 3 sessions should not meet threshold=5."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "tool_name": "Bash",
                    "correct_outcome": 0,
                }
                for i in range(3)
            ],
        )
        assert (
            is_optimization_candidate(tmp_db, skill_name="Bash", threshold=5)
            is False
        )

    def test_candidate_at_exact_threshold(self, tmp_db, sample_invocation):
        """Exactly meeting the threshold returns True."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "tool_name": "Bash",
                    "correct_outcome": 0,
                }
                for i in range(5)
            ],
        )
        assert (
            is_optimization_candidate(tmp_db, skill_name="Bash", threshold=5)
            is True
        )
