"""Failing tests for sync.py — T033 (TDD red).

Tests assert (per contracts/storage-sync.md §4):
  1. sync_behavior_invocations() copies all rows from per-platform DB
  2. Second call copies zero rows (idempotency via INSERT OR IGNORE)
  3. since_timestamp scopes correctly — only rows >= timestamp copied
  4. Each mirrored row has platform='claude-code' discriminator
  5. Missing per-platform DB returns {platform: 0} gracefully

Run to confirm RED before T035:
    uv run pytest tests/unit/db/test_sync.py -v
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
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

_CANONICAL_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS ix_bi_identity
    ON behavior_invocations(platform, session_id, timestamp, tool_name)
"""


def _seed_platform_rows(path: Path, count: int = 10, ts_prefix: str = "2026-04-20T") -> None:
    """Insert `count` rows into a per-platform behavior_invocations DB."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_PLATFORM_DDL)
    for i in range(count):
        conn.execute(
            "INSERT INTO behavior_invocations "
            "(session_id, timestamp, tool_name, user_message) VALUES (?, ?, ?, ?)",
            (f"sess-{i:03d}", f"{ts_prefix}{i:02d}:00:00+00:00", "Read", f"msg-{i}"),
        )
    conn.commit()
    conn.close()


def _seed_canonical_schema(path: Path) -> None:
    """Create canonical sio.db schema with UNIQUE index."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CANONICAL_DDL)
    conn.execute(_CANONICAL_INDEX)
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
    conn.commit()
    conn.close()


def _import_sync(monkeypatch, sio_db_path: Path, platform_db_path: Path):
    """Import sync module with env vars pointing at tmp paths."""
    import importlib  # noqa: PLC0415

    monkeypatch.setenv("SIO_DB_PATH", str(sio_db_path))
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(platform_db_path))
    import sio.core.db.sync as sync_mod  # noqa: PLC0415

    importlib.reload(sync_mod)
    return sync_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sync_dbs(tmp_path: Path, monkeypatch):
    """Return (sio_db_path, platform_db_path) with schemas pre-created."""
    sio_path = tmp_path / "sio.db"
    plat_path = tmp_path / "claude-code" / "behavior_invocations.db"
    plat_path.parent.mkdir(parents=True, exist_ok=True)

    _seed_canonical_schema(sio_path)
    # Platform DB starts with 10 rows
    conn = sqlite3.connect(str(plat_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_PLATFORM_DDL)
    conn.commit()
    conn.close()

    monkeypatch.setenv("SIO_DB_PATH", str(sio_path))
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(plat_path))
    return sio_path, plat_path


# ---------------------------------------------------------------------------
# 1. Full sync copies all rows
# ---------------------------------------------------------------------------


def test_sync_copies_all_rows(sync_dbs, monkeypatch):
    """sync_behavior_invocations() must copy all platform rows to sio.db."""
    sio_path, plat_path = sync_dbs
    _seed_platform_rows(plat_path, count=10)

    import importlib  # noqa: PLC0415

    import sio.core.db.sync as sync_mod  # noqa: PLC0415

    importlib.reload(sync_mod)

    result = sync_mod.sync_behavior_invocations()

    assert "claude-code" in result, "Result must contain 'claude-code' key"
    assert result["claude-code"] == 10, f"Expected 10 rows copied, got {result['claude-code']}"

    conn = sqlite3.connect(str(sio_path))
    count = conn.execute("SELECT COUNT(*) FROM behavior_invocations").fetchone()[0]
    conn.close()
    assert count == 10, f"Expected 10 rows in sio.db, got {count}"


# ---------------------------------------------------------------------------
# 2. Second call copies zero rows (idempotency)
# ---------------------------------------------------------------------------


def test_sync_idempotent_second_call_copies_zero(sync_dbs, monkeypatch):
    """Second sync_behavior_invocations() call must copy 0 rows (INSERT OR IGNORE)."""
    sio_path, plat_path = sync_dbs
    _seed_platform_rows(plat_path, count=5)

    import importlib  # noqa: PLC0415

    import sio.core.db.sync as sync_mod  # noqa: PLC0415

    importlib.reload(sync_mod)

    sync_mod.sync_behavior_invocations()
    result2 = sync_mod.sync_behavior_invocations()

    assert result2["claude-code"] == 0, (
        f"Expected 0 rows on second sync, got {result2['claude-code']}"
    )

    conn = sqlite3.connect(str(sio_path))
    count = conn.execute("SELECT COUNT(*) FROM behavior_invocations").fetchone()[0]
    conn.close()
    assert count == 5, f"Expected 5 total rows after idempotent sync, got {count}"


