"""T048 [US2] — Failing tests for applied_changes superseded_at / superseded_by.

Tests for two helpers (to be implemented in T049 in sio.core.db.queries):
- ``list_active_applied_changes(conn)`` — rows where superseded_at IS NULL
- ``mark_superseded(conn, id, by_id)``   — sets superseded_at and superseded_by

Invariants per data-model.md §2.7:
- ``list_active_applied_changes`` returns only rows with superseded_at IS NULL.
- ``mark_superseded`` sets superseded_at to the current UTC timestamp and
  superseded_by to the by_id argument (may be None).
- After marking superseded, the row no longer appears in list_active_applied_changes.
- Idempotent: marking an already-superseded row a second time does NOT change
  superseded_at (first-write-wins semantics to preserve audit trail).

Run to confirm RED before T049:
    uv run pytest tests/unit/db/test_superseded.py -v
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_db(tmp_path):
    """Minimal DB with applied_changes table including superseded columns."""
    db_path = tmp_path / "sio.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS applied_changes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER,
            target_file   TEXT,
            diff_before   TEXT,
            diff_after    TEXT,
            backup_path   TEXT,
            applied_at    TEXT,
            superseded_at TEXT,
            superseded_by INTEGER
        );
    """)
    conn.commit()
    return conn


def _insert_applied(conn, n: int = 3) -> list[int]:
    """Insert n applied_changes rows with superseded_at=NULL. Returns IDs."""
    now = _now()
    ids = []
    for i in range(n):
        cur = conn.execute(
            "INSERT INTO applied_changes "
            "(target_file, diff_before, diff_after, applied_at, superseded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("~/.claude/CLAUDE.md", f"before-{i}", f"after-{i}", now, None),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _import_fns():
    from sio.core.db.queries import (  # noqa: PLC0415
        list_active_applied_changes,
        mark_superseded,
    )

    return list_active_applied_changes, mark_superseded


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListActiveAppliedChanges:
    """list_active_applied_changes returns only rows with superseded_at IS NULL."""

    def test_importable(self):
        """list_active_applied_changes must be importable from queries."""
        fn, _ = _import_fns()
        assert callable(fn)

    def test_returns_all_when_none_superseded(self, tmp_path):
        """Returns all rows when none have been superseded."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=3)
        fn, _ = _import_fns()
        rows = fn(conn)
        assert len(rows) == 3, f"Expected 3 active rows, got {len(rows)}"
        returned_ids = {r["id"] for r in rows}
        assert returned_ids == set(ids)

    def test_excludes_superseded_rows(self, tmp_path):
        """Rows with superseded_at IS NOT NULL must not be returned."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=3)
        now = _now()
        # Manually mark first row as superseded
        conn.execute(
            "UPDATE applied_changes SET superseded_at = ? WHERE id = ?",
            (now, ids[0]),
        )
        conn.commit()

        fn, _ = _import_fns()
        rows = fn(conn)
        assert len(rows) == 2, f"Expected 2 active rows, got {len(rows)}"
        returned_ids = {r["id"] for r in rows}
        assert ids[0] not in returned_ids, "Superseded row must not appear in results"

    def test_empty_when_all_superseded(self, tmp_path):
        """Returns empty list when all rows are superseded."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=2)
        now = _now()
        for row_id in ids:
            conn.execute(
                "UPDATE applied_changes SET superseded_at = ? WHERE id = ?",
                (now, row_id),
            )
        conn.commit()

        fn, _ = _import_fns()
        rows = fn(conn)
        assert rows == [], f"Expected empty list when all superseded, got {rows}"

    def test_empty_table_returns_empty_list(self, tmp_path):
        """Returns empty list when applied_changes has no rows."""
        conn = _fresh_db(tmp_path)
        fn, _ = _import_fns()
        rows = fn(conn)
        assert rows == []


class TestMarkSuperseded:
    """mark_superseded sets superseded_at and superseded_by on a row."""

    def test_importable(self):
        """mark_superseded must be importable from queries."""
        _, fn = _import_fns()
        assert callable(fn)

    def test_sets_superseded_at(self, tmp_path):
        """After mark_superseded, superseded_at must be a non-null ISO timestamp."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=1)
        _, mark_fn = _import_fns()
        mark_fn(conn, ids[0], by_id=None)

        row = conn.execute(
            "SELECT superseded_at FROM applied_changes WHERE id = ?", (ids[0],)
        ).fetchone()
        assert row is not None
        assert row["superseded_at"] is not None, "superseded_at must be set"
        # Must be a valid ISO timestamp
        datetime.fromisoformat(row["superseded_at"])  # raises ValueError if invalid

    def test_sets_superseded_by(self, tmp_path):
        """superseded_by must be set to the by_id argument."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=2)
        _, mark_fn = _import_fns()
        mark_fn(conn, ids[0], by_id=ids[1])

        row = conn.execute(
            "SELECT superseded_by FROM applied_changes WHERE id = ?", (ids[0],)
        ).fetchone()
        assert row["superseded_by"] == ids[1], (
            f"Expected superseded_by={ids[1]}, got {row['superseded_by']}"
        )

    def test_sets_superseded_by_none(self, tmp_path):
        """by_id=None must be stored as NULL in superseded_by."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=1)
        _, mark_fn = _import_fns()
        mark_fn(conn, ids[0], by_id=None)

        row = conn.execute(
            "SELECT superseded_by FROM applied_changes WHERE id = ?", (ids[0],)
        ).fetchone()
        assert row["superseded_by"] is None, (
            f"Expected superseded_by=NULL when by_id=None, got {row['superseded_by']}"
        )

    def test_row_excluded_from_list_after_mark(self, tmp_path):
        """After mark_superseded, the row must not appear in list_active_applied_changes."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=2)
        list_fn, mark_fn = _import_fns()

        mark_fn(conn, ids[0], by_id=None)

        rows = list_fn(conn)
        returned_ids = {r["id"] for r in rows}
        assert ids[0] not in returned_ids, (
            "Superseded row must not appear in list_active_applied_changes"
        )
        assert ids[1] in returned_ids, "Unsuperseded row must still appear"

    def test_idempotent_does_not_change_superseded_at(self, tmp_path):
        """Calling mark_superseded twice must NOT change superseded_at (first-write-wins)."""
        conn = _fresh_db(tmp_path)
        ids = _insert_applied(conn, n=1)
        _, mark_fn = _import_fns()

        mark_fn(conn, ids[0], by_id=None)
        first_ts = conn.execute(
            "SELECT superseded_at FROM applied_changes WHERE id = ?", (ids[0],)
        ).fetchone()["superseded_at"]

        import time

        time.sleep(0.01)  # tiny delay to ensure timestamps differ if rewritten

        mark_fn(conn, ids[0], by_id=None)
        second_ts = conn.execute(
            "SELECT superseded_at FROM applied_changes WHERE id = ?", (ids[0],)
        ).fetchone()["superseded_at"]

        assert first_ts == second_ts, (
            f"mark_superseded must be idempotent (first-write-wins). "
            f"first={first_ts!r}, second={second_ts!r}"
        )
