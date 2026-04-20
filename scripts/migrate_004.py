"""scripts/migrate_004.py — Apply schema deltas from data-model.md §2.

Applies every additive ALTER TABLE / CREATE INDEX from the 004-pipeline-integrity-remediation
spec.  Each ALTER TABLE ADD COLUMN is wrapped in a try/except that silently ignores
sqlite3.OperationalError "duplicate column name" so the script is fully idempotent.

Usage:
    python -m scripts.migrate_004 ~/.sio/sio.db        # apply to a DB
    python scripts/migrate_004.py ~/.sio/sio.db        # direct invocation

The migration is recorded as schema_version row (version=2, description='004-pipeline-integrity-remediation').
Subsequent calls detect the existing version=2 'applied' row and are no-ops.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    """ALTER TABLE ADD COLUMN, ignoring 'duplicate column name' errors."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def _create_index(conn: sqlite3.Connection, ddl: str) -> None:
    """Execute a CREATE [UNIQUE] INDEX IF NOT EXISTS statement."""
    conn.execute(ddl)


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """§2.1 — Create schema_version table if missing."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'applied',
            description TEXT
        )
    """)
    # Seed baseline row (version=1) if not already present.
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at, status, description) "
        "VALUES (1, datetime('now'), 'applied', 'baseline')"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def migrate(db_path: Path | str) -> None:
    """Apply all 004 schema deltas to the DB at *db_path*.

    Idempotent: safe to call multiple times.  Uses schema_version row
    (version=2) to detect whether the migration was already completed.
    """
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    try:
        # §2.1 — Ensure schema_version table + baseline row exist.
        _ensure_schema_version_table(conn)
        conn.commit()

        # Detect whether version=2 is already applied (idempotency gate).
        existing = conn.execute(
            "SELECT status FROM schema_version WHERE version = 2"
        ).fetchone()
        if existing and existing[0] == "applied":
            # Migration already complete; nothing to do.
            return

        # Begin migration — write 'applying' sentinel.
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at, status, description) "
            "VALUES (2, datetime('now'), 'applying', '004-pipeline-integrity-remediation')"
        )
        # Update to 'applying' if it somehow already existed in a different state.
        conn.execute(
            "UPDATE schema_version SET status='applying', applied_at=datetime('now') "
            "WHERE version=2 AND status != 'applied'"
        )
        conn.commit()

        # ---------------------------------------------------------------
        # §2.2 behavior_invocations — composite UNIQUE + platform index
        # ---------------------------------------------------------------
        _create_index(conn, """
            CREATE UNIQUE INDEX IF NOT EXISTS ix_bi_identity
                ON behavior_invocations(platform, session_id, timestamp, tool_name)
        """)
        _create_index(conn, """
            CREATE INDEX IF NOT EXISTS ix_bi_platform_timestamp
                ON behavior_invocations(platform, timestamp)
        """)

        # ---------------------------------------------------------------
        # §2.3 patterns — centroid model version column
        # ---------------------------------------------------------------
        _add_column(conn, "patterns", "centroid_model_version TEXT")

        # ---------------------------------------------------------------
        # §2.4 processed_sessions — byte-offset resume columns
        # ---------------------------------------------------------------
        _add_column(conn, "processed_sessions", "last_offset    INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "processed_sessions", "last_mtime     REAL")
        _add_column(conn, "processed_sessions", "is_subagent    INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "processed_sessions", "parent_session_id TEXT")

        # ---------------------------------------------------------------
        # §2.5 error_records — subagent linkage + hot-read indexes
        # ---------------------------------------------------------------
        _add_column(conn, "error_records", "parent_session_id TEXT")
        _add_column(conn, "error_records", "is_subagent INTEGER NOT NULL DEFAULT 0")
        _create_index(conn, """
            CREATE INDEX IF NOT EXISTS ix_er_user_msg
                ON error_records(user_message)
        """)
        _create_index(conn, """
            CREATE INDEX IF NOT EXISTS ix_er_error_text
                ON error_records(error_text)
        """)
        _create_index(conn, """
            CREATE INDEX IF NOT EXISTS ix_er_pattern_id
                ON error_records(pattern_id)
        """)

        # ---------------------------------------------------------------
        # §2.6 flow_events — dedup key + indexes
        # ---------------------------------------------------------------
        _add_column(conn, "flow_events", "parent_session_id TEXT")
        _add_column(conn, "flow_events", "is_subagent INTEGER NOT NULL DEFAULT 0")
        _create_index(conn, """
            CREATE UNIQUE INDEX IF NOT EXISTS ix_fe_identity
                ON flow_events(file_path, session_id, flow_hash)
        """)
        _create_index(conn, """
            CREATE INDEX IF NOT EXISTS ix_fe_success_hash
                ON flow_events(was_successful, flow_hash)
        """)

        # ---------------------------------------------------------------
        # §2.7 applied_changes — soft delete columns
        # ---------------------------------------------------------------
        _add_column(conn, "applied_changes", "superseded_at TEXT")
        _add_column(conn, "applied_changes", "superseded_by INTEGER")

        # ---------------------------------------------------------------
        # §2.8 patterns, datasets, pattern_errors, suggestions — active flag
        # ---------------------------------------------------------------
        _add_column(conn, "patterns",       "active INTEGER NOT NULL DEFAULT 1")
        _add_column(conn, "patterns",       "cycle_id TEXT")
        _add_column(conn, "datasets",       "active INTEGER NOT NULL DEFAULT 1")
        _add_column(conn, "datasets",       "cycle_id TEXT")
        _add_column(conn, "pattern_errors", "active INTEGER NOT NULL DEFAULT 1")
        _add_column(conn, "pattern_errors", "cycle_id TEXT")
        _add_column(conn, "suggestions",    "active INTEGER NOT NULL DEFAULT 1")
        _add_column(conn, "suggestions",    "cycle_id TEXT")

        # ---------------------------------------------------------------
        # §2.9 optimized_modules — DSPy persistence columns
        # ---------------------------------------------------------------
        _add_column(conn, "optimized_modules", "optimizer_name TEXT")
        _add_column(conn, "optimized_modules", "metric_name    TEXT")
        _add_column(conn, "optimized_modules", "trainset_size  INTEGER")
        _add_column(conn, "optimized_modules", "valset_size    INTEGER")
        _add_column(conn, "optimized_modules", "score          REAL")
        _add_column(conn, "optimized_modules", "reflection_lm  TEXT")
        _add_column(conn, "optimized_modules", "task_lm        TEXT")
        _add_column(conn, "optimized_modules", "artifact_path  TEXT")

        # ---------------------------------------------------------------
        # §2.10 ground_truth — slug remap column
        # ---------------------------------------------------------------
        _add_column(conn, "ground_truth", "remapped_from_pattern_id TEXT")

        conn.commit()

        # Mark migration as applied.
        conn.execute(
            "UPDATE schema_version SET status='applied', applied_at=datetime('now') "
            "WHERE version=2"
        )
        conn.commit()

    except Exception:
        conn.execute(
            "UPDATE schema_version SET status='failed' WHERE version=2"
        )
        conn.commit()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: python -m scripts.migrate_004 <db_path>", file=sys.stderr)
        return 1
    try:
        migrate(sys.argv[1])
        print(f"Migration 004 applied to {sys.argv[1]}")
        return 0
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
