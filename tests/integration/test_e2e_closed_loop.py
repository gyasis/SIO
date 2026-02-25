"""T080 End-to-end closed-loop integration test.

Tests the full SIO pipeline:
install → capture telemetry → label feedback → detect passive signals →
detect recurring pattern → optimize → arena validates → verify result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from sio.core.db.queries import insert_invocation
from sio.core.dspy.optimizer import optimize
from sio.core.health.aggregator import compute_health
from sio.core.telemetry.auto_labeler import auto_label
from sio.core.telemetry.passive_signals import detect_correction


class TestClosedLoop:
    """Full closed-loop: capture → label → detect → optimize → validate."""

    def test_full_pipeline(self, tmp_db, sample_invocation):
        """End-to-end: seed data, label, optimize, verify result."""
        conn = tmp_db

        # Step 1: Capture telemetry (simulate 15 tool calls)
        sessions = {
            "sess-A": 4, "sess-B": 3, "sess-C": 3,
            "sess-D": 3, "sess-E": 2,
        }
        inv_ids = []
        seq = 0
        for session_id, count in sessions.items():
            for j in range(count):
                is_failure = session_id in ("sess-A", "sess-B", "sess-C")
                record = sample_invocation(
                    session_id=session_id,
                    platform="claude-code",
                    tool_name="Read",
                    user_message=f"Read file {seq}.py",
                    user_satisfied=0 if is_failure else 1,
                    correct_outcome=0 if is_failure else 1,
                    labeled_by="auto",
                    labeled_at=datetime.now(timezone.utc).isoformat(),
                    timestamp=f"2026-01-10T{seq:02d}:00:00+00:00",
                )
                inv_ids.append(insert_invocation(conn, record))
                seq += 1

        assert len(inv_ids) == 15

        # Step 2: Verify auto-labeler produces labels
        label = auto_label(
            tool_name="Read", tool_input="foo.py",
            tool_output="file contents", error=None,
        )
        assert label["activated"] == 1
        assert label["correct_outcome"] == 1

        # Step 3: Verify passive signal detection
        assert detect_correction("No, read the other file") is True
        assert detect_correction("Read foo.py") is False

        # Step 4: Check health dashboard
        health = compute_health(conn, platform="claude-code")
        assert len(health) >= 1
        read_health = [h for h in health if h.skill_name == "Read"]
        assert len(read_health) == 1
        assert read_health[0].total_invocations == 15

        # Step 5: Run optimization (mocked DSPy)
        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {
                "proposed_diff": "# Improved Read prompt\n",
                "score": 0.8,
            }
            result = optimize(
                conn, skill_name="Read",
                platform="claude-code", optimizer="gepa",
            )

        assert result["status"] == "pending"
        assert result["optimization_id"] is not None
        assert len(result["diff"]) > 0

        # Step 6: Verify optimization run recorded
        opt_id = result["optimization_id"]
        row = conn.execute(
            "SELECT * FROM optimization_runs WHERE id = ?",
            (opt_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "pending"
        assert row["skill_name"] == "Read"
        assert row["example_count"] == 15

    def test_pipeline_rejects_insufficient_data(self, tmp_db, sample_invocation):
        """Pipeline should refuse optimization with insufficient data."""
        conn = tmp_db

        # Only 3 records — not enough
        for i in range(3):
            insert_invocation(
                conn,
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

        result = optimize(conn, skill_name="Read")
        assert result["status"] == "error"
        assert "example" in result["reason"].lower()
