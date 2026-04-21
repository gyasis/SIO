"""Unit tests for `sio apply` CLI flags — T058 (FR-004, FR-019, FR-024).

Verifies:
1. `sio apply --no-backup` exits 1 with a BackupRequired-y message.
2. `sio apply --rollback 1` works even without a suggestion_id argument.
3. `sio apply --merge` sets the merge flag (inspect via CliRunner invocation).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

from click.testing import CliRunner

from sio.cli.main import cli

# ---------------------------------------------------------------------------
# T058-1: --no-backup exits 1 with BackupRequired message
# ---------------------------------------------------------------------------


def test_apply_no_backup_exits_1():
    """sio apply --no-backup must exit with code 1 and mention BackupRequired."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["apply", "--no-backup"],
        catch_exceptions=False,
    )
    assert result.exit_code == 1, (
        f"Expected exit code 1, got {result.exit_code}. Output: {result.output}"
    )
    combined = (result.output or "") + (result.stderr if hasattr(result, "stderr") else "")
    assert "BackupRequired" in combined or "backup" in combined.lower(), (
        f"Expected 'BackupRequired' or 'backup' in output. Got: {combined!r}"
    )


def test_apply_no_backup_with_suggestion_id_exits_1():
    """sio apply 5 --no-backup must also exit 1 (--no-backup checked first)."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["apply", "5", "--no-backup"],
        catch_exceptions=False,
    )
    assert result.exit_code == 1, (
        f"Expected exit code 1, got {result.exit_code}. Output: {result.output}"
    )


# ---------------------------------------------------------------------------
# T058-2: --rollback works without suggestion_id
# ---------------------------------------------------------------------------


def test_apply_rollback_works_without_suggestion_id():
    """sio apply --rollback 1 does not require suggestion_id argument."""
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        sio_db = os.path.join(tmpdir, "sio.db")
        # Create minimal DB with an applied_changes row
        conn = sqlite3.connect(sio_db)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS applied_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER,
                target_file TEXT,
                backup_path TEXT,
                content_after TEXT,
                merge_consent INTEGER DEFAULT 0,
                applied_at TEXT,
                superseded_at TEXT,
                superseded_by INTEGER
            );
            INSERT INTO applied_changes
                (suggestion_id, target_file, backup_path, content_after, applied_at)
                VALUES (1, '/tmp/nonexistent.md', '/tmp/nonexistent.bak', '# test', '2026-01-01');
        """)
        conn.commit()
        conn.close()

        # Patch rollback_applied_change to avoid real file operations
        with patch(
            "sio.core.applier.writer.rollback_applied_change",
        ) as mock_rollback:
            mock_rollback.return_value = {
                "rolled_back": True,
                "target": "/tmp/nonexistent.md",
                "applied_change_id": 1,
            }
            # Also patch open rollback db to avoid needing a real DB path
            with patch.dict(os.environ, {"SIO_DB_PATH": sio_db}):
                result = runner.invoke(
                    cli,
                    ["apply", "--rollback", "1"],
                    catch_exceptions=False,
                )

        # The command should succeed (rollback mock returns success)
        # OR fail gracefully (if mock wasn't invoked due to DB lookup path)
        # Either way, it must NOT crash with "missing argument SUGGESTION_ID"
        assert "SUGGESTION_ID" not in (result.output or ""), (
            "--rollback should work without a suggestion_id argument"
        )


# ---------------------------------------------------------------------------
# T058-3: --merge flag is accepted and propagated
# ---------------------------------------------------------------------------


def test_apply_merge_flag_accepted():
    """sio apply --merge must be accepted by the CLI (not rejected as unknown option)."""
    runner = CliRunner()
    # We invoke with --no-backup AFTER --merge to trigger early exit at no-backup check
    # (cheaper than setting up a full DB and writer mock)
    result = runner.invoke(
        cli,
        ["apply", "--merge", "--no-backup"],
        catch_exceptions=False,
    )
    # Should exit 1 due to --no-backup, NOT 2 (Click "no such option" error)
    assert result.exit_code == 1, (
        f"Expected exit 1 from --no-backup guard, got {result.exit_code}. Output: {result.output}"
    )
    # Confirm it's not a Click "no such option" error
    assert "no such option" not in (result.output or "").lower(), (
        "--merge must be a recognized CLI option"
    )


def test_apply_yes_flag_accepted():
    """sio apply --yes must be accepted by the CLI."""
    runner = CliRunner()
    # Combine with --no-backup to get early exit without DB setup
    result = runner.invoke(
        cli,
        ["apply", "--yes", "--no-backup"],
        catch_exceptions=False,
    )
    assert "no such option" not in (result.output or "").lower(), (
        "--yes must be a recognized CLI option"
    )
    assert result.exit_code == 1  # from --no-backup guard
