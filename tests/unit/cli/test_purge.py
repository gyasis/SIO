"""Unit tests for `sio purge` — T052 (FR-025, M7).

Verifies:
1. `sio purge --days 30 --yes` deletes old error_records/flow_events from
   the main sio.db and does NOT touch the per-platform behavior_invocations DB.
2. `sio purge --days 30 --behavior-only --yes` also deletes behavior_invocations
   rows from sio.db.
3. The default query path uses SIO_DB_PATH (or ~/.sio/sio.db), never the legacy
   ~/.sio/<platform>/behavior_invocations.db for the primary purge target.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

from click.testing import CliRunner

from sio.cli.main import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sio_db(path: str) -> None:
    """Create a minimal sio.db with required tables and test rows."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS error_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mined_at TEXT NOT NULL,
            error_text TEXT
        );
        CREATE TABLE IF NOT EXISTS flow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mined_at TEXT NOT NULL,
            flow_text TEXT
        );
        CREATE TABLE IF NOT EXISTS behavior_invocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            platform TEXT,
            skill_name TEXT
        );
    """)
    # Rows older than 31 days (should be purged when --days 30)
    conn.executescript("""
        INSERT INTO error_records (mined_at, error_text)
            VALUES (datetime('now', '-40 days'), 'old error');
        INSERT INTO flow_events (mined_at, flow_text)
            VALUES (datetime('now', '-35 days'), 'old flow');
        INSERT INTO behavior_invocations (timestamp, platform, skill_name)
            VALUES (datetime('now', '-50 days'), 'claude-code', 'sio-scan');
        -- Recent rows -- must NOT be purged
        INSERT INTO error_records (mined_at, error_text)
            VALUES (datetime('now', '-5 days'), 'recent error');
        INSERT INTO flow_events (mined_at, flow_text)
            VALUES (datetime('now', '-2 days'), 'recent flow');
        INSERT INTO behavior_invocations (timestamp, platform, skill_name)
            VALUES (datetime('now', '-1 day'), 'claude-code', 'sio-health');
    """)
    conn.commit()
    conn.close()


def _make_platform_db(path: str) -> None:
    """Create a minimal per-platform behavior_invocations DB."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS behavior_invocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            platform TEXT,
            skill_name TEXT
        );
        INSERT INTO behavior_invocations (timestamp, platform, skill_name)
            VALUES (datetime('now', '-90 days'), 'claude-code', 'old-skill');
        INSERT INTO behavior_invocations (timestamp, platform, skill_name)
            VALUES (datetime('now', '-1 day'), 'claude-code', 'new-skill');
    """)
    conn.commit()
    conn.close()


def _count(db_path: str, table: str) -> int:
    """Count rows in a table without going through init_db."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _raw_db_conn(db_path):
    """Context manager that opens a raw sqlite3 connection (bypasses init_db)."""
    import contextlib

    @contextlib.contextmanager
    def _ctx(path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    return _ctx(db_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_purge_days_yes_deletes_old_records_leaves_platform_db():
    """sio purge --days 30 --yes removes old error_records/flow_events from sio.db
    but does NOT purge the per-platform behavior_invocations DB.
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        sio_db = os.path.join(tmpdir, "sio.db")
        platform_db_dir = os.path.join(tmpdir, "claude-code")
        os.makedirs(platform_db_dir, exist_ok=True)
        platform_db = os.path.join(platform_db_dir, "behavior_invocations.db")

        _make_sio_db(sio_db)
        _make_platform_db(platform_db)

        # Platform DB starts with 2 rows
        assert _count(platform_db, "behavior_invocations") == 2

        # Patch _db_conn so the CLI doesn't run init_db (which needs full schema)
        with patch("sio.cli.main._db_conn", side_effect=_raw_db_conn):
            env = {"SIO_DB_PATH": sio_db}
            result = runner.invoke(
                cli,
                ["purge", "--days", "30", "--yes"],
                env=env,
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output

        # sio.db: old rows gone, recent ones kept
        assert _count(sio_db, "error_records") == 1, "Recent error_records should survive purge"
        assert _count(sio_db, "flow_events") == 1, "Recent flow_events should survive purge"
        # Platform DB must be UNTOUCHED
        assert _count(platform_db, "behavior_invocations") == 2, (
            "Per-platform DB must not be touched by a non --behavior-only purge"
        )


def test_purge_behavior_only_also_deletes_behavior_invocations():
    """sio purge --days 30 --behavior-only --yes also purges behavior_invocations
    rows from sio.db.
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        sio_db = os.path.join(tmpdir, "sio.db")
        _make_sio_db(sio_db)

        with patch("sio.cli.main._db_conn", side_effect=_raw_db_conn):
            env = {"SIO_DB_PATH": sio_db}
            result = runner.invoke(
                cli,
                ["purge", "--days", "30", "--behavior-only", "--yes"],
                env=env,
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output

        # Old behavior_invocations should be gone; recent one kept
        assert _count(sio_db, "behavior_invocations") == 1, (
            "Old behavior_invocations should be purged; recent one must remain"
        )


def test_purge_targets_sio_db_path_env():
    """The purge command MUST use SIO_DB_PATH as its primary target
    (NOT the legacy per-platform path).
    """
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        sio_db = os.path.join(tmpdir, "custom_sio.db")
        _make_sio_db(sio_db)

        initial_errors = _count(sio_db, "error_records")

        with patch("sio.cli.main._db_conn", side_effect=_raw_db_conn):
            env = {"SIO_DB_PATH": sio_db}
            result = runner.invoke(
                cli,
                ["purge", "--days", "30", "--yes"],
                env=env,
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output

        # The database at SIO_DB_PATH should have been modified
        final_errors = _count(sio_db, "error_records")
        assert final_errors < initial_errors, (
            "SIO_DB_PATH database should have fewer rows after purge"
        )
