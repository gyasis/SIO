"""TDD tests for v3 competitive-enhancement schema additions.

Tests that init_db(':memory:') creates the 5 new tables
(processed_sessions, session_metrics, positive_records, velocity_snapshots,
autoresearch_txlog), the new columns on existing tables (patterns.grade,
applied_changes.delta_type), CHECK constraints, and indexes.

Follows the pattern established in test_schema.py and test_v2_schema.py.
"""

from __future__ import annotations

import sqlite3

import pytest

from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Private helpers (mirror test_schema.py / test_v2_schema.py)
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


def _col_map(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    """Return {column_name: info_dict} for quick lookups."""
    return {c["name"]: c for c in _column_info(conn, table)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """In-memory database with full schema via init_db."""
    connection = init_db(":memory:")
    yield connection
    connection.close()


# ---------------------------------------------------------------------------
# T004-1: New tables exist
# ---------------------------------------------------------------------------


class TestNewTablesExist:
    """init_db(':memory:') creates all 5 competitive-enhancement tables."""

    NEW_TABLES = {
        "processed_sessions",
        "session_metrics",
        "positive_records",
        "velocity_snapshots",
        "autoresearch_txlog",
    }

    def test_all_five_new_tables_created(self, conn):
        tables = _table_names(conn)
        missing = self.NEW_TABLES - tables
        assert not missing, f"Missing new tables: {sorted(missing)}"

    @pytest.mark.parametrize("table_name", sorted(NEW_TABLES))
    def test_individual_table_exists(self, conn, table_name):
        tables = _table_names(conn)
        assert table_name in tables


# ---------------------------------------------------------------------------
# T004-2: New columns on existing tables
# ---------------------------------------------------------------------------


class TestPatternsGradeColumn:
    """patterns table has the new 'grade' column with correct default and CHECK."""

    def test_grade_column_exists(self, conn):
        cols = _col_map(conn, "patterns")
        assert "grade" in cols, f"patterns missing 'grade'. Columns: {sorted(cols)}"

    def test_grade_default_is_emerging(self, conn):
        cols = _col_map(conn, "patterns")
        assert cols["grade"]["dflt_value"] == "'emerging'"

    VALID_GRADES = ("emerging", "strong", "established", "declining")

    @pytest.mark.parametrize("grade", VALID_GRADES)
    def test_grade_accepts_valid_values(self, conn, grade):
        conn.execute(
            "INSERT INTO patterns "
            "(pattern_id, description, tool_name, error_count, session_count, "
            "first_seen, last_seen, rank_score, created_at, updated_at, grade) "
            "VALUES (?, 'test', 'Bash', 1, 1, "
            "datetime('now'), datetime('now'), 1.0, "
            "datetime('now'), datetime('now'), ?)",
            (f"pat-grade-{grade}", grade),
        )
        row = conn.execute(
            "SELECT grade FROM patterns WHERE pattern_id = ?",
            (f"pat-grade-{grade}",),
        ).fetchone()
        assert row[0] == grade

    def test_grade_rejects_invalid_value(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO patterns "
                "(pattern_id, description, tool_name, error_count, session_count, "
                "first_seen, last_seen, rank_score, created_at, updated_at, grade) "
                "VALUES ('pat-bad', 'test', 'Bash', 1, 1, "
                "datetime('now'), datetime('now'), 1.0, "
                "datetime('now'), datetime('now'), 'INVALID_GRADE')"
            )


class TestAppliedChangesDeltaTypeColumn:
    """applied_changes table has the new 'delta_type' column."""

    def test_delta_type_column_exists(self, conn):
        cols = _col_map(conn, "applied_changes")
        assert "delta_type" in cols, (
            f"applied_changes missing 'delta_type'. Columns: {sorted(cols)}"
        )

    def test_delta_type_default_is_append(self, conn):
        cols = _col_map(conn, "applied_changes")
        assert cols["delta_type"]["dflt_value"] == "'append'"

    VALID_DELTA_TYPES = ("append", "merge")

    @pytest.mark.parametrize("delta_type", VALID_DELTA_TYPES)
    def test_delta_type_accepts_valid_values(self, conn, delta_type):
        # Need a parent suggestion row first (FK constraint)
        conn.execute(
            "INSERT INTO suggestions "
            "(id, description, confidence, proposed_change, target_file, "
            "change_type, status, created_at) "
            "VALUES (999, 'test', 0.9, 'change', '/tmp/test', "
            "'append', 'pending', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO applied_changes "
            "(suggestion_id, target_file, diff_before, diff_after, "
            "applied_at, delta_type) "
            "VALUES (999, '/tmp/test', 'before', 'after', "
            "datetime('now'), ?)",
            (delta_type,),
        )
        row = conn.execute(
            "SELECT delta_type FROM applied_changes WHERE suggestion_id = 999"
        ).fetchone()
        assert row[0] == delta_type

    def test_delta_type_rejects_invalid_value(self, conn):
        conn.execute(
            "INSERT INTO suggestions "
            "(id, description, confidence, proposed_change, target_file, "
            "change_type, status, created_at) "
            "VALUES (998, 'test', 0.9, 'change', '/tmp/test', "
            "'append', 'pending', datetime('now'))"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO applied_changes "
                "(suggestion_id, target_file, diff_before, diff_after, "
                "applied_at, delta_type) "
                "VALUES (998, '/tmp/test', 'before', 'after', "
                "datetime('now'), 'INVALID_DELTA')"
            )


# ---------------------------------------------------------------------------
# T004-3: CHECK constraints on new tables
# ---------------------------------------------------------------------------


class TestPositiveRecordsSignalTypeCheck:
    """positive_records.signal_type CHECK constraint."""

    VALID_SIGNAL_TYPES = (
        "confirmation",
        "gratitude",
        "implicit_approval",
        "session_success",
    )

    @pytest.mark.parametrize("signal_type", VALID_SIGNAL_TYPES)
    def test_signal_type_accepts_valid(self, conn, signal_type):
        conn.execute(
            "INSERT INTO positive_records "
            "(session_id, timestamp, signal_type, signal_text, source_file, mined_at) "
            "VALUES ('sess-1', datetime('now'), ?, 'good job', 'test.jsonl', datetime('now'))",
            (signal_type,),
        )
        row = conn.execute(
            "SELECT signal_type FROM positive_records WHERE signal_type = ?",
            (signal_type,),
        ).fetchone()
        assert row[0] == signal_type

    def test_signal_type_rejects_invalid(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO positive_records "
                "(session_id, timestamp, signal_type, signal_text, source_file, mined_at) "
                "VALUES ('sess-1', datetime('now'), 'INVALID_SIGNAL', "
                "'text', 'test.jsonl', datetime('now'))"
            )


class TestAutoresearchTxlogActionCheck:
    """autoresearch_txlog.action CHECK constraint."""

    VALID_ACTIONS = (
        "mine",
        "cluster",
        "grade",
        "generate",
        "assert",
        "experiment_create",
        "validate",
        "promote",
        "rollback",
        "error",
        "stop",
    )

    @pytest.mark.parametrize("action", VALID_ACTIONS)
    def test_action_accepts_valid(self, conn, action):
        conn.execute(
            "INSERT INTO autoresearch_txlog "
            "(cycle_number, action, status, created_at) "
            "VALUES (1, ?, 'success', datetime('now'))",
            (action,),
        )

    def test_action_rejects_invalid(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO autoresearch_txlog "
                "(cycle_number, action, status, created_at) "
                "VALUES (1, 'INVALID_ACTION', 'success', datetime('now'))"
            )


class TestAutoresearchTxlogStatusCheck:
    """autoresearch_txlog.status CHECK constraint."""

    VALID_STATUSES = ("success", "failure", "skipped", "pending_approval")

    @pytest.mark.parametrize("status", VALID_STATUSES)
    def test_status_accepts_valid(self, conn, status):
        conn.execute(
            "INSERT INTO autoresearch_txlog "
            "(cycle_number, action, status, created_at) "
            "VALUES (1, 'mine', ?, datetime('now'))",
            (status,),
        )

    def test_status_rejects_invalid(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO autoresearch_txlog "
                "(cycle_number, action, status, created_at) "
                "VALUES (1, 'mine', 'INVALID_STATUS', datetime('now'))"
            )


# ---------------------------------------------------------------------------
# T004-4: Indexes exist
# ---------------------------------------------------------------------------


class TestNewIndexes:
    """All indexes defined in data-model.md for the 5 new tables exist."""

    EXPECTED_INDEXES = {
        # processed_sessions
        "idx_ps_path",
        "idx_ps_hash",
        # session_metrics
        "idx_sm_session",
        "idx_sm_mined",
        # positive_records
        "idx_pr_session",
        "idx_pr_type",
        "idx_pr_tool",
        # velocity_snapshots
        "idx_vs_type",
        "idx_vs_window",
        # autoresearch_txlog
        "idx_tx_cycle",
        "idx_tx_action",
    }

    def test_all_new_indexes_exist(self, conn):
        indexes = _index_names(conn)
        missing = self.EXPECTED_INDEXES - indexes
        assert not missing, f"Missing indexes: {sorted(missing)}"

    @pytest.mark.parametrize("index_name", sorted(EXPECTED_INDEXES))
    def test_individual_index_exists(self, conn, index_name):
        indexes = _index_names(conn)
        assert index_name in indexes


# ---------------------------------------------------------------------------
# T004-5: Processed sessions UNIQUE constraint
# ---------------------------------------------------------------------------


class TestProcessedSessionsUniqueConstraint:
    """UNIQUE(file_path, file_hash) prevents duplicate processing records."""

    def test_same_path_and_hash_rejected(self, conn):
        conn.execute(
            "INSERT INTO processed_sessions "
            "(file_path, file_hash, message_count, tool_call_count, mined_at) "
            "VALUES ('/tmp/test.jsonl', 'abc123', 10, 5, datetime('now'))"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO processed_sessions "
                "(file_path, file_hash, message_count, tool_call_count, mined_at) "
                "VALUES ('/tmp/test.jsonl', 'abc123', 10, 5, datetime('now'))"
            )

    def test_same_path_different_hash_allowed(self, conn):
        conn.execute(
            "INSERT INTO processed_sessions "
            "(file_path, file_hash, message_count, tool_call_count, mined_at) "
            "VALUES ('/tmp/test.jsonl', 'hash_v1', 10, 5, datetime('now'))"
        )
        conn.execute(
            "INSERT INTO processed_sessions "
            "(file_path, file_hash, message_count, tool_call_count, mined_at) "
            "VALUES ('/tmp/test.jsonl', 'hash_v2', 12, 6, datetime('now'))"
        )
        rows = conn.execute(
            "SELECT COUNT(*) FROM processed_sessions WHERE file_path = '/tmp/test.jsonl'"
        ).fetchone()
        assert rows[0] == 2

    def test_different_path_same_hash_allowed(self, conn):
        conn.execute(
            "INSERT INTO processed_sessions "
            "(file_path, file_hash, message_count, tool_call_count, mined_at) "
            "VALUES ('/tmp/a.jsonl', 'same_hash', 10, 5, datetime('now'))"
        )
        conn.execute(
            "INSERT INTO processed_sessions "
            "(file_path, file_hash, message_count, tool_call_count, mined_at) "
            "VALUES ('/tmp/b.jsonl', 'same_hash', 10, 5, datetime('now'))"
        )


# ---------------------------------------------------------------------------
# T004-6: Session metrics UNIQUE constraint on session_id
# ---------------------------------------------------------------------------


class TestSessionMetricsUniqueSessionId:
    """session_metrics.session_id is UNIQUE."""

    def test_duplicate_session_id_rejected(self, conn):
        conn.execute(
            "INSERT INTO session_metrics "
            "(session_id, file_path, mined_at) "
            "VALUES ('sess-dup', '/tmp/test.jsonl', datetime('now'))"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO session_metrics "
                "(session_id, file_path, mined_at) "
                "VALUES ('sess-dup', '/tmp/test2.jsonl', datetime('now'))"
            )


# ---------------------------------------------------------------------------
# T004-7: Column structure of new tables
# ---------------------------------------------------------------------------


class TestProcessedSessionsColumns:
    """processed_sessions table has expected columns with correct types."""

    EXPECTED_COLUMNS = {
        "id": "INTEGER",
        "file_path": "TEXT",
        "file_hash": "TEXT",
        "message_count": "INTEGER",
        "tool_call_count": "INTEGER",
        "skipped": "INTEGER",
        "mined_at": "TEXT",
    }

    def test_columns_present_with_types(self, conn):
        cols = _col_map(conn, "processed_sessions")
        for name, expected_type in self.EXPECTED_COLUMNS.items():
            assert name in cols, f"Missing column: {name}"
            assert cols[name]["type"] == expected_type, (
                f"Column {name} type mismatch: expected {expected_type}, "
                f"got {cols[name]['type']}"
            )

    def test_skipped_default_is_zero(self, conn):
        cols = _col_map(conn, "processed_sessions")
        assert cols["skipped"]["dflt_value"] == "0"


class TestSessionMetricsColumns:
    """session_metrics table has the full set of aggregate metric columns."""

    REQUIRED_COLUMNS = {
        "id",
        "session_id",
        "file_path",
        "total_input_tokens",
        "total_output_tokens",
        "total_cache_read_tokens",
        "total_cache_create_tokens",
        "cache_hit_ratio",
        "total_cost_usd",
        "session_duration_seconds",
        "message_count",
        "tool_call_count",
        "error_count",
        "correction_count",
        "positive_signal_count",
        "sidechain_count",
        "stop_reason_distribution",
        "model_used",
        "mined_at",
    }

    def test_all_columns_present(self, conn):
        cols = _col_map(conn, "session_metrics")
        missing = self.REQUIRED_COLUMNS - set(cols.keys())
        assert not missing, f"Missing columns: {sorted(missing)}"


class TestVelocitySnapshotsColumns:
    """velocity_snapshots table has expected columns."""

    REQUIRED_COLUMNS = {
        "id",
        "error_type",
        "session_id",
        "error_rate",
        "error_count_in_window",
        "window_start",
        "window_end",
        "rule_applied",
        "rule_suggestion_id",
        "created_at",
    }

    def test_all_columns_present(self, conn):
        cols = _col_map(conn, "velocity_snapshots")
        missing = self.REQUIRED_COLUMNS - set(cols.keys())
        assert not missing, f"Missing columns: {sorted(missing)}"

    def test_rule_applied_default_is_zero(self, conn):
        cols = _col_map(conn, "velocity_snapshots")
        assert cols["rule_applied"]["dflt_value"] == "0"


class TestAutoresearchTxlogColumns:
    """autoresearch_txlog table has expected columns."""

    REQUIRED_COLUMNS = {
        "id",
        "cycle_number",
        "action",
        "suggestion_id",
        "experiment_branch",
        "assertion_results",
        "details",
        "status",
        "created_at",
    }

    def test_all_columns_present(self, conn):
        cols = _col_map(conn, "autoresearch_txlog")
        missing = self.REQUIRED_COLUMNS - set(cols.keys())
        assert not missing, f"Missing columns: {sorted(missing)}"


# ---------------------------------------------------------------------------
# T004-8: Idempotency
# ---------------------------------------------------------------------------


class TestSchemaIdempotency:
    """Running init_db twice on the same database does not fail."""

    def test_reinit_does_not_raise(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        tables = _table_names(conn2)
        assert "processed_sessions" in tables
        assert "autoresearch_txlog" in tables
        conn2.close()
