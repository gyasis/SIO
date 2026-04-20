"""Failing tests for sync drift computation — T034 (TDD red).

Tests assert:
  1. compute_sync_drift() returns dict with {platform: {canonical_count, per_platform_count, drift_pct}}
  2. 100 per-platform rows, 100 canonical rows -> drift_pct == 0.0
  3. 100 per-platform rows, 95 canonical rows -> drift_pct == 0.05
  4. Missing per-platform DB -> per_platform_count == 0, drift skipped gracefully

Run to confirm RED before T035:
    uv run pytest tests/unit/db/test_sync_drift.py -v
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# DDL helpers (mirrors test_sync.py to keep tests self-contained)
# ---------------------------------------------------------------------------

_PLATFORM_DDL = """
CREATE TABLE IF NOT EXISTS behavior_invocations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    tool_input   TEXT,
    user_message TEXT,
    activated    INTEGER,
    correct_action INTEGER,
    correct_outcome INTEGER,
    user_satisfied INTEGER,
    conversation_pointer TEXT
)
"""

_CANONICAL_DDL = """
CREATE TABLE IF NOT EXISTS behavior_invocations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    tool_input   TEXT,
    user_message TEXT,
    activated    INTEGER,
    correct_action INTEGER,
    correct_outcome INTEGER,
    user_satisfied INTEGER,
    conversation_pointer TEXT
)
"""


def _create_canonical_db(path: Path, row_count: int, platform: str = "claude-code") -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CANONICAL_DDL)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'applied',
            description TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version VALUES (1, datetime('now'), 'applied', 'baseline')"
    )
    for i in range(row_count):
        conn.execute(
            "INSERT INTO behavior_invocations "
            "(platform, session_id, timestamp, tool_name) VALUES (?, ?, ?, ?)",
            (platform, f"sess-{i:04d}", f"2026-04-20T{(i % 60):02d}:00:00+00:00", "Read"),
        )
    conn.commit()
    conn.close()


def _create_platform_db(path: Path, row_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_PLATFORM_DDL)
    for i in range(row_count):
        conn.execute(
            "INSERT INTO behavior_invocations (session_id, timestamp, tool_name) "
            "VALUES (?, ?, ?)",
            (f"sess-{i:04d}", f"2026-04-20T{(i % 60):02d}:00:00+00:00", "Read"),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def drift_dbs(tmp_path: Path, monkeypatch):
    """Return (sio_path, plat_path) factory callable."""
    def _setup(canonical_count: int, platform_count: int):
        sio_path = tmp_path / "sio.db"
        plat_path = tmp_path / "claude-code" / "behavior_invocations.db"
        _create_canonical_db(sio_path, canonical_count)
        _create_platform_db(plat_path, platform_count)
        monkeypatch.setenv("SIO_DB_PATH", str(sio_path))
        monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(plat_path))
        return sio_path, plat_path

    return _setup


def _reload_sync(monkeypatch=None):
    import importlib  # noqa: PLC0415
    import sio.core.db.sync as sync_mod  # noqa: PLC0415
    importlib.reload(sync_mod)
    return sync_mod


# ---------------------------------------------------------------------------
# 1. compute_sync_drift() returns correct dict structure
# ---------------------------------------------------------------------------

def test_compute_sync_drift_returns_dict(drift_dbs, monkeypatch):
    """compute_sync_drift() must return a dict keyed by platform."""
    drift_dbs(canonical_count=10, platform_count=10)
    sync_mod = _reload_sync()

    result = sync_mod.compute_sync_drift()

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "claude-code" in result, "Result must have 'claude-code' key"


def test_compute_sync_drift_result_has_required_keys(drift_dbs, monkeypatch):
    """Each platform entry must have canonical_count, per_platform_count, drift_pct."""
    drift_dbs(canonical_count=10, platform_count=10)
    sync_mod = _reload_sync()

    result = sync_mod.compute_sync_drift()
    entry = result["claude-code"]

    assert "canonical_count" in entry, "Missing 'canonical_count' in drift result"
    assert "per_platform_count" in entry, "Missing 'per_platform_count' in drift result"
    assert "drift_pct" in entry, "Missing 'drift_pct' in drift result"


# ---------------------------------------------------------------------------
# 2. 100/100 rows -> drift_pct == 0.0
# ---------------------------------------------------------------------------

def test_compute_sync_drift_zero_when_in_sync(drift_dbs, monkeypatch):
    """drift_pct must be 0.0 when canonical_count == per_platform_count."""
    drift_dbs(canonical_count=100, platform_count=100)
    sync_mod = _reload_sync()

    result = sync_mod.compute_sync_drift()
    drift = result["claude-code"]["drift_pct"]

    assert drift == 0.0, f"Expected drift_pct=0.0, got {drift}"


# ---------------------------------------------------------------------------
# 3. 100 per-platform, 95 canonical -> drift_pct == 0.05
# ---------------------------------------------------------------------------

def test_compute_sync_drift_correct_percentage(drift_dbs, monkeypatch):
    """drift_pct == (per_platform - canonical) / per_platform."""
    drift_dbs(canonical_count=95, platform_count=100)
    sync_mod = _reload_sync()

    result = sync_mod.compute_sync_drift()
    entry = result["claude-code"]

    assert entry["per_platform_count"] == 100, (
        f"Expected per_platform_count=100, got {entry['per_platform_count']}"
    )
    assert entry["canonical_count"] == 95, (
        f"Expected canonical_count=95, got {entry['canonical_count']}"
    )
    assert abs(entry["drift_pct"] - 0.05) < 0.001, (
        f"Expected drift_pct≈0.05, got {entry['drift_pct']}"
    )


# ---------------------------------------------------------------------------
# 4. Missing per-platform DB -> per_platform_count == 0
# ---------------------------------------------------------------------------

def test_compute_sync_drift_missing_platform_db(tmp_path, monkeypatch):
    """Missing platform DB must set per_platform_count=0 and not raise."""
    sio_path = tmp_path / "sio.db"
    plat_path = tmp_path / "claude-code" / "behavior_invocations.db"
    # Platform DB intentionally absent

    _create_canonical_db(sio_path, row_count=50)
    monkeypatch.setenv("SIO_DB_PATH", str(sio_path))
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(plat_path))

    sync_mod = _reload_sync()
    result = sync_mod.compute_sync_drift()

    assert "claude-code" in result, "Must still return 'claude-code' entry"
    entry = result["claude-code"]
    assert entry["per_platform_count"] == 0, (
        f"Expected per_platform_count=0 for missing DB, got {entry['per_platform_count']}"
    )