# ---------------------------------------------------------------------------
# 3. since_timestamp scopes correctly
# ---------------------------------------------------------------------------


def test_sync_since_timestamp_scopes_correctly(sync_dbs, monkeypatch):
    """sync_behavior_invocations(since_timestamp=...) copies only rows >= timestamp."""
    sio_path, plat_path = sync_dbs

    # Seed rows with timestamps spanning two days
    conn = sqlite3.connect(str(plat_path))
    conn.execute(_PLATFORM_DDL)
    rows_old = [
        ("sess-old-0", "2026-04-19T10:00:00+00:00", "Read", "old-msg-0"),
        ("sess-old-1", "2026-04-19T11:00:00+00:00", "Bash", "old-msg-1"),
    ]
    rows_new = [
        ("sess-new-0", "2026-04-20T10:00:00+00:00", "Edit", "new-msg-0"),
        ("sess-new-1", "2026-04-20T11:00:00+00:00", "Write", "new-msg-1"),
        ("sess-new-2", "2026-04-20T12:00:00+00:00", "Read", "new-msg-2"),
    ]
    for r in rows_old + rows_new:
        conn.execute(
            "INSERT INTO behavior_invocations (session_id, timestamp, tool_name, user_message) "
            "VALUES (?, ?, ?, ?)",
            r,
        )
    conn.commit()
    conn.close()

    import importlib  # noqa: PLC0415

    import sio.core.db.sync as sync_mod  # noqa: PLC0415

    importlib.reload(sync_mod)

    result = sync_mod.sync_behavior_invocations(since_timestamp="2026-04-20T00:00:00+00:00")

    assert result["claude-code"] == 3, (
        f"Expected 3 new rows with since_timestamp filter, got {result['claude-code']}"
    )


# ---------------------------------------------------------------------------
# 4. Each mirrored row has platform='claude-code'
# ---------------------------------------------------------------------------


def test_sync_adds_platform_discriminator(sync_dbs, monkeypatch):
    """Each mirrored row in sio.db must have platform='claude-code'."""
    sio_path, plat_path = sync_dbs
    _seed_platform_rows(plat_path, count=3)

    import importlib  # noqa: PLC0415

    import sio.core.db.sync as sync_mod  # noqa: PLC0415

    importlib.reload(sync_mod)

    sync_mod.sync_behavior_invocations()

    conn = sqlite3.connect(str(sio_path))
    platforms = [
        r[0] for r in conn.execute("SELECT DISTINCT platform FROM behavior_invocations").fetchall()
    ]
    conn.close()

    assert platforms == ["claude-code"], (
        f"Expected only 'claude-code' platform discriminator, got {platforms}"
    )


# ---------------------------------------------------------------------------
# 5. Missing per-platform DB returns gracefully
# ---------------------------------------------------------------------------


def test_sync_missing_platform_db_returns_zero(tmp_path, monkeypatch):
    """sync_behavior_invocations() with missing platform DB returns {platform: 0}."""
    sio_path = tmp_path / "sio.db"
    plat_path = tmp_path / "claude-code" / "behavior_invocations.db"
    # Platform DB does NOT exist

    _seed_canonical_schema(sio_path)
    monkeypatch.setenv("SIO_DB_PATH", str(sio_path))
    monkeypatch.setenv("SIO_PLATFORM_DB_PATH", str(plat_path))

    import importlib  # noqa: PLC0415

    import sio.core.db.sync as sync_mod  # noqa: PLC0415

    importlib.reload(sync_mod)

    result = sync_mod.sync_behavior_invocations()

    assert "claude-code" in result, "Result must contain 'claude-code' even when DB missing"
    assert result["claude-code"] == 0, (
        f"Expected 0 for missing platform DB, got {result['claude-code']}"
    )
