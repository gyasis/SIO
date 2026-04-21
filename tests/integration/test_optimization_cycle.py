"""T048 [US4] Integration test -- full prompt optimization cycle.

Seeds the database with labeled invocations and verifies that:
- The optimizer produces a result dict with status, diff, and optimization_id
- Quality gates pass (min examples, min failures, min sessions)
- An OptimizationRun record is created with status='pending'
- DSPy optimization is mocked (no real optimizer execution)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from sio.core.db.queries import insert_invocation
from sio.core.dspy.optimizer import (
    OptimizationResult,
    check_quality_gates,
    optimize,
    run_optimization,
)


def _seed_invocations(conn, factory):
    """Seed DB with 15 labeled invocations for the Read skill.

    Layout:
        - 10 failures across 3 sessions
        - 5 successes in 2 additional sessions
    """
    ids: list[int] = []
    failure_sessions = ["fail-sess-001", "fail-sess-002", "fail-sess-003"]
    failures_per = [4, 3, 3]
    seq = 0

    for session_id, count in zip(failure_sessions, failures_per):
        for j in range(count):
            record = factory(
                session_id=session_id,
                platform="claude-code",
                tool_name="Read",
                user_message=f"Read file number {seq}",
                user_satisfied=0,
                correct_outcome=0,
                labeled_by="auto",
                labeled_at=datetime.now(timezone.utc).isoformat(),
                timestamp=f"2026-01-10T{seq:02d}:00:00+00:00",
            )
            ids.append(insert_invocation(conn, record))
            seq += 1

    success_sessions = ["ok-sess-001", "ok-sess-002"]
    successes_per = [3, 2]
    for session_id, count in zip(success_sessions, successes_per):
        for j in range(count):
            record = factory(
                session_id=session_id,
                platform="claude-code",
                tool_name="Read",
                user_message=f"Read good file {seq}",
                user_satisfied=1,
                correct_outcome=1,
                labeled_by="auto",
                labeled_at=datetime.now(timezone.utc).isoformat(),
                timestamp=f"2026-01-10T{seq:02d}:00:00+00:00",
            )
            ids.append(insert_invocation(conn, record))
            seq += 1

    return ids


@pytest.fixture
def seeded_db(tmp_db, sample_invocation):
    """Return (conn, row_ids) with 15 labeled invocations."""
    ids = _seed_invocations(tmp_db, sample_invocation)
    return tmp_db, ids


class TestQualityGates:
    """Quality gate checks must pass given seeded data."""

    def test_gates_pass_with_sufficient_data(self, seeded_db):
        conn, _ = seeded_db
        result = check_quality_gates(
            conn,
            skill="Read",
            platform="claude-code",
        )
        assert result.passed is True, f"Quality gates should pass: {result.reason}"

    def test_gates_report_example_count(self, seeded_db):
        conn, _ = seeded_db
        result = check_quality_gates(
            conn,
            skill="Read",
            platform="claude-code",
        )
        assert result.example_count == 15

    def test_gates_report_failure_count(self, seeded_db):
        conn, _ = seeded_db
        result = check_quality_gates(
            conn,
            skill="Read",
            platform="claude-code",
        )
        assert result.failure_count == 10

    def test_gates_report_session_count(self, seeded_db):
        conn, _ = seeded_db
        result = check_quality_gates(
            conn,
            skill="Read",
            platform="claude-code",
        )
        assert result.session_count >= 3

    def test_gates_fail_insufficient_examples(self, tmp_db, sample_invocation):
        for i in range(5):
            insert_invocation(
                tmp_db,
                sample_invocation(
                    session_id=f"few-{i}",
                    platform="claude-code",
                    tool_name="Read",
                    user_satisfied=0,
                    correct_outcome=0,
                    labeled_by="auto",
                    labeled_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
        result = check_quality_gates(
            tmp_db,
            skill="Read",
            platform="claude-code",
        )
        assert result.passed is False
        assert "example" in result.reason.lower()

    def test_gates_fail_insufficient_failures(self, tmp_db, sample_invocation):
        for i in range(12):
            insert_invocation(
                tmp_db,
                sample_invocation(
                    session_id=f"succ-{i}",
                    platform="claude-code",
                    tool_name="Read",
                    user_satisfied=1,
                    correct_outcome=1,
                    labeled_by="auto",
                    labeled_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
        for i in range(2):
            insert_invocation(
                tmp_db,
                sample_invocation(
                    session_id=f"fail-{i}",
                    platform="claude-code",
                    tool_name="Read",
                    user_satisfied=0,
                    correct_outcome=0,
                    labeled_by="auto",
                    labeled_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
        result = check_quality_gates(
            tmp_db,
            skill="Read",
            platform="claude-code",
        )
        assert result.passed is False
        assert "failure" in result.reason.lower()

    def test_gates_fail_insufficient_sessions(self, tmp_db, sample_invocation):
        for i in range(12):
            insert_invocation(
                tmp_db,
                sample_invocation(
                    session_id="only-sess-A" if i < 6 else "only-sess-B",
                    platform="claude-code",
                    tool_name="Read",
                    user_satisfied=0,
                    correct_outcome=0,
                    labeled_by="auto",
                    labeled_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
        result = check_quality_gates(
            tmp_db,
            skill="Read",
            platform="claude-code",
        )
        assert result.passed is False
        assert "session" in result.reason.lower()


class TestRunOptimization:
    """run_optimization should produce a result dict and create an OptimizationRun."""

    @patch("sio.core.dspy.optimizer._run_dspy_optimization")
    def test_optimize_returns_result_dict(self, mock_opt, seeded_db):
        conn, _ = seeded_db
        mock_opt.return_value = {"proposed_diff": "--- a\n+++ b", "score": 0.8}

        result = run_optimization(conn, skill="Read", platform="claude-code")

        assert isinstance(result, dict)
        assert "status" in result
        assert "diff" in result
        assert "optimization_id" in result

    @patch("sio.core.dspy.optimizer._run_dspy_optimization")
    def test_optimize_status_is_pending(self, mock_opt, seeded_db):
        conn, _ = seeded_db
        mock_opt.return_value = {"proposed_diff": "--- a\n+++ b", "score": 0.8}

        result = run_optimization(conn, skill="Read", platform="claude-code")
        assert result["status"] == "pending"

    @patch("sio.core.dspy.optimizer._run_dspy_optimization")
    def test_optimize_creates_optimization_run_record(self, mock_opt, seeded_db):
        conn, _ = seeded_db
        mock_opt.return_value = {"proposed_diff": "--- a\n+++ b", "score": 0.8}

        result = run_optimization(conn, skill="Read", platform="claude-code")

        opt_id = result["optimization_id"]
        row = conn.execute("SELECT * FROM optimization_runs WHERE id = ?", (opt_id,)).fetchone()

        assert row is not None
        assert row["status"] == "pending"
        assert row["skill_name"] == "Read"
        assert row["platform"] == "claude-code"
        assert row["example_count"] == 15

    @patch("sio.core.dspy.optimizer._run_dspy_optimization")
    def test_optimize_records_before_satisfaction(self, mock_opt, seeded_db):
        conn, _ = seeded_db
        mock_opt.return_value = {"proposed_diff": "--- a\n+++ b", "score": 0.8}

        result = run_optimization(conn, skill="Read", platform="claude-code")

        opt_id = result["optimization_id"]
        row = conn.execute(
            "SELECT before_satisfaction FROM optimization_runs WHERE id = ?",
            (opt_id,),
        ).fetchone()
        # 5 satisfied / 15 total = 0.333
        assert row["before_satisfaction"] == pytest.approx(5.0 / 15.0, abs=0.01)

    @patch("sio.core.dspy.optimizer._run_dspy_optimization")
    def test_optimize_diff_is_nonempty_string(self, mock_opt, seeded_db):
        conn, _ = seeded_db
        mock_opt.return_value = {"proposed_diff": "--- a\n+++ b", "score": 0.8}

        result = run_optimization(conn, skill="Read", platform="claude-code")
        assert isinstance(result["diff"], str)
        assert len(result["diff"]) > 0

    def test_optimize_fails_when_gates_fail(self, tmp_db, sample_invocation):
        for i in range(3):
            insert_invocation(
                tmp_db,
                sample_invocation(
                    session_id=f"tiny-{i}",
                    platform="claude-code",
                    tool_name="Read",
                    user_satisfied=0,
                    correct_outcome=0,
                    labeled_by="auto",
                    labeled_at=datetime.now(timezone.utc).isoformat(),
                ),
            )
        result = optimize(tmp_db, skill_name="Read")
        assert result["status"] == "error"


class TestOptimizationResult:
    """OptimizationResult dataclass contract."""

    def test_optimization_result_has_required_fields(self):
        result = OptimizationResult(
            passed=True,
            reason="",
            example_count=15,
            failure_count=10,
            session_count=5,
        )
        assert result.passed is True
        assert result.example_count == 15
        assert result.failure_count == 10
        assert result.session_count == 5
