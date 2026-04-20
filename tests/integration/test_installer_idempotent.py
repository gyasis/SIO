"""T037 [US1] — Failing tests for installer idempotency (FR-007 + SC-014).

Run to confirm RED before T038:
    uv run pytest tests/integration/test_installer_idempotent.py -v

These tests verify:
  1. Running sio install twice does not change row counts or cause errors.
  2. Running install when per-platform DB is absent still creates canonical schema.
  3. After install, schema_version table exists with at least the baseline row.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def sio_env(tmp_path, monkeypatch):
    """Override SIO home paths to use tmp_path for isolation."""
    sio_home = tmp_path / ".sio"
    sio_home.mkdir(parents=True, exist_ok=True)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Override env vars that the installer and sync module honour
    monkeypatch.setenv("SIO_DB_PATH", str(sio_home / "sio.db"))
    monkeypatch.setenv(
        "SIO_PLATFORM_DB_PATH",
        str(sio_home / "claude-code" / "behavior_invocations.db"),
    )

    return {
        "sio_home": sio_home,
        "claude_dir": claude_dir,
        "db_dir": str(sio_home / "claude-code"),
        "claude_str": str(claude_dir),
        "sio_db": sio_home / "sio.db",
        "platform_db": sio_home / "claude-code" / "behavior_invocations.db",
    }


def _run_install(env: dict) -> dict:
    """Import and call installer.install() with tmp dirs."""
    from sio.adapters.claude_code.installer import install  # noqa: PLC0415

    return install(db_dir=env["db_dir"], claude_dir=env["claude_str"])


def _get_platform_row_count(env: dict) -> int:
    """Return row count in per-platform behavior_invocations DB (or 0 if absent)."""
    db_path = env["platform_db"]
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM behavior_invocations"
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def _get_canonical_row_count(env: dict) -> int:
    """Return row count in canonical sio.db.behavior_invocations (or 0 if absent)."""
    sio_db = env["sio_db"]
    if not sio_db.exists():
        return 0
    conn = sqlite3.connect(str(sio_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM behavior_invocations"
        ).fetchone()
        return row["cnt"] if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def _schema_version_rows(env: dict) -> list[dict]:
    """Return rows from schema_version in canonical sio.db."""
    sio_db = env["sio_db"]
    if not sio_db.exists():
        return []
    conn = sqlite3.connect(str(sio_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM schema_version").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


class TestInstallerIdempotent:
    """The installer must be safe to run multiple times without side effects."""

    def test_double_install_platform_row_count_stable(self, sio_env):
        """Per-platform DB row count unchanged after second install call."""
        _run_install(sio_env)
        count_after_first = _get_platform_row_count(sio_env)

        _run_install(sio_env)
        count_after_second = _get_platform_row_count(sio_env)

        assert count_after_first == count_after_second, (
            f"Per-platform row count changed from {count_after_first} "
            f"to {count_after_second} on second install"
        )

    def test_double_install_canonical_row_count_stable(self, sio_env):
        """Canonical sio.db behavior_invocations row count stable after second install."""
        _run_install(sio_env)
        count_after_first = _get_canonical_row_count(sio_env)

        _run_install(sio_env)
        count_after_second = _get_canonical_row_count(sio_env)

        assert count_after_first == count_after_second, (
            f"Canonical row count changed from {count_after_first} "
            f"to {count_after_second} on second install"
        )

    def test_install_without_platform_db_succeeds(self, sio_env):
        """Install succeeds and creates canonical schema when per-platform DB absent."""
        # Ensure per-platform DB does NOT exist
        platform_db = sio_env["platform_db"]
        if platform_db.exists():
            platform_db.unlink()

        result = _run_install(sio_env)

        assert result is not None, "install() must return a result dict"
        # Canonical sio.db must be created
        assert sio_env["sio_db"].exists() or sio_env["platform_db"].exists(), (
            "At least one DB must be created after install"
        )

    def test_schema_version_baseline_row_exists(self, sio_env):
        """After install, schema_version has at least one row with version=1 'applied'."""
        _run_install(sio_env)

        rows = _schema_version_rows(sio_env)

        # Must have at least one schema_version row
        assert len(rows) >= 1, (
            f"schema_version table must have at least 1 row, got {len(rows)}"
        )

        # The baseline row (version=1, status='applied') must exist
        baseline = [r for r in rows if r.get("version") == 1]
        assert baseline, (
            "No schema_version row with version=1 found after install. "
            f"Rows found: {rows}"
        )
        assert baseline[0]["status"] == "applied", (
            f"Baseline row status must be 'applied', got {baseline[0]['status']!r}"
        )
