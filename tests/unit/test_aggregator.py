"""Unit tests for sio.core.health.aggregator — T066 [US6].

Tests the health aggregation layer that computes per-skill health metrics.
These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

import pytest

from sio.core.db.queries import insert_invocation
from sio.core.health.aggregator import compute_health


def _insert_many(conn, factory, records):
    """Helper to bulk-insert invocation records."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestComputeHealth:
    """compute_health(conn, platform=None, skill=None) -> list[SkillHealth]."""

    def test_compute_health_correct_counts(self, tmp_db, sample_invocation):
        """Aggregated counts match inserted data."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "tool_name": "Read",
                    "platform": "claude-code",
                    "user_satisfied": 1,
                },
                {
                    "tool_name": "Read",
                    "platform": "claude-code",
                    "user_satisfied": 0,
                },
                {
                    "tool_name": "Read",
                    "platform": "claude-code",
                    "user_satisfied": None,
                },
                {
                    "tool_name": "Bash",
                    "platform": "claude-code",
                    "user_satisfied": 1,
                },
            ],
        )
        results = compute_health(tmp_db, platform="claude-code")

        # Find the Read skill entry
        read_health = None
        for entry in results:
            skill = (
                entry.skill_name
                if hasattr(entry, "skill_name")
                else entry["skill_name"]
            )
            if skill == "Read":
                read_health = entry
                break

        assert read_health is not None

        # Access fields — support both dataclass and dict
        def _get(obj, key):
            return getattr(obj, key) if hasattr(obj, key) else obj[key]

        assert _get(read_health, "total_invocations") == 3
        assert _get(read_health, "satisfied_count") == 1
        assert _get(read_health, "unsatisfied_count") == 1
        assert _get(read_health, "unlabeled_count") == 1

    def test_satisfaction_rate_calculation(self, tmp_db, sample_invocation):
        """2 satisfied / 3 total labeled = 0.667 (rounded)."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "tool_name": "Grep",
                    "platform": "claude-code",
                    "user_satisfied": 1,
                },
                {
                    "tool_name": "Grep",
                    "platform": "claude-code",
                    "user_satisfied": 1,
                },
                {
                    "tool_name": "Grep",
                    "platform": "claude-code",
                    "user_satisfied": 0,
                },
            ],
        )
        results = compute_health(tmp_db, platform="claude-code", skill="Grep")
        assert len(results) == 1

        entry = results[0]

        def _get(obj, key):
            return getattr(obj, key) if hasattr(obj, key) else obj[key]

        rate = _get(entry, "satisfaction_rate")
        assert rate == pytest.approx(2 / 3, abs=0.01)

    def test_skills_below_50_flagged(self, tmp_db, sample_invocation):
        """Skills with satisfaction_rate < 0.5 should have flagged=True."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "tool_name": "Write",
                    "platform": "claude-code",
                    "user_satisfied": 1,
                },
                {
                    "tool_name": "Write",
                    "platform": "claude-code",
                    "user_satisfied": 0,
                },
                {
                    "tool_name": "Write",
                    "platform": "claude-code",
                    "user_satisfied": 0,
                },
                {
                    "tool_name": "Write",
                    "platform": "claude-code",
                    "user_satisfied": 0,
                },
            ],
        )
        results = compute_health(
            tmp_db, platform="claude-code", skill="Write"
        )
        assert len(results) == 1

        entry = results[0]

        def _get(obj, key):
            return getattr(obj, key) if hasattr(obj, key) else obj[key]

        rate = _get(entry, "satisfaction_rate")
        assert rate < 0.5
        assert _get(entry, "flagged") is True

    def test_compute_health_all_platforms(self, tmp_db, sample_invocation):
        """Passing platform=None returns results across all platforms."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "tool_name": "Read",
                    "platform": "claude-code",
                    "user_satisfied": 1,
                },
                {
                    "tool_name": "Read",
                    "platform": "cursor",
                    "user_satisfied": 1,
                },
            ],
        )
        results = compute_health(tmp_db)
        # Should return entries for both platforms
        assert len(results) >= 2
