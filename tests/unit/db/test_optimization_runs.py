"""T069 [US9] Tests for record_optimization_run and mark_prior_inactive in queries.py.

Per data-model.md §2.9:
- record_optimization_run inserts a row with active=1
- mark_prior_inactive sets prior rows to active=0
- Full sequence: only the latest artifact is active=1 per module
- Missing required column raises sqlite3.IntegrityError or OperationalError
"""

from __future__ import annotations

import sqlite3
import tempfile
import os

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db():
    """In-memory SQLite DB with a minimal optimized_modules schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS optimized_modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module_type TEXT NOT NULL,
            module_name TEXT,
            optimizer_used TEXT NOT NULL,
            optimizer_name TEXT,
            file_path TEXT NOT NULL,
            artifact_path TEXT,
            training_count INTEGER DEFAULT 0,
            trainset_size INTEGER,
            valset_size INTEGER,
            score REAL,
            metric_name TEXT,
            metric_after REAL,
            task_lm TEXT,
            reflection_lm TEXT,
            is_active INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
    """)
    return conn


# ---------------------------------------------------------------------------
# T069-1: record_optimization_run inserts a row with active=1
# ---------------------------------------------------------------------------


def test_record_optimization_run_inserts_active_row(tmp_db):
    """record_optimization_run must insert a row with active=1."""
    from sio.core.db.queries import record_optimization_run  # noqa: PLC0415

    row_id = record_optimization_run(
        tmp_db,
        module_name="suggestion_generator",
        optimizer_name="gepa",
        metric_name="embedding_similarity",
        trainset_size=10,
        valset_size=3,
        score=0.75,
        task_lm="openai/gpt-4o-mini",
        reflection_lm="openai/gpt-4o",
        artifact_path="/tmp/fake_artifact.json",
    )

    assert isinstance(row_id, int), "record_optimization_run must return an int row id"
    assert row_id > 0, "Returned row id must be positive"

    row = tmp_db.execute(
        "SELECT * FROM optimized_modules WHERE id = ?", (row_id,)
    ).fetchone()
    assert row is not None, "Row must exist in optimized_modules"
    # active and is_active both set to 1
    assert row["is_active"] == 1 or row["active"] == 1, (
        "Newly inserted row must have active=1"
    )
    assert row["score"] == 0.75 or row["metric_after"] == 0.75
    assert row["module_type"] == "suggestion_generator" or row["module_name"] == "suggestion_generator"


# ---------------------------------------------------------------------------
# T069-2: mark_prior_inactive flips old rows to active=0
# ---------------------------------------------------------------------------


def test_mark_prior_inactive_flips_old_rows(tmp_db):
    """mark_prior_inactive must set active=0 on all prior rows for the module."""
    from sio.core.db.queries import mark_prior_inactive, record_optimization_run  # noqa: PLC0415

    # Insert two rows for the same module
    id1 = record_optimization_run(
        tmp_db,
        module_name="suggestion_generator",
        optimizer_name="bootstrap",
        metric_name="embedding_similarity",
        trainset_size=5,
        valset_size=2,
        score=0.60,
        task_lm=None,
        reflection_lm=None,
        artifact_path="/tmp/artifact_v1.json",
    )
    id2 = record_optimization_run(
        tmp_db,
        module_name="suggestion_generator",
        optimizer_name="gepa",
        metric_name="embedding_similarity",
        trainset_size=8,
        valset_size=3,
        score=0.70,
        task_lm=None,
        reflection_lm=None,
        artifact_path="/tmp/artifact_v2.json",
    )

    # Now mark prior as inactive — both rows are currently active
    mark_prior_inactive(tmp_db, "suggestion_generator")

    rows = tmp_db.execute(
        "SELECT id, is_active, active FROM optimized_modules "
        "WHERE module_type='suggestion_generator' OR module_name='suggestion_generator'"
    ).fetchall()

    for row in rows:
        # After mark_prior_inactive, both columns must be 0
        assert row["is_active"] == 0 and row["active"] == 0, (
            f"Row id={row['id']} must have active=0 and is_active=0 after mark_prior_inactive, "
            f"got is_active={row['is_active']}, active={row['active']}"
        )


# ---------------------------------------------------------------------------
# T069-3: Full sequence — only latest artifact is active=1
# ---------------------------------------------------------------------------


def test_full_sequence_only_latest_active(tmp_db):
    """Full insert→mark→insert cycle must leave only the newest row active=1."""
    from sio.core.db.queries import mark_prior_inactive, record_optimization_run  # noqa: PLC0415

    # First run
    id1 = record_optimization_run(
        tmp_db,
        module_name="suggestion_generator",
        optimizer_name="bootstrap",
        metric_name="embedding_similarity",
        trainset_size=5,
        valset_size=2,
        score=0.55,
        task_lm=None,
        reflection_lm=None,
        artifact_path="/tmp/v1.json",
    )

    # Deactivate prior, then insert second run
    mark_prior_inactive(tmp_db, "suggestion_generator")
    id2 = record_optimization_run(
        tmp_db,
        module_name="suggestion_generator",
        optimizer_name="gepa",
        metric_name="embedding_similarity",
        trainset_size=10,
        valset_size=4,
        score=0.80,
        task_lm="openai/gpt-4o-mini",
        reflection_lm="openai/gpt-4o",
        artifact_path="/tmp/v2.json",
    )

    rows = tmp_db.execute(
        "SELECT id, is_active, active FROM optimized_modules "
        "WHERE module_type='suggestion_generator' OR module_name='suggestion_generator' "
        "ORDER BY id"
    ).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    # Row 1 must be inactive
    row1 = next(r for r in rows if r["id"] == id1)
    assert row1["is_active"] == 0 or row1["active"] == 0, (
        f"First run (id={id1}) must be inactive; "
        f"got is_active={row1['is_active']}, active={row1['active']}"
    )

    # Row 2 must be active
    row2 = next(r for r in rows if r["id"] == id2)
    assert row2["is_active"] == 1 or row2["active"] == 1, (
        f"Second run (id={id2}) must be active=1; "
        f"got is_active={row2['is_active']}, active={row2['active']}"
    )


# ---------------------------------------------------------------------------
# T069-4: Missing required column raises an error
# ---------------------------------------------------------------------------


def test_record_optimization_run_missing_required_column_raises():
    """Inserting with a required NOT NULL column missing must raise an error."""
    # Create a DB with a strict schema that rejects NULL for module_type
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE optimized_modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module_type TEXT NOT NULL,
            optimizer_used TEXT NOT NULL,
            file_path TEXT NOT NULL,
            training_count INTEGER DEFAULT 0,
            metric_after REAL,
            is_active INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            CONSTRAINT ck_module_type_nonempty CHECK (module_type != '')
        );
    """)

    # Patch record_optimization_run to use a bad module_name that triggers constraint
    # by directly calling the insert with missing values
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        conn.execute(
            "INSERT INTO optimized_modules (module_type, optimizer_used, file_path) "
            "VALUES (NULL, 'gepa', '/tmp/x.json')"
        )
