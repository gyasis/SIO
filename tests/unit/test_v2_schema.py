"""TDD tests for v2 schema DDL extensions to sio.core.db.schema.

These tests are intentionally RED until init_db is extended to create the
v2 tables and indexes.  The tests assume init_db(":memory:") produces a
connection that contains both v1 and v2 objects.

Test strategy
-------------
- Use the shared ``tmp_db`` fixture (init_db(":memory:")) for existence and
  structural checks that depend on v2 being built into init_db itself.
- Inline helper functions mirror the pattern established in test_schema.py.
- Default-value tests require an actual INSERT so they use sqlite3 directly
  against the connection returned by init_db(":memory:").
"""

from __future__ import annotations

import sqlite3

import pytest

from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _column_info(conn: sqlite3.Connection, table: str) -> list[dict]:
    """Return PRAGMA table_info rows as dicts keyed by column attribute."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2],
            "notnull": r[3],
            "dflt_value": r[4],
            "pk": r[5],
        }
        for r in rows
    ]


def _index_info(conn: sqlite3.Connection, index_name: str) -> list[dict]:
    """Return PRAGMA index_info rows for the named index."""
    rows = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    return [{"seqno": r[0], "cid": r[1], "name": r[2]} for r in rows]


def _get_index_ddl(conn: sqlite3.Connection, index_name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row[0] if row else ""


def _insert_pattern(conn: sqlite3.Connection, pattern_id: str = "p-001") -> int:
    """Insert a minimal row into patterns and return the rowid."""
    now = "2026-02-25T00:00:00Z"
    cursor = conn.execute(
        """
        INSERT INTO patterns
            (pattern_id, description, error_count, session_count,
             first_seen, last_seen, rank_score, created_at, updated_at)
        VALUES (?, 'test pattern', 1, 1, ?, ?, 0.5, ?, ?)
        """,
        (pattern_id, now, now, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_error_record(conn: sqlite3.Connection, session_id: str = "sess-001") -> int:
    """Insert a minimal row into error_records and return the rowid."""
    now = "2026-02-25T00:00:00Z"
    cursor = conn.execute(
        """
        INSERT INTO error_records
            (session_id, timestamp, source_type, source_file,
             error_text, mined_at)
        VALUES (?, ?, 'specstory', 'some_file.md', 'err text', ?)
        """,
        (session_id, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_dataset(conn: sqlite3.Connection, pattern_row_id: int) -> int:
    """Insert a minimal row into datasets (omitting min_threshold) and return rowid."""
    now = "2026-02-25T00:00:00Z"
    cursor = conn.execute(
        """
        INSERT INTO datasets
            (pattern_id, file_path, positive_count, negative_count,
             created_at, updated_at)
        VALUES (?, '/tmp/ds.jsonl', 3, 2, ?, ?)
        """,
        (pattern_row_id, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_suggestion(
    conn: sqlite3.Connection,
    pattern_row_id: int,
    dataset_row_id: int,
) -> int:
    """Insert a minimal suggestion (omitting status) and return rowid."""
    now = "2026-02-25T00:00:00Z"
    cursor = conn.execute(
        """
        INSERT INTO suggestions
            (pattern_id, dataset_id, description, confidence,
             proposed_change, target_file, change_type, created_at)
        VALUES (?, ?, 'fix the bug', 0.9, 'diff text', 'src/main.py', 'edit', ?)
        """,
        (pattern_row_id, dataset_row_id, now),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Test: v2 tables created by init_db
# ---------------------------------------------------------------------------

V2_TABLES = {
    "error_records",
    "patterns",
    "pattern_errors",
    "datasets",
    "suggestions",
    "applied_changes",
}


def test_v2_tables_created(tmp_db: sqlite3.Connection) -> None:
    """init_db must create all six v2 tables."""
    tables = _table_names(tmp_db)
    missing = V2_TABLES - tables
    assert not missing, f"Missing v2 tables: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Test: v1 tables are still present after v2 extension
# ---------------------------------------------------------------------------

V1_TABLES = {
    "behavior_invocations",
    "optimization_runs",
    "gold_standards",
    "platform_config",
}


def test_v1_tables_still_exist(tmp_db: sqlite3.Connection) -> None:
    """Adding v2 tables must not remove or rename any v1 table."""
    tables = _table_names(tmp_db)
    missing = V1_TABLES - tables
    assert not missing, f"v1 tables missing after v2 init: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Test: WAL mode is active
# ---------------------------------------------------------------------------


def test_wal_mode(tmp_path) -> None:
    """journal_mode must be WAL after init_db on a file-based database.

    SQLite in-memory databases always report 'memory' for journal_mode
    regardless of the PRAGMA call, so this test uses a temporary file.
    """
    db_path = str(tmp_path / "wal_test.db")
    conn = init_db(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal", f"Expected 'wal', got '{mode}'"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test: v2 indexes are created
# ---------------------------------------------------------------------------

V2_INDEXES = {
    "idx_error_session",
    "idx_error_type",
    "idx_error_tool",
    "idx_error_timestamp",
    "idx_pattern_rank",
    "idx_suggestion_status",
}


def test_v2_indexes_created(tmp_db: sqlite3.Connection) -> None:
    """init_db must create all six v2 indexes."""
    indexes = _index_names(tmp_db)
    missing = V2_INDEXES - indexes
    assert not missing, f"Missing v2 indexes: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Test: error_records column structure
# ---------------------------------------------------------------------------

_ERROR_RECORDS_EXPECTED_COLUMNS = {
    "id": "INTEGER",
    "session_id": "TEXT",
    "timestamp": "TEXT",
    "source_type": "TEXT",
    "source_file": "TEXT",
    "tool_name": "TEXT",
    "error_text": "TEXT",
    "user_message": "TEXT",
    "context_before": "TEXT",
    "context_after": "TEXT",
    "error_type": "TEXT",
    "mined_at": "TEXT",
}

_ERROR_RECORDS_NOT_NULL = {"session_id", "timestamp", "source_type", "source_file", "error_text", "mined_at"}


def test_error_records_columns(tmp_db: sqlite3.Connection) -> None:
    """error_records must have all 12 columns with correct affinity types."""
    columns = _column_info(tmp_db, "error_records")
    col_map = {c["name"]: c["type"] for c in columns}

    for col_name, expected_type in _ERROR_RECORDS_EXPECTED_COLUMNS.items():
        assert col_name in col_map, f"error_records missing column '{col_name}'"
        assert col_map[col_name] == expected_type, (
            f"error_records.{col_name}: expected type '{expected_type}', "
            f"got '{col_map[col_name]}'"
        )


def test_error_records_not_null_constraints(tmp_db: sqlite3.Connection) -> None:
    """Mandatory error_records columns must carry NOT NULL."""
    columns = _column_info(tmp_db, "error_records")
    col_map = {c["name"]: c for c in columns}
    for col_name in _ERROR_RECORDS_NOT_NULL:
        assert col_map[col_name]["notnull"] == 1, (
            f"error_records.{col_name} should be NOT NULL"
        )


def test_error_records_id_primary_key(tmp_db: sqlite3.Connection) -> None:
    """error_records.id must be the primary key."""
    columns = _column_info(tmp_db, "error_records")
    id_col = next((c for c in columns if c["name"] == "id"), None)
    assert id_col is not None, "error_records.id column not found"
    assert id_col["pk"] == 1, "error_records.id must be the primary key"


# ---------------------------------------------------------------------------
# Test: patterns.pattern_id UNIQUE constraint
# ---------------------------------------------------------------------------


def test_patterns_columns(tmp_db: sqlite3.Connection) -> None:
    """patterns must have all expected columns including pattern_id."""
    expected = {
        "id": "INTEGER",
        "pattern_id": "TEXT",
        "description": "TEXT",
        "tool_name": "TEXT",
        "error_count": "INTEGER",
        "session_count": "INTEGER",
        "first_seen": "TEXT",
        "last_seen": "TEXT",
        "rank_score": "REAL",
        "centroid_embedding": "BLOB",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }
    columns = _column_info(tmp_db, "patterns")
    col_map = {c["name"]: c["type"] for c in columns}
    for col_name, expected_type in expected.items():
        assert col_name in col_map, f"patterns missing column '{col_name}'"
        assert col_map[col_name] == expected_type, (
            f"patterns.{col_name}: expected '{expected_type}', got '{col_map[col_name]}'"
        )


def test_patterns_pattern_id_unique(tmp_db: sqlite3.Connection) -> None:
    """Inserting two patterns with the same pattern_id must raise IntegrityError."""
    now = "2026-02-25T00:00:00Z"
    common_args = ("p-dup", "description", 1, 1, now, now, 0.5, now, now)
    insert_sql = """
        INSERT INTO patterns
            (pattern_id, description, error_count, session_count,
             first_seen, last_seen, rank_score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    tmp_db.execute(insert_sql, common_args)
    tmp_db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.execute(insert_sql, common_args)
        tmp_db.commit()


# ---------------------------------------------------------------------------
# Test: pattern_errors composite primary key
# ---------------------------------------------------------------------------


def test_pattern_errors_composite_pk(tmp_db: sqlite3.Connection) -> None:
    """pattern_errors PRIMARY KEY (pattern_id, error_id) must reject duplicates."""
    pattern_row_id = _insert_pattern(tmp_db, "p-pe-001")
    error_row_id = _insert_error_record(tmp_db, "sess-pe-001")

    tmp_db.execute(
        "INSERT INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
        (pattern_row_id, error_row_id),
    )
    tmp_db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.execute(
            "INSERT INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
            (pattern_row_id, error_row_id),
        )
        tmp_db.commit()


def test_pattern_errors_fk_references_patterns(tmp_db: sqlite3.Connection) -> None:
    """pattern_errors.pattern_id must reference a real patterns row."""
    error_row_id = _insert_error_record(tmp_db, "sess-fk-001")
    # Use a pattern rowid that does not exist.
    nonexistent_pattern_id = 999_999

    # Enable FK enforcement — SQLite doesn't enforce by default.
    tmp_db.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.execute(
            "INSERT INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
            (nonexistent_pattern_id, error_row_id),
        )
        tmp_db.commit()


def test_pattern_errors_fk_references_error_records(tmp_db: sqlite3.Connection) -> None:
    """pattern_errors.error_id must reference a real error_records row."""
    pattern_row_id = _insert_pattern(tmp_db, "p-fk-002")
    nonexistent_error_id = 999_999

    tmp_db.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.execute(
            "INSERT INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
            (pattern_row_id, nonexistent_error_id),
        )
        tmp_db.commit()


# ---------------------------------------------------------------------------
# Test: suggestions.status DEFAULT 'pending'
# ---------------------------------------------------------------------------


def test_suggestions_default_status(tmp_db: sqlite3.Connection) -> None:
    """Inserting a suggestion without status must default to 'pending'."""
    pattern_row_id = _insert_pattern(tmp_db, "p-sug-001")
    dataset_row_id = _insert_dataset(tmp_db, pattern_row_id)
    suggestion_row_id = _insert_suggestion(tmp_db, pattern_row_id, dataset_row_id)

    row = tmp_db.execute(
        "SELECT status FROM suggestions WHERE id = ?",
        (suggestion_row_id,),
    ).fetchone()

    assert row is not None, "Suggestion row not found after insert"
    assert row[0] == "pending", f"Expected default status 'pending', got '{row[0]}'"


# ---------------------------------------------------------------------------
# Test: datasets.min_threshold DEFAULT 5
# ---------------------------------------------------------------------------


def test_datasets_default_threshold(tmp_db: sqlite3.Connection) -> None:
    """Inserting a dataset without min_threshold must default to 5."""
    pattern_row_id = _insert_pattern(tmp_db, "p-ds-001")
    dataset_row_id = _insert_dataset(tmp_db, pattern_row_id)

    row = tmp_db.execute(
        "SELECT min_threshold FROM datasets WHERE id = ?",
        (dataset_row_id,),
    ).fetchone()

    assert row is not None, "Dataset row not found after insert"
    assert row[0] == 5, f"Expected default min_threshold 5, got '{row[0]}'"


# ---------------------------------------------------------------------------
# Test: idx_pattern_rank is a DESC index on rank_score
# ---------------------------------------------------------------------------


def test_idx_pattern_rank_is_on_rank_score(tmp_db: sqlite3.Connection) -> None:
    """idx_pattern_rank must index the rank_score column of patterns."""
    info = _index_info(tmp_db, "idx_pattern_rank")
    assert info, "idx_pattern_rank returned no column info"
    col_names = {row["name"] for row in info}
    assert "rank_score" in col_names, (
        f"idx_pattern_rank does not cover rank_score; covers: {col_names}"
    )


def test_idx_pattern_rank_ddl_contains_desc(tmp_db: sqlite3.Connection) -> None:
    """The DDL for idx_pattern_rank must specify DESC ordering on rank_score."""
    ddl = _get_index_ddl(tmp_db, "idx_pattern_rank")
    assert "DESC" in ddl.upper(), (
        f"idx_pattern_rank DDL missing DESC: {ddl!r}"
    )


# ---------------------------------------------------------------------------
# Test: init_db is idempotent across v1+v2 tables
# ---------------------------------------------------------------------------


def test_init_db_idempotent_v2(tmp_path) -> None:
    """Calling init_db twice on the same file must not raise or duplicate tables."""
    db_path = str(tmp_path / "idempotent_v2.db")
    conn1 = init_db(db_path)
    conn1.close()

    conn2 = init_db(db_path)
    tables = _table_names(conn2)
    conn2.close()

    assert V2_TABLES.issubset(tables), (
        f"v2 tables missing on second init: {V2_TABLES - tables}"
    )
    assert V1_TABLES.issubset(tables), (
        f"v1 tables missing on second init: {V1_TABLES - tables}"
    )
