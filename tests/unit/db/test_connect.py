"""Failing tests for sio.core.db.connect.open_db — T008 (TDD red phase).

Tests assert the PRAGMA settings applied by the connection factory:
  - journal_mode = wal
  - busy_timeout = 30000
  - synchronous = 1 (NORMAL)
  - foreign_keys = 1 (ON)
  - read_only=True opens in RO mode and raises on write attempt

Run to confirm RED before implementing connect.py:
    uv run pytest tests/unit/db/test_connect.py -v
"""

import sqlite3
from pathlib import Path

import pytest


def _open_db(path: Path, read_only: bool = False) -> sqlite3.Connection:
    """Import and call open_db; raises ImportError if not yet implemented."""
    from sio.core.db.connect import open_db  # noqa: PLC0415
    return open_db(path, read_only=read_only)


# ---------------------------------------------------------------------------
# PRAGMA: journal_mode = WAL
# ---------------------------------------------------------------------------


def test_open_db_journal_mode_is_wal(tmp_path: Path):
    """PRAGMA journal_mode returns 'wal' after open_db()."""
    db_path = tmp_path / "test.db"
    conn = _open_db(db_path)
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row is not None
        assert row[0].lower() == "wal", f"Expected 'wal', got {row[0]!r}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PRAGMA: busy_timeout = 30000
# ---------------------------------------------------------------------------


def test_open_db_busy_timeout_is_30000(tmp_path: Path):
    """PRAGMA busy_timeout returns 30000 ms after open_db()."""
    db_path = tmp_path / "test.db"
    conn = _open_db(db_path)
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        assert row is not None
        assert int(row[0]) == 30000, f"Expected 30000, got {row[0]!r}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PRAGMA: synchronous = NORMAL (1)
# ---------------------------------------------------------------------------


def test_open_db_synchronous_is_normal(tmp_path: Path):
    """PRAGMA synchronous returns 1 (NORMAL) after open_db()."""
    db_path = tmp_path / "test.db"
    conn = _open_db(db_path)
    try:
        row = conn.execute("PRAGMA synchronous").fetchone()
        assert row is not None
        assert int(row[0]) == 1, (
            f"Expected synchronous=1 (NORMAL), got {row[0]!r}. "
            "Accepted values: 1 or 'NORMAL'."
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PRAGMA: foreign_keys = ON (1)
# ---------------------------------------------------------------------------


def test_open_db_foreign_keys_on(tmp_path: Path):
    """PRAGMA foreign_keys returns 1 (ON) after open_db()."""
    db_path = tmp_path / "test.db"
    conn = _open_db(db_path)
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row is not None
        assert int(row[0]) == 1, f"Expected foreign_keys=1, got {row[0]!r}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


def test_open_db_returns_sqlite_connection(tmp_path: Path):
    """open_db() returns a sqlite3.Connection instance."""
    db_path = tmp_path / "test.db"
    conn = _open_db(db_path)
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# read_only mode
# ---------------------------------------------------------------------------


def test_open_db_read_only_raises_on_write(tmp_path: Path):
    """open_db(path, read_only=True) raises on any write attempt.

    First create the DB in RW mode, then reopen in RO mode and assert that
    CREATE TABLE raises sqlite3.OperationalError (attempt to write a readonly
    database).
    """
    db_path = tmp_path / "readonly_test.db"
    # Create the DB first so it exists for RO open.
    rw_conn = _open_db(db_path, read_only=False)
    rw_conn.execute("CREATE TABLE IF NOT EXISTS sentinel (id INTEGER PRIMARY KEY)")
    rw_conn.commit()
    rw_conn.close()

    ro_conn = _open_db(db_path, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("INSERT INTO sentinel VALUES (1)")
            ro_conn.commit()
    finally:
        ro_conn.close()


def test_open_db_read_only_allows_select(tmp_path: Path):
    """open_db(path, read_only=True) still allows SELECT queries."""
    db_path = tmp_path / "readonly_test.db"
    rw_conn = _open_db(db_path, read_only=False)
    rw_conn.execute("CREATE TABLE t (x INTEGER)")
    rw_conn.execute("INSERT INTO t VALUES (42)")
    rw_conn.commit()
    rw_conn.close()

    ro_conn = _open_db(db_path, read_only=True)
    try:
        row = ro_conn.execute("SELECT x FROM t").fetchone()
        assert row is not None
        assert row[0] == 42
    finally:
        ro_conn.close()
