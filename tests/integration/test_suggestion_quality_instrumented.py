"""Integration tests for T107/T108: SuggestionGenerator instrumentation (FR-029).

Tests that SuggestionGenerator produces per-run counters in
suggestions.instrumentation_json and captures per-stage rejection reasons.

These tests are intentionally RED until T108 wires the instrumentation
(rejection-reason capture in dspy_generator.py).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from sio.core.db.schema import init_db
from sio.suggestions.dspy_generator import SuggestionGenerator


def _seed_patterns(db: sqlite3.Connection, count: int = 5) -> None:
    """Seed the patterns table with synthetic data."""
    from sio.core.util.time import utc_now_iso

    now = utc_now_iso()
    for i in range(count):
        db.execute(
            """
            INSERT OR IGNORE INTO patterns
                (pattern_id, description, tool_name, error_count, session_count,
                 centroid_text, first_seen, last_seen, approved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"pat-{i:03d}",
                f"Error pattern {i}: tool call fails on large input",
                "Bash",
                i + 3,
                i + 1,
                f"Tool call fails: pattern {i}",
                now,
                now,
                0,
            ),
        )
    db.commit()


@pytest.fixture()
def db_with_patterns() -> sqlite3.Connection:
    """In-memory DB seeded with 5 synthetic patterns."""
    conn = init_db(":memory:")
    _seed_patterns(conn, count=5)
    yield conn
    conn.close()


class TestSuggestionQualityInstrumented:
    """SuggestionGenerator exposes per-run instrumentation after forward()."""

    @pytest.mark.slow
    def test_forward_produces_instrumentation_json(self, db_with_patterns):
        """After calling forward(), the result should expose instrumentation_json
        with per-run counters (backtrack_count, forward_count).

        This test is RED until T108 wires the instrumentation pipeline.
        """
        gen = SuggestionGenerator()
        pred = gen.forward(
            pattern_description="Tool call fails on large input",
            example_errors=["Bash: command too long", "Bash: arg list too long"],
            project_context="SIO pipeline integrity",
        )
        # T108 deliverable: SuggestionGenerator.forward() must attach instrumentation
        assert hasattr(pred, "instrumentation_json"), (
            "Prediction must expose instrumentation_json (T108 deliverable)"
        )
        instrumentation = json.loads(pred.instrumentation_json)
        assert "backtrack_count" in instrumentation, (
            "instrumentation_json must contain backtrack_count"
        )
        assert "forward_count" in instrumentation, (
            "instrumentation_json must contain forward_count"
        )

    @pytest.mark.slow
    def test_forward_captures_rejection_reasons(self, db_with_patterns):
        """Rejection reasons for each stage (format_valid, no_phi) must be
        captured in instrumentation_json.

        This test is RED until T108 wires the rejection-reason pipeline.
        """
        gen = SuggestionGenerator()
        pred = gen.forward(
            pattern_description="PHI leak in error logs",
            example_errors=["Error: SSN 123-45-6789 found in output"],
            project_context="SIO pipeline integrity",
        )
        assert hasattr(pred, "instrumentation_json"), (
            "Prediction must expose instrumentation_json (T108 deliverable)"
        )
        instrumentation = json.loads(pred.instrumentation_json)
        assert "rejection_reasons" in instrumentation, (
            "instrumentation_json must contain rejection_reasons mapping"
        )
        # rejection_reasons should be a dict with at least format_valid and no_phi keys
        rejection = instrumentation["rejection_reasons"]
        assert isinstance(rejection, dict), "rejection_reasons must be a dict"
