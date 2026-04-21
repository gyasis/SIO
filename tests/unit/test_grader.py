"""T052 [US6] Unit tests for pattern lifecycle grading and auto-suggestion generation.

Tests cover:
- ``grade_pattern(pattern_row)`` returning correct grades across the lifecycle.
- ``run_grading(db)`` updating the patterns table grade column in-place.
- ``auto_generate_suggestions(db, strong_patterns)`` creating suggestion records
  for newly-promoted 'strong' patterns without duplicates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sio.clustering.grader import (
    auto_generate_suggestions,
    grade_pattern,
    run_grading,
)
from sio.core.config import SIOConfig
from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Config with generous fresh window so patterns don't accidentally
# decline in these unit tests (decay_fresh_days=90 to keep things fresh).
_FRESH_CFG = SIOConfig(decay_fresh_days=90, decay_stale_days=120, decay_floor=0.3)

# Config where everything decays fast — for decline testing.
_DECAY_CFG = SIOConfig(decay_fresh_days=1, decay_stale_days=2, decay_floor=0.1)


def _ts(days_ago: float = 0) -> str:
    """Return an ISO-8601 datetime string *days_ago* days before now (UTC)."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.isoformat()


def _make_pattern_row(
    *,
    error_count: int = 1,
    session_count: int = 1,
    first_seen_days_ago: float = 0,
    last_seen_days_ago: float = 0,
    rank_score: float = 0.8,
) -> dict:
    """Build a pattern dict suitable for ``grade_pattern``."""
    return {
        "error_count": error_count,
        "session_count": session_count,
        "first_seen": _ts(first_seen_days_ago),
        "last_seen": _ts(last_seen_days_ago),
        "rank_score": rank_score,
    }


@pytest.fixture()
def db():
    """In-memory SIO database with schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Tests: grade_pattern
# ---------------------------------------------------------------------------


class TestGradePatternThresholds:
    """grade_pattern returns correct grades at threshold boundaries."""

    def test_below_emerging_returns_none(self) -> None:
        """1 occurrence, 1 session -> None (below emerging threshold)."""
        row = _make_pattern_row(error_count=1, session_count=1)
        assert grade_pattern(row, config=_FRESH_CFG) is None

    def test_emerging(self) -> None:
        """2 occurrences, 2 sessions -> 'emerging'."""
        row = _make_pattern_row(error_count=2, session_count=2)
        assert grade_pattern(row, config=_FRESH_CFG) == "emerging"

    def test_strong(self) -> None:
        """3 occurrences, 3 sessions -> 'strong'."""
        row = _make_pattern_row(error_count=3, session_count=3)
        assert grade_pattern(row, config=_FRESH_CFG) == "strong"

    def test_established(self) -> None:
        """5 occurrences, first_seen 10 days ago -> 'established'."""
        row = _make_pattern_row(
            error_count=5,
            session_count=5,
            first_seen_days_ago=10,
        )
        assert grade_pattern(row, config=_FRESH_CFG) == "established"

    def test_declining_low_confidence(self) -> None:
        """Pattern with very old last_seen -> 'declining'.

        With decay_fresh_days=1, decay_stale_days=2, floor=0.1:
        A pattern last seen 100 days ago will have decay_multiplier = 0.1.
        Since 0.1 < 0.5, the pattern is graded as 'declining'.
        """
        row = _make_pattern_row(
            error_count=5,
            session_count=5,
            last_seen_days_ago=100,
            first_seen_days_ago=110,
            rank_score=0.9,
        )
        result = grade_pattern(row, config=_DECAY_CFG)
        assert result == "declining"


class TestGradePatternPrecedence:
    """Grade precedence: declining > established > strong > emerging."""

    def test_established_beats_strong(self) -> None:
        """Pattern with 5 errors, 5 sessions, 10-day span should be established, not strong."""
        row = _make_pattern_row(
            error_count=5,
            session_count=5,
            first_seen_days_ago=10,
        )
        assert grade_pattern(row, config=_FRESH_CFG) == "established"

    def test_strong_when_not_enough_span(self) -> None:
        """5 errors, 5 sessions, but first_seen only 3 days ago -> strong (not established)."""
        row = _make_pattern_row(
            error_count=5,
            session_count=5,
            first_seen_days_ago=3,
        )
        assert grade_pattern(row, config=_FRESH_CFG) == "strong"

    def test_emerging_not_strong(self) -> None:
        """2 errors, 2 sessions -> emerging, not strong."""
        row = _make_pattern_row(error_count=2, session_count=2)
        assert grade_pattern(row, config=_FRESH_CFG) == "emerging"

    def test_three_errors_two_sessions_is_emerging(self) -> None:
        """3 errors but only 2 sessions -> emerging (strong requires 3 sessions)."""
        row = _make_pattern_row(error_count=3, session_count=2)
        assert grade_pattern(row, config=_FRESH_CFG) == "emerging"


# ---------------------------------------------------------------------------
# Tests: run_grading
# ---------------------------------------------------------------------------


def _insert_pattern(
    db,
    *,
    pattern_id: str,
    error_count: int,
    session_count: int,
    first_seen_days_ago: float = 0,
    last_seen_days_ago: float = 0,
    rank_score: float = 0.8,
    grade: str | None = None,
) -> int:
    """Insert a pattern row and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    first_seen = _ts(first_seen_days_ago)
    last_seen = _ts(last_seen_days_ago)
    cur = db.execute(
        "INSERT INTO patterns "
        "(pattern_id, description, tool_name, error_count, session_count, "
        "first_seen, last_seen, rank_score, grade, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pattern_id,
            f"Test pattern {pattern_id}",
            "Bash",
            error_count,
            session_count,
            first_seen,
            last_seen,
            rank_score,
            grade,
            now,
            now,
        ),
    )
    db.commit()
    return cur.lastrowid


