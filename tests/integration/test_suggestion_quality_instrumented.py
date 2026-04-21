"""Integration tests for T107/T108: SuggestionGenerator instrumentation (FR-029).

Tests that SuggestionGenerator produces per-run counters in
suggestions.instrumentation_json and captures per-stage rejection reasons.

Uses the ``dspy_stub_lm`` fixture so ``forward()`` runs with a controlled LM
and the real validation loop executes (not a rubber-stamp mock). The stub
drives deterministic LM outputs; the instrumentation code under test is real.
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

    def test_forward_produces_instrumentation_json(self, db_with_patterns, dspy_stub_lm):
        """Happy path: valid first-attempt prediction → instrumentation attached.

        Exercises the real SuggestionGenerator.forward() loop with a
        deterministic LM stub that returns a format-valid, PHI-free prediction
        on the first attempt. Asserts:
          - forward_count == 1 (one attempt)
          - backtrack_count == 0 (no retries)
          - rejection_reasons has both keys (format_valid, no_phi), both None
        """
        dspy_stub_lm(
            answers=[
                {
                    "reasoning": "Bash commands above system arg limit fail.",
                    "rule_title": "Split long Bash commands",
                    "rule_body": "Bash argv has a length limit. Split oversize commands using files or xargs.",
                    "rule_rationale": "Prevents arg-list-too-long failures.",
                }
            ]
        )

        gen = SuggestionGenerator()
        pred = gen.forward(
            pattern_description="Tool call fails on large input",
            example_errors=["Bash: command too long", "Bash: arg list too long"],
            project_context="SIO pipeline integrity",
        )

        assert hasattr(pred, "instrumentation_json"), (
            "Prediction must expose instrumentation_json (T108 deliverable)"
        )
        instrumentation = json.loads(pred.instrumentation_json)
        assert instrumentation["forward_count"] == 1
        assert instrumentation["backtrack_count"] == 0
        assert instrumentation["rejection_reasons"] == {"format_valid": None, "no_phi": None}

    def test_forward_captures_rejection_reasons(self, db_with_patterns, dspy_stub_lm):
        """PHI on attempt 1 → correction hint → clean on attempt 2.

        Exercises the real retry loop: attempt 1's prediction contains 'SSN'
        which `validate_no_phi` rejects; `forward()` appends a PHI-removal
        hint to project_context and retries; attempt 2 returns a PHI-free
        prediction. Asserts:
          - forward_count == 1 (forward was called once by the user)
          - backtrack_count == 1 (one PHI-triggered retry)
          - rejection_reasons['no_phi'] is a non-empty string (the failure message)
          - rejection_reasons['format_valid'] is None (format was fine both attempts)
        """
        dspy_stub_lm(
            answers=[
                # Attempt 1: contains a PHI token → validate_no_phi rejects
                {
                    "reasoning": "First attempt describes the leak concretely.",
                    "rule_title": "Avoid logging SSN",
                    "rule_body": "Never log SSN 123-45-6789 in any output.",
                    "rule_rationale": "SSN exposure.",
                },
                # Attempt 2: PHI removed after correction hint → passes
                {
                    "reasoning": "Corrected per PHI REMOVAL NEEDED hint.",
                    "rule_title": "Redact PHI in error logs",
                    "rule_body": "Redact sensitive tokens before logging. Use a scrubber.",
                    "rule_rationale": "Prevents accidental PHI exposure.",
                },
            ]
        )

        gen = SuggestionGenerator()
        pred = gen.forward(
            pattern_description="PHI leak in error logs",
            example_errors=["Error: SSN 123-45-6789 found in output"],
            project_context="SIO pipeline integrity",
        )

        assert hasattr(pred, "instrumentation_json")
        instrumentation = json.loads(pred.instrumentation_json)
        assert instrumentation["forward_count"] == 1
        assert instrumentation["backtrack_count"] == 1, (
            "Exactly one backtrack expected (PHI retry)"
        )
        rejection = instrumentation["rejection_reasons"]
        assert isinstance(rejection, dict)
        assert rejection["format_valid"] is None, (
            "format was valid on both attempts; format_valid should be None"
        )
        assert rejection["no_phi"], (
            "no_phi must hold the failure message from attempt 1 (not None / not empty)"
        )
        assert "SSN" in rejection["no_phi"] or "PHI" in rejection["no_phi"].upper()
