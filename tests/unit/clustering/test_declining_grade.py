"""T103 [US7] — Declining grade tests (FR-023, audit M4).

Tests confirm that the pattern grader transitions patterns through:
  established → declining (last_error_at > 7 days ago)
  declining → dead (last_error_at > 30 days ago, if dead state exists)
  Fresh last_error_at → stays established

Grade transitions use ``MAX(error_records.timestamp) WHERE pattern_id=?``
NOT the current insert time.

These tests are EXPECTED RED until T104 (Wave 11) fixes the grader impl.

Run to confirm RED:
    uv run pytest tests/unit/clustering/test_declining_grade.py -v
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Test DB fixture
# ---------------------------------------------------------------------------

def _utc_days_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return dt.isoformat()


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "sio.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT NOT NULL UNIQUE,
            description TEXT,
            grade TEXT DEFAULT 'new',
            error_count INTEGER DEFAULT 0,
            last_error_at TEXT
        );
        CREATE TABLE IF NOT EXISTS error_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            pattern_id TEXT,
            error_text TEXT,
            timestamp TEXT
        );
    """)
    conn.commit()
    return conn


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = _open_db(tmp_path)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# T103-1: established pattern with last_error 14 days ago → declining
# ---------------------------------------------------------------------------


def test_established_transitions_to_declining_after_7_days(db):
    """Pattern last active 14 days ago must transition from established → declining."""
    from sio.clustering.grader import compute_pattern_grade  # noqa: PLC0415

    db.execute(
        "INSERT INTO patterns (pattern_id, description, grade, error_count, last_error_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("tool_fail_abc1234567", "tool_failure: Bash error", "established", 10,
         _utc_days_ago(14)),
    )
    db.execute(
        "INSERT INTO error_records (session_id, pattern_id, error_text, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("sess_001", "tool_fail_abc1234567", "tool_failure: Bash error",
         _utc_days_ago(14)),
    )
    db.commit()

    grade = compute_pattern_grade(db, "tool_fail_abc1234567")

    assert grade == "declining", (
        f"Pattern with last_error 14 days ago should be 'declining', got {grade!r}"
    )


# ---------------------------------------------------------------------------
# T103-2: pattern with fresh last_error_at (today) stays established
# ---------------------------------------------------------------------------


def test_fresh_pattern_stays_established(db):
    """Pattern with recent activity must stay 'established'."""
    from sio.clustering.grader import compute_pattern_grade  # noqa: PLC0415

    db.execute(
        "INSERT INTO patterns (pattern_id, description, grade, error_count, last_error_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("fresh_pattern_bb1234567a", "parse_error: recent", "established", 5,
         _utc_days_ago(0)),
    )
    db.execute(
        "INSERT INTO error_records (session_id, pattern_id, error_text, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("sess_002", "fresh_pattern_bb1234567a", "parse_error: recent",
         _utc_days_ago(0)),
    )
    db.commit()

    grade = compute_pattern_grade(db, "fresh_pattern_bb1234567a")

    assert grade == "established", (
        f"Fresh pattern should stay 'established', got {grade!r}"
    )


# ---------------------------------------------------------------------------
# T103-3: pattern with no errors in 30+ days → dead (or declining if no dead state)
# ---------------------------------------------------------------------------


def test_very_stale_pattern_transitions_to_dead(db):
    """Pattern with no errors for 30+ days must be 'dead' (or at least 'declining')."""
    from sio.clustering.grader import compute_pattern_grade  # noqa: PLC0415

    db.execute(
        "INSERT INTO patterns (pattern_id, description, grade, error_count, last_error_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("dead_pattern_cc1234567b", "timeout: old error", "declining", 3,
         _utc_days_ago(35)),
    )
    db.execute(
        "INSERT INTO error_records (session_id, pattern_id, error_text, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("sess_003", "dead_pattern_cc1234567b", "timeout: old error",
         _utc_days_ago(35)),
    )
    db.commit()

    grade = compute_pattern_grade(db, "dead_pattern_cc1234567b")

    # Must be either dead or at minimum declining (implementation may not have dead state yet)
    assert grade in ("dead", "declining"), (
        f"Pattern 35 days stale should be 'dead' or 'declining', got {grade!r}"
    )


# ---------------------------------------------------------------------------
# T103-4: grade uses MAX(error_records.timestamp), not patterns.last_error_at
# ---------------------------------------------------------------------------


def test_grade_uses_max_error_records_timestamp(db):
    """Grader must compute grade from MAX(error_records.timestamp), ignoring patterns.last_error_at."""
    from sio.clustering.grader import compute_pattern_grade  # noqa: PLC0415

    # patterns.last_error_at says 14 days ago (would trigger declining)
    # but error_records has a fresh timestamp (today) → should stay established
    db.execute(
        "INSERT INTO patterns (pattern_id, description, grade, error_count, last_error_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("stale_meta_dd1234567c", "auth_error: stale meta", "established", 8,
         _utc_days_ago(14)),
    )
    # Insert fresh error record — MAX will be today
    db.execute(
        "INSERT INTO error_records (session_id, pattern_id, error_text, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("sess_004", "stale_meta_dd1234567c", "auth_error: stale meta",
         _utc_days_ago(14)),
    )
    db.execute(
        "INSERT INTO error_records (session_id, pattern_id, error_text, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("sess_005", "stale_meta_dd1234567c", "auth_error: stale meta",
         _utc_days_ago(0)),  # fresh
    )
    db.commit()

    grade = compute_pattern_grade(db, "stale_meta_dd1234567c")

    assert grade == "established", (
        f"Grader must use MAX(error_records.timestamp). Fresh record exists, "
        f"expected 'established', got {grade!r}"
    )


# ---------------------------------------------------------------------------
# T103-5: pattern with no error_records rows uses last_error_at fallback
# ---------------------------------------------------------------------------


def test_grade_falls_back_to_last_error_at_when_no_records(db):
    """When no error_records exist for pattern, fallback to patterns.last_error_at."""
    from sio.clustering.grader import compute_pattern_grade  # noqa: PLC0415

    db.execute(
        "INSERT INTO patterns (pattern_id, description, grade, error_count, last_error_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("orphan_ee1234567d", "io_error: orphan pattern", "established", 2,
         _utc_days_ago(10)),
    )
    # No error_records inserted for this pattern
    db.commit()

    grade = compute_pattern_grade(db, "orphan_ee1234567d")

    assert grade == "declining", (
        f"Pattern with last_error_at 10 days ago and no error_records "
        f"should be 'declining', got {grade!r}"
    )
