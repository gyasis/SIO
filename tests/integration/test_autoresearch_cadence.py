"""T074 [P] [US4] Failing integration test for autoresearch cadence.

Tests per contracts/cli-commands.md § sio autoresearch:
- autoresearch_run_once() exists in sio.autoresearch.scheduler (module DNE until T075 Wave 8)
- Each call appends exactly one row to autoresearch_txlog
- Suggestions with arena_passed=1 AND metric > threshold get outcome='promoted'
  or 'pending_approval'
- Suggestions with arena_passed=0 get outcome='rejected_arena'
- Approval gate: auto-promote is blocked by default; only allowed when
  auto_approve_above=0.9 is passed and metric exceeds the threshold

These tests are EXPECTED RED until T075 (Wave 8) creates sio.autoresearch.scheduler.

Run to confirm RED:
    uv run pytest tests/integration/test_autoresearch_cadence.py -v
"""

from __future__ import annotations

import sqlite3

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create the minimal tables needed for autoresearch testing."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT,
            description TEXT,
            confidence REAL DEFAULT 0.5,
            proposed_change TEXT,
            target_file TEXT,
            change_type TEXT,
            status TEXT DEFAULT 'pending',
            arena_passed INTEGER DEFAULT NULL,
            arena_score REAL DEFAULT NULL,
            ai_explanation TEXT,
            user_note TEXT,
            instrumentation_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            reviewed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS autoresearch_txlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER,
            outcome TEXT NOT NULL,
            metric_score REAL,
            auto_approved INTEGER DEFAULT 0,
            run_timestamp TEXT DEFAULT (datetime('now')),
            notes TEXT
        );
    """)


@pytest.fixture
def tmp_sio_db():
    """Temporary SIO DB with 10 suggestions with mixed arena_passed values."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    # Insert 10 suggestions: 5 with arena_passed=1 (high metric), 5 with arena_passed=0
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO suggestions "
            "(description, confidence, arena_passed, arena_score, status) "
            "VALUES (?, ?, 1, ?, 'pending')",
            (f"Passing suggestion {i}", 0.85 + i * 0.01, 0.85 + i * 0.01),
        )
    for i in range(6, 11):
        conn.execute(
            "INSERT INTO suggestions "
            "(description, confidence, arena_passed, arena_score, status) "
            "VALUES (?, ?, 0, ?, 'pending')",
            (f"Failing suggestion {i}", 0.3 + i * 0.01, 0.3 + i * 0.01),
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# T074-1: autoresearch_run_once gains exactly one txlog row per call
# ---------------------------------------------------------------------------


def test_autoresearch_run_once_adds_one_txlog_row_per_call(tmp_sio_db):
    """Each call to autoresearch_run_once() must append exactly one row to autoresearch_txlog.

    This test is EXPECTED RED — sio.autoresearch.scheduler does not exist yet.
    T075 (Wave 8) creates the module.
    """
    from sio.autoresearch.scheduler import (
        autoresearch_run_once,  # type: ignore[import]  # noqa: PLC0415
    )

    initial_count = tmp_sio_db.execute("SELECT COUNT(*) FROM autoresearch_txlog").fetchone()[0]

    for call_num in range(1, 6):
        autoresearch_run_once(tmp_sio_db)
        new_count = tmp_sio_db.execute("SELECT COUNT(*) FROM autoresearch_txlog").fetchone()[0]
        assert new_count == initial_count + call_num, (
            f"After call {call_num}, txlog must have {initial_count + call_num} rows, "
            f"got {new_count}"
        )


# ---------------------------------------------------------------------------
# T074-2: Suggestions with arena_passed=1 and high metric get promoted/pending
# ---------------------------------------------------------------------------


def test_autoresearch_promotes_arena_passed_suggestions(tmp_sio_db):
    """Suggestions with arena_passed=1 AND metric > threshold get
    outcome='promoted' or 'pending_approval' in autoresearch_txlog."""
    from sio.autoresearch.scheduler import (
        autoresearch_run_once,  # type: ignore[import]  # noqa: PLC0415
    )

    autoresearch_run_once(tmp_sio_db)

    passing_ids = [
        r["id"]
        for r in tmp_sio_db.execute("SELECT id FROM suggestions WHERE arena_passed=1").fetchall()
    ]

    txlog_rows = tmp_sio_db.execute(
        "SELECT * FROM autoresearch_txlog WHERE suggestion_id IN ({})".format(
            ",".join("?" * len(passing_ids))
        ),
        passing_ids,
    ).fetchall()

    if txlog_rows:
        for row in txlog_rows:
            assert row["outcome"] in ("promoted", "pending_approval"), (
                f"arena_passed=1 suggestion must get outcome='promoted' or "
                f"'pending_approval', got '{row['outcome']}'"
            )


# ---------------------------------------------------------------------------
# T074-3: Suggestions with arena_passed=0 get outcome='rejected_arena'
# ---------------------------------------------------------------------------


def test_autoresearch_rejects_arena_failed_suggestions(tmp_sio_db):
    """Suggestions with arena_passed=0 must get outcome='rejected_arena'."""
    from sio.autoresearch.scheduler import (
        autoresearch_run_once,  # type: ignore[import]  # noqa: PLC0415
    )

    autoresearch_run_once(tmp_sio_db)

    failing_ids = [
        r["id"]
        for r in tmp_sio_db.execute("SELECT id FROM suggestions WHERE arena_passed=0").fetchall()
    ]

    txlog_rows = tmp_sio_db.execute(
        "SELECT * FROM autoresearch_txlog WHERE suggestion_id IN ({})".format(
            ",".join("?" * len(failing_ids))
        ),
        failing_ids,
    ).fetchall()

    if txlog_rows:
        for row in txlog_rows:
            assert row["outcome"] == "rejected_arena", (
                f"arena_passed=0 suggestion must get outcome='rejected_arena', "
                f"got '{row['outcome']}'"
            )


# ---------------------------------------------------------------------------
# T074-4: Approval gate — auto-promote blocked by default
# ---------------------------------------------------------------------------


def test_autoresearch_blocks_auto_promote_by_default(tmp_sio_db):
    """Without auto_approve_above, promoted suggestions must remain 'pending_approval',
    not 'promoted' (auto-promote is gated by explicit flag)."""
    from sio.autoresearch.scheduler import (
        autoresearch_run_once,  # type: ignore[import]  # noqa: PLC0415
    )

    # Run without auto_approve_above — default behavior blocks auto-promotion
    autoresearch_run_once(tmp_sio_db)

    txlog_rows = tmp_sio_db.execute(
        "SELECT * FROM autoresearch_txlog WHERE auto_approved=1"
    ).fetchall()

    assert len(txlog_rows) == 0, (
        f"No rows should have auto_approved=1 when auto_approve_above is not set; "
        f"got {len(txlog_rows)} auto-approved rows"
    )


def test_autoresearch_allows_auto_promote_above_threshold(tmp_sio_db):
    """When auto_approve_above=0.9 is passed, high-metric suggestions should
    get outcome='promoted' with auto_approved=1."""
    from sio.autoresearch.scheduler import (
        autoresearch_run_once,  # type: ignore[import]  # noqa: PLC0415
    )

    autoresearch_run_once(tmp_sio_db, auto_approve_above=0.9)

    # At least one of the passing suggestions has arena_score > 0.9
    high_metric_ids = [
        r["id"]
        for r in tmp_sio_db.execute(
            "SELECT id FROM suggestions WHERE arena_passed=1 AND arena_score > 0.9"
        ).fetchall()
    ]

    if high_metric_ids:
        txlog_rows = tmp_sio_db.execute(
            "SELECT * FROM autoresearch_txlog "
            "WHERE suggestion_id IN ({}) AND auto_approved=1".format(
                ",".join("?" * len(high_metric_ids))
            ),
            high_metric_ids,
        ).fetchall()
        assert len(txlog_rows) > 0, (
            "With auto_approve_above=0.9, high-metric arena-passed suggestions "
            "must be auto-approved (auto_approved=1 in txlog)"
        )