class TestRunGrading:
    """run_grading updates patterns table and returns change list."""

    def test_grades_updated_in_db(self, db) -> None:
        """run_grading should write new grade values to the patterns table."""
        pid = _insert_pattern(
            db,
            pattern_id="p1",
            error_count=3,
            session_count=3,
            grade=None,
        )
        changes = run_grading(db, config=_FRESH_CFG)
        assert len(changes) >= 1

        row = db.execute("SELECT grade FROM patterns WHERE id = ?", (pid,)).fetchone()
        assert row["grade"] == "strong"

    def test_no_change_when_grade_matches(self, db) -> None:
        """If the existing grade matches the computed grade, no change is recorded."""
        _insert_pattern(
            db,
            pattern_id="p2",
            error_count=3,
            session_count=3,
            grade="strong",
        )
        changes = run_grading(db, config=_FRESH_CFG)
        # Should have no changes since it was already 'strong'
        assert all(c["new_grade"] != "strong" or c["old_grade"] != "strong" for c in changes)

    def test_returns_old_and_new_grade(self, db) -> None:
        """Each change dict should have pattern_id, old_grade, new_grade."""
        _insert_pattern(
            db,
            pattern_id="p3",
            error_count=2,
            session_count=2,
            grade=None,
        )
        changes = run_grading(db, config=_FRESH_CFG)
        change = next(c for c in changes if c["new_grade"] == "emerging")
        assert "old_grade" in change
        assert "pattern_id" in change
        assert change["old_grade"] is None
        assert change["new_grade"] == "emerging"

    def test_multiple_patterns_graded(self, db) -> None:
        """Multiple patterns should all be graded in a single run."""
        _insert_pattern(
            db,
            pattern_id="pa",
            error_count=2,
            session_count=2,
            grade=None,
        )
        _insert_pattern(
            db,
            pattern_id="pb",
            error_count=5,
            session_count=5,
            first_seen_days_ago=10,
            grade=None,
        )
        changes = run_grading(db, config=_FRESH_CFG)
        assert len(changes) == 2
        grades = {c["new_grade"] for c in changes}
        assert "emerging" in grades
        assert "established" in grades


# ---------------------------------------------------------------------------
# Tests: auto_generate_suggestions
# ---------------------------------------------------------------------------


