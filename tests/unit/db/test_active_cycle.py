"""T046 [US2] — Failing tests for active-flag cycle transitions (FR-003).

Tests for ``mark_stale_for_new_cycle(conn, new_cycle_id)`` which will be
implemented in T047 inside ``src/sio/core/db/queries.py``.

Invariants per FR-003:
- Prior rows in patterns/datasets/pattern_errors/suggestions flip to active=0
  when a new cycle_id arrives.
- New rows with the incoming cycle_id can be inserted with active=1.
- ``applied_changes`` is NEVER touched by this helper.
- The helper is idempotent: calling it twice with the same cycle_id has no
  additional effect.

Run to confirm RED before T047:
    uv run pytest tests/unit/db/test_active_cycle.py -v
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_db(tmp_path):
    """Create a minimal SIO DB with the required columns for these tests."""
    db_path = tmp_path / "sio.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Minimal schema — only tables/columns relevant to active-cycle helper
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS patterns (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT UNIQUE NOT NULL,
            description TEXT,
            error_count INTEGER DEFAULT 0,
            session_count INTEGER DEFAULT 0,
            first_seen TEXT,
            last_seen TEXT,
            rank_score REAL DEFAULT 0.0,
            created_at TEXT,
            updated_at TEXT,
            active     INTEGER NOT NULL DEFAULT 1,
            cycle_id   TEXT
        );

        CREATE TABLE IF NOT EXISTS datasets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER,
            file_path  TEXT,
            positive_count INTEGER DEFAULT 0,
            negative_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            active     INTEGER NOT NULL DEFAULT 1,
            cycle_id   TEXT
        );

        CREATE TABLE IF NOT EXISTS pattern_errors (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER,
            error_id   INTEGER,
            active     INTEGER NOT NULL DEFAULT 1,
            cycle_id   TEXT
        );

        CREATE TABLE IF NOT EXISTS suggestions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT,
            confidence  REAL DEFAULT 0.5,
            proposed_change TEXT,
            target_file TEXT,
            change_type TEXT DEFAULT 'append',
            status      TEXT DEFAULT 'pending',
            created_at  TEXT,
            active      INTEGER NOT NULL DEFAULT 1,
            cycle_id    TEXT
        );

        CREATE TABLE IF NOT EXISTS applied_changes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER,
            target_file   TEXT,
            diff_before   TEXT,
            diff_after    TEXT,
            applied_at    TEXT,
            superseded_at TEXT
        );
    """)
    conn.commit()
    return conn, str(db_path)


