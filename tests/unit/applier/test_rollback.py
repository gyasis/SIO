"""T050 [US2] — Failing tests for rollback_applied_change (FR-003).

Tests for:
- ``rollback_applied_change(applied_change_id, db_path=None)``
- ``BackupMissingError(Exception)`` (raised when backup file absent)

Both will be implemented in T051 in ``src/sio/core/applier/writer.py``.

Invariants:
- Happy path: target file reverts to the backed-up content, applied_changes
  row gets superseded_at set, function returns dict with rolled_back=True.
- Non-existent applied_change_id raises ValueError.
- Missing backup file raises BackupMissingError.

Run to confirm RED before T051:
    uv run pytest tests/unit/applier/test_rollback.py -v
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_db(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    """Minimal SIO DB with applied_changes + superseded columns."""
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
    return conn, str(db_path)


def _insert_applied_change(
    conn: sqlite3.Connection,
    target_file: str,
    backup_path: str | None,
    diff_before: str = "ORIGINAL",
    diff_after: str = "NEW",
) -> int:
    """Insert a row into applied_changes. Returns new row ID."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO applied_changes "
        "(target_file, diff_before, diff_after, backup_path, applied_at, "
        "superseded_at, superseded_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (target_file, diff_before, diff_after, backup_path, now, None, None),
    )
    conn.commit()
    return cur.lastrowid


def _import_rollback():
    from sio.core.applier.writer import (  # noqa: PLC0415
        BackupMissingError,
        rollback_applied_change,
    )
    return rollback_applied_change, BackupMissingError


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRollbackAppliedChange:
    """rollback_applied_change must revert target file and mark row superseded."""

    def test_importable(self):
        """rollback_applied_change and BackupMissingError must be importable."""
        fn, exc = _import_rollback()
        assert callable(fn)
        assert issubclass(exc, Exception)

    def test_happy_path_reverts_target_content(self, tmp_path, monkeypatch):
        """After rollback, target file must contain the backed-up (original) content."""
        from sio.core.applier.writer import atomic_write, ALLOWLIST_ROOTS  # noqa: PLC0415

        # Allow tmp_path in allowlist
        monkeypatch.setattr(
            "sio.core.applier.writer.ALLOWLIST_ROOTS",
            ALLOWLIST_ROOTS + [tmp_path],
        )

        target = tmp_path / "CLAUDE.md"
        original_content = "ORIGINAL CONTENT"
        new_content = "NEW CONTENT"

        # Write original content first
        target.write_text(original_content, encoding="utf-8")

        # Use atomic_write to simulate the apply step (writes NEW, creates backup)
        backup_path = atomic_write(target, new_content)

        # Confirm the write happened
        assert target.read_text(encoding="utf-8") == new_content

        # Set up DB
        conn, db_path = _fresh_db(tmp_path)
        row_id = _insert_applied_change(
            conn, str(target), str(backup_path),
            diff_before=original_content, diff_after=new_content,
        )
        conn.close()

        # Execute rollback
        rollback_fn, _ = _import_rollback()
        result = rollback_fn(row_id, db_path=db_path)

        # Target must revert to original
        reverted = target.read_text(encoding="utf-8")
        assert reverted == original_content, (
            f"Target must revert to original content after rollback. "
            f"Got: {reverted!r}"
        )

    def test_happy_path_sets_superseded_at(self, tmp_path, monkeypatch):
        """After rollback, applied_changes row must have superseded_at set."""
        from sio.core.applier.writer import atomic_write, ALLOWLIST_ROOTS  # noqa: PLC0415

        monkeypatch.setattr(
            "sio.core.applier.writer.ALLOWLIST_ROOTS",
            ALLOWLIST_ROOTS + [tmp_path],
        )

        target = tmp_path / "CLAUDE.md"
        target.write_text("ORIGINAL", encoding="utf-8")
        backup_path = atomic_write(target, "NEW")

        conn, db_path = _fresh_db(tmp_path)
        row_id = _insert_applied_change(conn, str(target), str(backup_path))
        conn.close()

        rollback_fn, _ = _import_rollback()
        rollback_fn(row_id, db_path=db_path)

        # Check DB
        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        row = check.execute(
            "SELECT superseded_at FROM applied_changes WHERE id = ?", (row_id,)
        ).fetchone()
        check.close()

        assert row is not None
        assert row["superseded_at"] is not None, (
            "applied_changes row must have superseded_at set after rollback"
        )

    def test_happy_path_returns_dict(self, tmp_path, monkeypatch):
        """rollback_applied_change must return a dict with rolled_back=True."""
        from sio.core.applier.writer import atomic_write, ALLOWLIST_ROOTS  # noqa: PLC0415

        monkeypatch.setattr(
            "sio.core.applier.writer.ALLOWLIST_ROOTS",
            ALLOWLIST_ROOTS + [tmp_path],
        )

        target = tmp_path / "CLAUDE.md"
        target.write_text("ORIGINAL", encoding="utf-8")
        backup_path = atomic_write(target, "NEW")

        conn, db_path = _fresh_db(tmp_path)
        row_id = _insert_applied_change(conn, str(target), str(backup_path))
        conn.close()

        rollback_fn, _ = _import_rollback()
        result = rollback_fn(row_id, db_path=db_path)

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result.get("rolled_back") is True, (
            f"Expected rolled_back=True, got {result.get('rolled_back')}"
        )
        assert "target" in result, "Result dict must include 'target' key"
        assert "applied_change_id" in result, "Result dict must include 'applied_change_id' key"

    def test_nonexistent_id_raises_value_error(self, tmp_path):
        """Calling rollback with a non-existent applied_change_id must raise ValueError."""
        conn, db_path = _fresh_db(tmp_path)
        conn.close()

        rollback_fn, _ = _import_rollback()
        with pytest.raises(ValueError, match=r"(not found|does not exist|no.*row)"):
            rollback_fn(99999, db_path=db_path)

    def test_missing_backup_file_raises_error(self, tmp_path):
        """When the backup file path doesn't exist, BackupMissingError must be raised."""
        conn, db_path = _fresh_db(tmp_path)
        missing_backup = str(tmp_path / "nonexistent_backup.bak")
        target = str(tmp_path / "CLAUDE.md")

        # Write target so DB row is "valid"
        Path(target).write_text("CONTENT", encoding="utf-8")

        row_id = _insert_applied_change(
            conn, target, missing_backup,
            diff_before="ORIGINAL", diff_after="CONTENT",
        )
        conn.close()

        rollback_fn, BackupMissingError = _import_rollback()
        with pytest.raises(BackupMissingError):
            rollback_fn(row_id, db_path=db_path)
