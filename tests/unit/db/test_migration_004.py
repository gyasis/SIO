"""Failing tests for scripts/migrate_004.py — T014 (TDD red).

These tests verify that migrate_004 applies all schema deltas from
data-model.md §2 in an additive, idempotent way.

Run to confirm RED before implementing migrate_004.py:
    uv run pytest tests/unit/db/test_migration_004.py -v
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module import helper — expect ImportError until migrate_004 is created
# ---------------------------------------------------------------------------

_MIGRATE_PATH = Path(__file__).parents[3] / "scripts" / "migrate_004.py"


def _load_migrate_004():
    """Load scripts/migrate_004.py as a module; pytest.skip if not found."""
    if not _MIGRATE_PATH.exists():
        pytest.fail(
            f"scripts/migrate_004.py not found at {_MIGRATE_PATH} — "
            "this test is expected to be RED until T015 is implemented."
        )
    spec = importlib.util.spec_from_file_location("migrate_004", _MIGRATE_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """Create a minimal SIO-style DB with the pre-migration schema."""
    db_path = tmp_path / "sio_test.db"
    conn = sqlite3.connect(str(db_path))

    # Minimal pre-migration tables that migrate_004 extends
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'applied',
            description TEXT
        );
        INSERT OR IGNORE INTO schema_version VALUES (1, datetime('now'), 'applied', 'baseline');

        CREATE TABLE IF NOT EXISTS behavior_invocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            tool_name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT UNIQUE,
            description TEXT NOT NULL,
            centroid_embedding BLOB
        );

        CREATE TABLE IF NOT EXISTS processed_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            mined_at TEXT NOT NULL,
            UNIQUE(file_path, file_hash)
        );

        CREATE TABLE IF NOT EXISTS error_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            error_text TEXT NOT NULL,
            pattern_id INTEGER,
            user_message TEXT,
            source_type TEXT NOT NULL DEFAULT 'tool',
            source_file TEXT NOT NULL DEFAULT '',
            mined_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS flow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            flow_hash TEXT NOT NULL,
            file_path TEXT,
            sequence TEXT NOT NULL,
            ngram_size INTEGER NOT NULL,
            was_successful INTEGER NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL,
            mined_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS applied_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER NOT NULL,
            target_file TEXT NOT NULL,
            diff_before TEXT NOT NULL,
            diff_after TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            positive_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            min_threshold INTEGER NOT NULL DEFAULT 5,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pattern_errors (
            pattern_id INTEGER NOT NULL,
            error_id INTEGER NOT NULL,
            PRIMARY KEY (pattern_id, error_id)
        );

        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id INTEGER,
            description TEXT NOT NULL,
            confidence REAL NOT NULL,
            proposed_change TEXT NOT NULL,
            target_file TEXT NOT NULL,
            change_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS optimized_modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module_type TEXT NOT NULL,
            optimizer_used TEXT NOT NULL,
            file_path TEXT NOT NULL,
            training_count INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ground_truth (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id TEXT NOT NULL,
            rule_title TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# 1. Module must exist (red: ImportError expected until T015)
# ---------------------------------------------------------------------------

def test_migrate_004_module_importable():
    """scripts/migrate_004.py must be importable as a module."""
    _load_migrate_004()


# ---------------------------------------------------------------------------
# 2. migrate() function must exist with correct signature
# ---------------------------------------------------------------------------

def test_migrate_004_has_migrate_function(seeded_db: Path):
    """migrate_004 must expose a migrate(db_path) function."""
    mod = _load_migrate_004()
    assert hasattr(mod, "migrate"), "migrate_004 must define a migrate(db_path) function"
    assert callable(mod.migrate)


# ---------------------------------------------------------------------------
# 3. Idempotency — calling migrate twice is safe
# ---------------------------------------------------------------------------

def test_migrate_004_idempotent(seeded_db: Path):
    """Calling migrate(db_path) twice must not raise and must not add duplicates."""
    mod = _load_migrate_004()
    mod.migrate(seeded_db)
    mod.migrate(seeded_db)  # second call must be safe

    conn = sqlite3.connect(str(seeded_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version=2"
    ).fetchone()[0]
    conn.close()
    assert count == 1, (
        f"Expected exactly 1 schema_version row with version=2 after double migrate; got {count}"
    )


# ---------------------------------------------------------------------------
# 4. All statements must be additive (no DROP, no destructive ALTER)
# ---------------------------------------------------------------------------

_ALLOWED_STMT_PATTERNS = re.compile(
    r"^\s*(CREATE TABLE IF NOT EXISTS|ALTER TABLE .+ ADD COLUMN"
    r"|CREATE INDEX IF NOT EXISTS|CREATE UNIQUE INDEX IF NOT EXISTS"
    r"|INSERT OR IGNORE INTO schema_version|UPDATE schema_version)",
    re.IGNORECASE,
)

_FORBIDDEN_PATTERNS = re.compile(
    r"\b(DROP TABLE|DROP COLUMN|DROP INDEX|RENAME COLUMN|DELETE FROM)\b",
    re.IGNORECASE,
)


def test_migrate_004_statements_are_additive():
    """Every SQL statement in migrate_004.py must be additive (CREATE/ALTER ADD/INDEX)."""
    if not _MIGRATE_PATH.exists():
        pytest.fail(f"scripts/migrate_004.py not found at {_MIGRATE_PATH}")

    source = _MIGRATE_PATH.read_text(encoding="utf-8")

    # Find SQL string literals (multi-line triple-quoted or single-line)
    # We check for forbidden keywords in the whole source as a conservative check
    matches = list(_FORBIDDEN_PATTERNS.finditer(source))
    assert not matches, (
        "migrate_004.py contains destructive SQL statement(s): "
        + ", ".join(m.group() for m in matches)
    )


# ---------------------------------------------------------------------------
# 5. Post-migration: required schema_version table
# ---------------------------------------------------------------------------

def test_migrate_004_schema_version_table_exists(seeded_db: Path):
    """After migration, schema_version table must exist."""
    mod = _load_migrate_004()
    mod.migrate(seeded_db)

    conn = sqlite3.connect(str(seeded_db))
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    conn.close()
    assert row is not None, "schema_version table missing after migrate_004"


# ---------------------------------------------------------------------------
# 6. Post-migration: required new columns exist
# ---------------------------------------------------------------------------

def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in info)


@pytest.mark.parametrize("table,column", [
    ("patterns", "centroid_model_version"),
    ("processed_sessions", "last_offset"),
    ("error_records", "parent_session_id"),
    ("flow_events", "parent_session_id"),
    ("applied_changes", "superseded_at"),
    ("patterns", "active"),
    ("datasets", "active"),
    ("pattern_errors", "active"),
    ("suggestions", "active"),
    ("optimized_modules", "optimizer_name"),
    ("ground_truth", "remapped_from_pattern_id"),
])
def test_migrate_004_new_columns_exist(seeded_db: Path, table: str, column: str):
    """After migration, each required new column must exist."""
    mod = _load_migrate_004()
    mod.migrate(seeded_db)

    conn = sqlite3.connect(str(seeded_db))
    exists = _column_exists(conn, table, column)
    conn.close()
    assert exists, f"Expected column '{column}' on table '{table}' after migrate_004"


# ---------------------------------------------------------------------------
# 7. Post-migration: required indexes exist
# ---------------------------------------------------------------------------

def _index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row is not None


@pytest.mark.parametrize("index_name", [
    "ix_bi_identity",
    "ix_bi_platform_timestamp",
    "ix_er_user_msg",
    "ix_er_error_text",
    "ix_fe_identity",
])
def test_migrate_004_indexes_exist(seeded_db: Path, index_name: str):
    """After migration, each required index must exist."""
    mod = _load_migrate_004()
    mod.migrate(seeded_db)

    conn = sqlite3.connect(str(seeded_db))
    exists = _index_exists(conn, index_name)
    conn.close()
    assert exists, f"Expected index '{index_name}' after migrate_004"