def _seed_old_rows(conn, cycle_id: str = "old"):
    """Insert 2 rows into each active table with cycle_id='old', active=1."""
    now = _now()
    for i in range(2):
        conn.execute(
            "INSERT INTO patterns (pattern_id, description, error_count, "
            "session_count, first_seen, last_seen, rank_score, created_at, "
            "updated_at, active, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"pat-{cycle_id}-{i}", f"Pattern {i}", 3, 2, now, now, 0.8, now, now, 1, cycle_id),
        )
        conn.execute(
            "INSERT INTO suggestions (description, confidence, proposed_change, "
            "target_file, change_type, status, created_at, active, cycle_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"Suggestion {i}",
                0.7,
                f"Add rule {i}",
                "~/.claude/CLAUDE.md",
                "append",
                "pending",
                now,
                1,
                cycle_id,
            ),
        )
        conn.execute(
            "INSERT INTO datasets (file_path, positive_count, negative_count, "
            "created_at, updated_at, active, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"/tmp/ds-{i}.json", 5, 2, now, now, 1, cycle_id),
        )
        conn.execute(
            "INSERT INTO pattern_errors (pattern_id, error_id, active, cycle_id) "
            "VALUES (?, ?, ?, ?)",
            (i + 1, i + 1, 1, cycle_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_helper():
    """Import mark_stale_for_new_cycle from queries."""
    from sio.core.db.queries import mark_stale_for_new_cycle  # noqa: PLC0415

    return mark_stale_for_new_cycle


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMarkStaleForNewCycle:
    """mark_stale_for_new_cycle(conn, new_cycle_id) must flip prior active rows."""

    def test_function_exists_in_queries(self):
        """mark_stale_for_new_cycle must be importable from sio.core.db.queries."""
        fn = _import_helper()
        assert callable(fn), "mark_stale_for_new_cycle must be callable"

    def test_prior_patterns_flipped_to_inactive(self, tmp_path):
        """Patterns with cycle_id != new_cycle_id must have active=0 after call."""
        conn, db_path = _fresh_db(tmp_path)
        _seed_old_rows(conn, cycle_id="old")

        new_cycle_id = str(uuid.uuid4())
        mark_stale = _import_helper()
        mark_stale(conn, new_cycle_id)

        rows = conn.execute("SELECT active FROM patterns WHERE cycle_id = 'old'").fetchall()
        assert len(rows) == 2, "Expected 2 old pattern rows"
        for r in rows:
            assert r["active"] == 0, f"Expected active=0 for old pattern, got {r['active']}"

    def test_prior_suggestions_flipped_to_inactive(self, tmp_path):
        """Suggestions with cycle_id != new_cycle_id must have active=0 after call."""
        conn, db_path = _fresh_db(tmp_path)
        _seed_old_rows(conn, cycle_id="old")

        new_cycle_id = str(uuid.uuid4())
        mark_stale = _import_helper()
        mark_stale(conn, new_cycle_id)

        rows = conn.execute("SELECT active FROM suggestions WHERE cycle_id = 'old'").fetchall()
        assert len(rows) == 2, "Expected 2 old suggestion rows"
        for r in rows:
            assert r["active"] == 0, f"Expected active=0 for old suggestion, got {r['active']}"

    def test_prior_datasets_flipped_to_inactive(self, tmp_path):
        """Datasets with cycle_id != new_cycle_id must have active=0 after call."""
        conn, db_path = _fresh_db(tmp_path)
        _seed_old_rows(conn, cycle_id="old")

        new_cycle_id = str(uuid.uuid4())
        mark_stale = _import_helper()
        mark_stale(conn, new_cycle_id)

        rows = conn.execute("SELECT active FROM datasets WHERE cycle_id = 'old'").fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["active"] == 0, f"Expected active=0 for old dataset, got {r['active']}"

    def test_prior_pattern_errors_flipped_to_inactive(self, tmp_path):
        """pattern_errors with cycle_id != new must have active=0 after call."""
        conn, db_path = _fresh_db(tmp_path)
        _seed_old_rows(conn, cycle_id="old")

        new_cycle_id = str(uuid.uuid4())
        mark_stale = _import_helper()
        mark_stale(conn, new_cycle_id)

        rows = conn.execute("SELECT active FROM pattern_errors WHERE cycle_id = 'old'").fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["active"] == 0, f"Expected active=0 for old pattern_error, got {r['active']}"

    def test_applied_changes_untouched(self, tmp_path):
        """applied_changes rows must NOT be modified by mark_stale_for_new_cycle."""
        conn, db_path = _fresh_db(tmp_path)
        _seed_old_rows(conn, cycle_id="old")

        now = _now()
        # Insert applied_changes rows with superseded_at=NULL (active audit rows)
        for i in range(3):
            conn.execute(
                "INSERT INTO applied_changes "
                "(target_file, diff_before, diff_after, applied_at, superseded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("~/.claude/CLAUDE.md", f"before-{i}", f"after-{i}", now, None),
            )
        conn.commit()

        new_cycle_id = str(uuid.uuid4())
        mark_stale = _import_helper()
        mark_stale(conn, new_cycle_id)

        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM applied_changes WHERE superseded_at IS NULL"
        ).fetchone()["cnt"]
        assert count == 3, (
            f"applied_changes must be untouched by mark_stale_for_new_cycle, "
            f"expected 3 non-superseded rows, got {count}"
        )

    def test_new_cycle_rows_remain_active(self, tmp_path):
        """Rows already using the new cycle_id must still have active=1."""
        conn, db_path = _fresh_db(tmp_path)
        new_cycle_id = str(uuid.uuid4())
        now = _now()

        # Insert rows already tagged with new_cycle_id
        conn.execute(
            "INSERT INTO patterns (pattern_id, description, error_count, "
            "session_count, first_seen, last_seen, rank_score, created_at, "
            "updated_at, active, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pat-new", "New pattern", 1, 1, now, now, 0.9, now, now, 1, new_cycle_id),
        )
        conn.commit()

        mark_stale = _import_helper()
        mark_stale(conn, new_cycle_id)

        row = conn.execute(
            "SELECT active FROM patterns WHERE cycle_id = ?", (new_cycle_id,)
        ).fetchone()
        assert row is not None
        assert row["active"] == 1, (
            f"Row with new cycle_id must remain active=1, got {row['active']}"
        )

    def test_second_call_with_different_cycle_id_flips_previous(self, tmp_path):
        """A second call with a new cycle_id flips the previously-active set."""
        conn, db_path = _fresh_db(tmp_path)
        _seed_old_rows(conn, cycle_id="cycle-1")

        cycle_2 = str(uuid.uuid4())
        mark_stale = _import_helper()

        # First call: mark cycle-1 stale, cycle-2 is new
        mark_stale(conn, cycle_2)

        # Insert rows for cycle_2
        now = _now()
        conn.execute(
            "INSERT INTO patterns (pattern_id, description, error_count, "
            "session_count, first_seen, last_seen, rank_score, created_at, "
            "updated_at, active, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pat-cycle2", "Cycle 2 pattern", 1, 1, now, now, 0.9, now, now, 1, cycle_2),
        )
        conn.commit()

        # Second call: mark cycle-2 stale, cycle-3 is new
        cycle_3 = str(uuid.uuid4())
        mark_stale(conn, cycle_3)

        # cycle-2 rows should now be inactive
        row = conn.execute("SELECT active FROM patterns WHERE cycle_id = ?", (cycle_2,)).fetchone()
        assert row is not None
        assert row["active"] == 0, (
            f"cycle-2 pattern must become inactive after cycle-3 mark_stale, "
            f"got active={row['active']}"
        )

        # cycle-1 rows should also remain inactive (unchanged)
        old_rows = conn.execute(
            "SELECT active FROM patterns WHERE cycle_id = 'cycle-1'"
        ).fetchall()
        for r in old_rows:
            assert r["active"] == 0

    def test_idempotent_same_cycle_id(self, tmp_path):
        """Calling mark_stale with the same cycle_id twice has no additional effect."""
        conn, db_path = _fresh_db(tmp_path)
        _seed_old_rows(conn, cycle_id="old")

        new_cycle_id = str(uuid.uuid4())
        mark_stale = _import_helper()

        mark_stale(conn, new_cycle_id)
        mark_stale(conn, new_cycle_id)  # second call — must be idempotent

        rows = conn.execute("SELECT active FROM patterns WHERE cycle_id = 'old'").fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["active"] == 0, "Idempotent second call must not change state"
