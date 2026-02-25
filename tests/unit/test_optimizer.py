"""Unit tests for sio.core.dspy.optimizer — T045 [US4].

Tests prompt optimization quality gates, optimizer selection,
atomic rollback, and recency weighting.
These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from sio.core.db.queries import insert_invocation
from sio.core.dspy.optimizer import optimize, OptimizationError


def _insert_many(conn, factory, records):
    """Helper to bulk-insert invocation records."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


def _make_labeled_failures(factory, *, n_sessions=3, n_per_session=4):
    """Build a list of labeled failure records across distinct sessions.

    Returns enough records to pass all quality gates by default:
    - >= 10 labeled examples (n_sessions * n_per_session = 12)
    - >= 5 failures (all have correct_outcome=0)
    - >= 3 distinct failing sessions
    """
    now = datetime.now(timezone.utc)
    records = []
    for s in range(n_sessions):
        for i in range(n_per_session):
            records.append({
                "session_id": f"sess-{s}",
                "behavior_type": "skill",
                "actual_action": "Read",
                "correct_outcome": 0,
                "correct_action": 0,
                "user_satisfied": 0,
                "labeled_by": "human",
                "labeled_at": (now - timedelta(hours=s * 10 + i)).isoformat(),
                "timestamp": (now - timedelta(hours=s * 10 + i)).isoformat(),
            })
    return records


class TestQualityGateMinimumExamples:
    """optimize() must reject datasets with fewer than 10 labeled examples."""

    def test_too_few_labeled_examples(self, tmp_db, sample_invocation):
        """Returns error when fewer than 10 labeled rows exist."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(9)  # Only 9 labeled examples
            ],
        )
        result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")
        assert result["status"] == "error"
        assert "10" in result["reason"].lower() or "labeled" in result["reason"].lower()

    def test_passes_with_enough_labeled_examples(self, tmp_db, sample_invocation):
        """No quality-gate error when >= 10 labeled examples exist."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

        assert result["status"] != "error" or "labeled" not in result.get("reason", "")


class TestQualityGateMinimumFailures:
    """optimize() must reject datasets with fewer than 5 failure examples."""

    def test_too_few_failures(self, tmp_db, sample_invocation):
        """Returns error when fewer than 5 failure rows exist."""
        # 10 labeled but only 4 failures
        records = [
            {
                "session_id": f"sess-{i}",
                "correct_outcome": 0,
                "labeled_by": "human",
                "labeled_at": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(4)
        ] + [
            {
                "session_id": f"sess-ok-{i}",
                "correct_outcome": 1,
                "correct_action": 1,
                "labeled_by": "human",
                "labeled_at": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(6)
        ]
        _insert_many(tmp_db, sample_invocation, records)

        result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")
        assert result["status"] == "error"
        assert "failure" in result["reason"].lower() or "5" in result["reason"]


class TestPatternThresholdGate:
    """FR-028: optimize() requires >= 3 distinct sessions with failures."""

    def test_too_few_sessions(self, tmp_db, sample_invocation):
        """Returns error when failures come from fewer than 3 sessions."""
        # 10 labeled failures but only 2 distinct sessions
        records = [
            {
                "session_id": f"sess-{i % 2}",
                "correct_outcome": 0,
                "labeled_by": "human",
                "labeled_at": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(10)
        ]
        _insert_many(tmp_db, sample_invocation, records)

        result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")
        assert result["status"] == "error"
        assert "session" in result["reason"].lower() or "3" in result["reason"]

    def test_passes_with_enough_sessions(self, tmp_db, sample_invocation):
        """No session-gate error when failures span >= 3 distinct sessions."""
        records = _make_labeled_failures(sample_invocation, n_sessions=3)
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

        assert result.get("reason", "") == "" or "session" not in result.get("reason", "").lower()


class TestOptimizerSelection:
    """optimize() accepts 'gepa', 'miprov2', 'bootstrap' as optimizer names."""

    @pytest.mark.parametrize("optimizer_name", ["gepa", "miprov2", "bootstrap"])
    def test_valid_optimizer_names(self, tmp_db, sample_invocation, optimizer_name):
        """Each valid optimizer name is accepted without ValueError."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            result = optimize(
                tmp_db, skill_name="Read", optimizer=optimizer_name
            )

        # Should not error on optimizer name
        if result["status"] == "error":
            assert "optimizer" not in result["reason"].lower()

    def test_invalid_optimizer_raises(self, tmp_db, sample_invocation):
        """Unknown optimizer name raises ValueError."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        with pytest.raises(ValueError, match="optimizer"):
            optimize(tmp_db, skill_name="Read", optimizer="nonexistent_optimizer")


class TestAtomicRollback:
    """If optimization raises, database state must be unchanged."""

    def test_rollback_on_failure(self, tmp_db, sample_invocation):
        """DB optimization_runs table is unchanged after an internal error."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        runs_before = tmp_db.execute(
            "SELECT COUNT(*) FROM optimization_runs"
        ).fetchone()[0]

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.side_effect = RuntimeError("DSPy internal error")

            with pytest.raises((RuntimeError, OptimizationError)):
                optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

        runs_after = tmp_db.execute(
            "SELECT COUNT(*) FROM optimization_runs"
        ).fetchone()[0]

        assert runs_after == runs_before, (
            "optimization_runs should be unchanged after a failed optimization"
        )


class TestRecencyWeighting:
    """More recent examples should get higher weight in the exported dataset."""

    def test_recent_examples_weighted_higher(self, tmp_db, sample_invocation):
        """Dataset export assigns higher weight to recent examples."""
        now = datetime.now(timezone.utc)
        records = []
        # Old examples (30 days ago)
        for i in range(5):
            records.append({
                "session_id": f"sess-old-{i}",
                "correct_outcome": 0,
                "labeled_by": "human",
                "labeled_at": (now - timedelta(days=30)).isoformat(),
                "timestamp": (now - timedelta(days=30)).isoformat(),
            })
        # Recent examples (1 day ago)
        for i in range(5):
            records.append({
                "session_id": f"sess-new-{i}",
                "correct_outcome": 0,
                "labeled_by": "human",
                "labeled_at": (now - timedelta(days=1)).isoformat(),
                "timestamp": (now - timedelta(days=1)).isoformat(),
            })
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

            # Inspect the dataset passed to the mock
            assert mock_opt.called
            call_kwargs = mock_opt.call_args
            dataset = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("dataset")

            # Recent examples should have higher weights
            recent_weights = [
                ex["weight"] for ex in dataset if "new" in ex.get("session_id", "")
            ]
            old_weights = [
                ex["weight"] for ex in dataset if "old" in ex.get("session_id", "")
            ]

            assert all(r > o for r, o in zip(recent_weights, old_weights)), (
                "Recent examples must have higher weight than old examples"
            )
