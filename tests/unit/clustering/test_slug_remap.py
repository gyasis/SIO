"""T099 [US7] — Ground-truth slug remap tests (R-5, Jaccard overlap).

Tests confirm that remap_ground_truth_slugs.py correctly remaps FK references
in ground_truth when old→new pattern_id pairs have Jaccard overlap >= 0.5.

Scenarios:
- Old→new with overlap >= 0.5 → remap FK, set remapped_from_pattern_id
- Old→new with overlap < 0.5 → skip (leave orphaned)
- Running twice produces no additional changes (idempotent)

These tests are EXPECTED RED until T100 (Wave 10) implements the remap script.

Run to confirm RED:
    uv run pytest tests/unit/clustering/test_slug_remap.py -v
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "sio.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT NOT NULL UNIQUE,
            description TEXT,
            error_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pattern_members (
            pattern_id TEXT NOT NULL,
            error_id INTEGER NOT NULL,
            PRIMARY KEY (pattern_id, error_id)
        );
        CREATE TABLE IF NOT EXISTS error_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            error_text TEXT,
            timestamp TEXT
        );
        CREATE TABLE IF NOT EXISTS ground_truth (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT NOT NULL,
            remapped_from_pattern_id TEXT,
            proposed_change TEXT,
            target_surface TEXT,
            confidence REAL DEFAULT 0.8,
            status TEXT DEFAULT 'pending'
        );
    """)
    conn.commit()
    return conn


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sio.db"


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = _open_db(tmp_path)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_errors(conn: sqlite3.Connection, n: int, base_id: int = 0) -> list[int]:
    """Insert n error records and return their IDs."""
    ids = []
    for i in range(n):
        cur = conn.execute(
            "INSERT INTO error_records (session_id, error_text, timestamp) VALUES (?, ?, ?)",
            (f"sess_{base_id + i:03d}", f"error text {base_id + i}", "2026-04-20T10:00:00Z"),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _seed_pattern(
    conn: sqlite3.Connection,
    pattern_id: str,
    error_ids: list[int],
) -> None:
    conn.execute(
        "INSERT INTO patterns (pattern_id, description, error_count) VALUES (?, ?, ?)",
        (pattern_id, f"pattern {pattern_id}", len(error_ids)),
    )
    for eid in error_ids:
        conn.execute(
            "INSERT OR IGNORE INTO pattern_members (pattern_id, error_id) VALUES (?, ?)",
            (pattern_id, eid),
        )
    conn.commit()


def _seed_ground_truth(
    conn: sqlite3.Connection,
    pattern_id: str,
    n: int = 2,
) -> list[int]:
    ids = []
    for i in range(n):
        cur = conn.execute(
            "INSERT INTO ground_truth (pattern_id, proposed_change, target_surface, confidence) "
            "VALUES (?, ?, ?, ?)",
            (pattern_id, f"change {i}", "claude_md_rule", 0.9),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


# ---------------------------------------------------------------------------
# T099-1: High overlap (>= 0.5) → FK remapped + remapped_from_pattern_id set
# ---------------------------------------------------------------------------


def test_high_overlap_remaps_ground_truth(db, db_path):
    """Old pattern with >= 0.5 Jaccard overlap to new pattern must be remapped."""
    from scripts.remap_ground_truth_slugs import remap_slugs  # noqa: PLC0415

    # 10 shared errors, 0 exclusive (Jaccard = 1.0)
    error_ids = _seed_errors(db, 10, base_id=0)

    old_slug = "old_pattern_aaa0000001"
    new_slug = "new_pattern_bbb0000002"

    _seed_pattern(db, old_slug, error_ids)
    _seed_pattern(db, new_slug, error_ids)  # same members → overlap = 1.0
    gt_ids = _seed_ground_truth(db, old_slug, n=3)

    summary = remap_slugs(db, dry_run=False)

    # All ground_truth rows pointing at old_slug should now point at new_slug
    rows = db.execute(
        "SELECT pattern_id, remapped_from_pattern_id FROM ground_truth WHERE id IN (?, ?, ?)",
        tuple(gt_ids),
    ).fetchall()

    for row in rows:
        assert row["pattern_id"] == new_slug, (
            f"Expected pattern_id={new_slug!r}, got {row['pattern_id']!r}"
        )
        assert row["remapped_from_pattern_id"] == old_slug, (
            f"Expected remapped_from_pattern_id={old_slug!r}, got {row['remapped_from_pattern_id']!r}"
        )

    assert summary["remapped"] >= 3, (
        f"Expected at least 3 remapped rows, got {summary['remapped']}"
    )


# ---------------------------------------------------------------------------
# T099-2: Low overlap (< 0.5) → NOT remapped, orphaned
# ---------------------------------------------------------------------------


def test_low_overlap_leaves_ground_truth_orphaned(db, db_path):
    """Old pattern with < 0.5 Jaccard overlap must NOT be remapped (stays orphaned)."""
    from scripts.remap_ground_truth_slugs import remap_slugs  # noqa: PLC0415

    # Old: errors 0-9 (10 errors)
    # New: errors 5-14 (10 errors), overlap = 5/15 ≈ 0.33 < 0.5
    old_errors = _seed_errors(db, 10, base_id=100)
    new_errors = _seed_errors(db, 5, base_id=105) + _seed_errors(db, 5, base_id=200)

    old_slug = "old_pattern_ccc0000003"
    new_slug = "new_pattern_ddd0000004"

    _seed_pattern(db, old_slug, old_errors)
    # new pattern shares only 5 out of 15 total = 0.33 Jaccard
    _seed_pattern(db, new_slug, new_errors)
    gt_ids = _seed_ground_truth(db, old_slug, n=2)

    summary = remap_slugs(db, dry_run=False)

    rows = db.execute(
        "SELECT pattern_id, remapped_from_pattern_id FROM ground_truth WHERE id IN (?, ?)",
        tuple(gt_ids),
    ).fetchall()

    for row in rows:
        assert row["pattern_id"] == old_slug, (
            f"Low overlap: pattern_id should remain {old_slug!r}, got {row['pattern_id']!r}"
        )
        assert row["remapped_from_pattern_id"] is None, (
            f"Low overlap: remapped_from_pattern_id should be NULL, got {row['remapped_from_pattern_id']!r}"
        )

    assert summary["skipped"] >= 2, f"Expected at least 2 skipped rows, got {summary['skipped']}"


# ---------------------------------------------------------------------------
# T099-3: Running remap twice is idempotent
# ---------------------------------------------------------------------------


def test_remap_is_idempotent(db, db_path):
    """Running remap_slugs twice must produce no additional changes on second run."""
    from scripts.remap_ground_truth_slugs import remap_slugs  # noqa: PLC0415

    error_ids = _seed_errors(db, 8, base_id=300)
    old_slug = "old_pattern_eee0000005"
    new_slug = "new_pattern_fff0000006"

    _seed_pattern(db, old_slug, error_ids)
    _seed_pattern(db, new_slug, error_ids)  # 100% overlap
    gt_ids = _seed_ground_truth(db, old_slug, n=2)

    summary1 = remap_slugs(db, dry_run=False)
    summary2 = remap_slugs(db, dry_run=False)

    assert summary1["remapped"] >= 2, f"First run should remap >=2 rows, got {summary1}"
    assert summary2["remapped"] == 0, (
        f"Second run should remap 0 rows (idempotent), got {summary2['remapped']}"
    )
