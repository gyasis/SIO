"""Failing tests for schema_version table and migration guards — T012 (TDD red).

Tests assert (per data-model.md §2.1):
  1. On first connect via schema helper, a baseline row
     (version=1, status='applied', description='baseline') is seeded.
  2. A migration inserts (N, now(), 'applying', ...) at start and updates to
     'applied' on success.
  3. If any row has status='applying', a refuse_to_start() helper raises
     PartialMigrationError.

Run to confirm RED before implementing schema.py additions:
    uv run pytest tests/unit/db/test_schema_version.py -v
"""

import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers — import under-test symbols
# ---------------------------------------------------------------------------


def _import_schema():
    """Import the schema module; raises if not yet implemented."""
    from sio.core.db import schema  # noqa: PLC0415

    return schema


def _import_error():
    """Import PartialMigrationError; raises if not yet defined."""
    from sio.core.db.schema import PartialMigrationError  # noqa: PLC0415

    return PartialMigrationError


def _connect(path: Path) -> sqlite3.Connection:
    """Open a plain sqlite3 connection (bypasses open_db to test schema init)."""
    return sqlite3.connect(str(path))


# ---------------------------------------------------------------------------
# 1. Baseline row seeded on first connect
# ---------------------------------------------------------------------------


def test_schema_version_table_created(tmp_path: Path):
    """schema_version table exists after calling ensure_schema_version()."""
    schema_mod = _import_schema()
    db_path = tmp_path / "test.db"
    conn = _connect(db_path)
    try:
        schema_mod.ensure_schema_version(conn)
        # Check table exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        assert row is not None, "schema_version table was not created"
    finally:
        conn.close()


def test_schema_version_baseline_row_seeded(tmp_path: Path):
    """On first call, a row (version=1, status='applied', description='baseline') exists."""
    schema_mod = _import_schema()
    db_path = tmp_path / "test.db"
    conn = _connect(db_path)
    try:
        schema_mod.ensure_schema_version(conn)
        row = conn.execute(
            "SELECT version, status, description FROM schema_version WHERE version=1"
        ).fetchone()
        assert row is not None, "Baseline row version=1 not found"
        version, status, description = row
        assert version == 1
        assert status == "applied", f"Expected status='applied', got {status!r}"
        assert description is not None and "baseline" in description.lower(), (
            f"Expected 'baseline' in description, got {description!r}"
        )
    finally:
        conn.close()


def test_schema_version_baseline_applied_at_is_utc(tmp_path: Path):
    """Baseline row applied_at is a non-empty string (UTC ISO-8601)."""
    schema_mod = _import_schema()
    db_path = tmp_path / "test.db"
    conn = _connect(db_path)
    try:
        schema_mod.ensure_schema_version(conn)
        row = conn.execute("SELECT applied_at FROM schema_version WHERE version=1").fetchone()
        assert row is not None
        assert row[0], "applied_at must not be empty"
    finally:
        conn.close()


def test_schema_version_idempotent(tmp_path: Path):
    """Calling ensure_schema_version() twice does not duplicate the baseline row."""
    schema_mod = _import_schema()
    db_path = tmp_path / "test.db"
    conn = _connect(db_path)
    try:
        schema_mod.ensure_schema_version(conn)
        schema_mod.ensure_schema_version(conn)
        count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        assert count == 1, f"Expected 1 row after double init, got {count}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Migration lifecycle: 'applying' → 'applied'
# ---------------------------------------------------------------------------


def test_migration_applying_then_applied(tmp_path: Path):
    """A migration row starts as 'applying' and updates to 'applied' on success."""
    schema_mod = _import_schema()
    db_path = tmp_path / "test.db"
    conn = _connect(db_path)
    try:
        schema_mod.ensure_schema_version(conn)

        # Simulate starting a migration (version 2).
        schema_mod.begin_migration(conn, version=2, description="add index")

        row = conn.execute("SELECT status FROM schema_version WHERE version=2").fetchone()
        assert row is not None, "Migration row version=2 not found after begin_migration"
        assert row[0] == "applying", f"Expected 'applying', got {row[0]!r}"

        # Simulate successful completion.
        schema_mod.finish_migration(conn, version=2)

        row = conn.execute("SELECT status FROM schema_version WHERE version=2").fetchone()
        assert row[0] == "applied", f"Expected 'applied', got {row[0]!r}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. refuse_to_start raises PartialMigrationError when 'applying' row exists
# ---------------------------------------------------------------------------


def test_refuse_to_start_raises_on_applying_row(tmp_path: Path):
    """refuse_to_start() raises PartialMigrationError when any row has status='applying'."""
    schema_mod = _import_schema()
    PartialMigrationError = _import_error()
    db_path = tmp_path / "test.db"
    conn = _connect(db_path)
    try:
        schema_mod.ensure_schema_version(conn)
        # Manually insert an 'applying' row to simulate a crashed migration.
        conn.execute(
            "INSERT INTO schema_version (version, applied_at, status, description) "
            "VALUES (99, datetime('now'), 'applying', 'crashed migration')"
        )
        conn.commit()

        with pytest.raises(PartialMigrationError):
            schema_mod.refuse_to_start(conn)
    finally:
        conn.close()


def test_refuse_to_start_passes_when_all_applied(tmp_path: Path):
    """refuse_to_start() does not raise when all rows have status='applied'."""
    schema_mod = _import_schema()
    db_path = tmp_path / "test.db"
    conn = _connect(db_path)
    try:
        schema_mod.ensure_schema_version(conn)
        # Should not raise — only the baseline 'applied' row exists.
        schema_mod.refuse_to_start(conn)
    finally:
        conn.close()


def test_partial_migration_error_is_exception():
    """PartialMigrationError is a subclass of Exception."""
    PartialMigrationError = _import_error()
    assert issubclass(PartialMigrationError, Exception)