class TestAutoGenerateSuggestions:
    """auto_generate_suggestions creates suggestion records for strong patterns."""

    def test_creates_suggestion_for_strong_pattern(self, db) -> None:
        pid = _insert_pattern(
            db,
            pattern_id="s1",
            error_count=3,
            session_count=3,
        )
        strong_patterns = [
            {
                "pattern_id": pid,
                "description": "Test pattern s1",
                "error_count": 3,
                "rank_score": 0.8,
                "last_seen": _ts(0),
            },
        ]
        count = auto_generate_suggestions(db, strong_patterns)
        assert count == 1

        # Verify suggestion exists in DB
        rows = db.execute("SELECT * FROM suggestions WHERE pattern_id = ?", (pid,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        assert rows[0]["confidence"] > 0

    def test_no_duplicate_suggestions(self, db) -> None:
        """Calling auto_generate_suggestions twice should not create duplicates."""
        pid = _insert_pattern(
            db,
            pattern_id="s2",
            error_count=3,
            session_count=3,
        )
        strong = [
            {
                "pattern_id": pid,
                "description": "Test pattern s2",
                "error_count": 3,
                "rank_score": 0.8,
                "last_seen": _ts(0),
            },
        ]
        count1 = auto_generate_suggestions(db, strong)
        count2 = auto_generate_suggestions(db, strong)
        assert count1 == 1
        assert count2 == 0

        # Still only 1 suggestion
        rows = db.execute("SELECT * FROM suggestions WHERE pattern_id = ?", (pid,)).fetchall()
        assert len(rows) == 1

    def test_empty_list_returns_zero(self, db) -> None:
        count = auto_generate_suggestions(db, [])
        assert count == 0

    def test_multiple_strong_patterns(self, db) -> None:
        """Multiple strong patterns each get a suggestion."""
        pid1 = _insert_pattern(
            db,
            pattern_id="m1",
            error_count=3,
            session_count=3,
        )
        pid2 = _insert_pattern(
            db,
            pattern_id="m2",
            error_count=4,
            session_count=4,
        )
        strong = [
            {
                "pattern_id": pid1,
                "description": "Pattern m1",
                "error_count": 3,
                "rank_score": 0.7,
                "last_seen": _ts(0),
            },
            {
                "pattern_id": pid2,
                "description": "Pattern m2",
                "error_count": 4,
                "rank_score": 0.9,
                "last_seen": _ts(0),
            },
        ]
        count = auto_generate_suggestions(db, strong)
        assert count == 2

    def test_already_strong_with_existing_suggestion(self, db) -> None:
        """A pattern that was already strong and has a suggestion should not get another."""
        pid = _insert_pattern(
            db,
            pattern_id="ex1",
            error_count=3,
            session_count=3,
            grade="strong",
        )
        # Manually insert a suggestion for this pattern
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO suggestions "
            "(pattern_id, description, confidence, proposed_change, "
            "target_file, change_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid,
                "Existing suggestion",
                0.7,
                "Do something",
                "CLAUDE.md",
                "claude_md_rule",
                "pending",
                now,
            ),
        )
        db.commit()

        strong = [
            {
                "pattern_id": pid,
                "description": "Pattern ex1",
                "error_count": 3,
                "rank_score": 0.8,
                "last_seen": _ts(0),
            },
        ]
        count = auto_generate_suggestions(db, strong)
        assert count == 0

    def test_suggestion_fields_populated(self, db) -> None:
        """Verify all required suggestion fields are populated."""
        pid = _insert_pattern(
            db,
            pattern_id="f1",
            error_count=5,
            session_count=5,
        )
        strong = [
            {
                "pattern_id": pid,
                "description": "Test pattern f1",
                "error_count": 5,
                "rank_score": 0.9,
                "last_seen": _ts(0),
            },
        ]
        auto_generate_suggestions(db, strong)

        row = db.execute("SELECT * FROM suggestions WHERE pattern_id = ?", (pid,)).fetchone()
        assert row is not None
        assert row["change_type"] == "claude_md_rule"
        assert row["target_file"] == "CLAUDE.md"
        assert row["status"] == "pending"
        assert row["created_at"] is not None
        assert row["confidence"] > 0.0
        assert "Test pattern f1" in row["description"]
