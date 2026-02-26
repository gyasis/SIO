"""TDD tests for sio.core.db.schema — database DDL and initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sio.core.db.schema import init_db


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_sio.db")


@pytest.fixture()
def conn(db_path: str) -> sqlite3.Connection:
    connection = init_db(db_path)
    yield connection
    connection.close()


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _index_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _column_info(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [{"cid": r[0], "name": r[1], "type": r[2], "notnull": r[3], "dflt_value": r[4], "pk": r[5]} for r in rows]


class TestTablesCreated:
    EXPECTED_TABLES = {"behavior_invocations", "optimization_runs", "gold_standards", "platform_config"}

    def test_tables_created(self, conn):
        tables = _table_names(conn)
        assert self.EXPECTED_TABLES.issubset(tables), f"Missing tables: {self.EXPECTED_TABLES - tables}"


class TestPragmas:
    def test_wal_mode_enabled(self, conn):
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_busy_timeout(self, conn):
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 1000

    def test_auto_vacuum(self, conn):
        value = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        assert value == 2  # INCREMENTAL


class TestIndexes:
    EXPECTED_INDEXES = {"idx_session", "idx_platform_behavior", "idx_satisfaction", "idx_timestamp"}

    def test_indexes_exist(self, conn):
        indexes = _index_names(conn)
        assert self.EXPECTED_INDEXES.issubset(indexes), f"Missing indexes: {self.EXPECTED_INDEXES - indexes}"


class TestBehaviorInvocationsColumns:
    EXPECTED_COLUMNS = {
        "id": "INTEGER", "session_id": "TEXT", "timestamp": "TEXT", "platform": "TEXT",
        "user_message": "TEXT", "behavior_type": "TEXT", "actual_action": "TEXT",
        "expected_action": "TEXT", "activated": "INTEGER", "correct_action": "INTEGER",
        "correct_outcome": "INTEGER", "user_satisfied": "INTEGER", "user_note": "TEXT",
        "passive_signal": "TEXT", "history_file": "TEXT", "line_start": "INTEGER",
        "line_end": "INTEGER", "token_count": "INTEGER", "latency_ms": "INTEGER",
        "labeled_by": "TEXT", "labeled_at": "TEXT",
    }

    def test_behavior_invocations_columns(self, conn):
        columns = _column_info(conn, "behavior_invocations")
        col_map = {c["name"]: c["type"] for c in columns}
        for name, expected_type in self.EXPECTED_COLUMNS.items():
            assert name in col_map, f"Missing column: {name}"
            assert col_map[name] == expected_type

    def test_behavior_invocations_column_count(self, conn):
        columns = _column_info(conn, "behavior_invocations")
        assert len(columns) >= 20

    def test_id_is_primary_key(self, conn):
        columns = _column_info(conn, "behavior_invocations")
        id_col = next(c for c in columns if c["name"] == "id")
        assert id_col["pk"] == 1

    def test_not_null_constraints(self, conn):
        columns = _column_info(conn, "behavior_invocations")
        col_map = {c["name"]: c for c in columns}
        for name in ("session_id", "timestamp", "platform", "user_message", "behavior_type"):
            assert col_map[name]["notnull"] == 1, f"Column '{name}' should be NOT NULL"


class TestIdempotency:
    def test_idempotent_reinit(self, db_path):
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        tables = _table_names(conn2)
        assert "behavior_invocations" in tables
        conn2.close()


class TestCheckConstraints:
    VALID_TYPES = ("skill", "mcp_tool", "preference", "instructions_rule")

    def test_behavior_type_check_rejects_invalid(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO behavior_invocations (session_id, timestamp, platform, user_message, behavior_type) "
                "VALUES ('s1', '2026-01-01T00:00:00Z', 'claude', 'hello', 'INVALID_TYPE')"
            )

    @pytest.mark.parametrize("btype", VALID_TYPES)
    def test_behavior_type_check_accepts_valid(self, conn, btype):
        conn.execute(
            "INSERT INTO behavior_invocations (session_id, timestamp, platform, user_message, behavior_type) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'claude', 'hello', ?)",
            (f"sess-{btype}", btype),
        )
        row = conn.execute("SELECT behavior_type FROM behavior_invocations WHERE session_id = ?", (f"sess-{btype}",)).fetchone()
        assert row[0] == btype


# ---------------------------------------------------------------------------
# T006: DSPy suggestion engine schema extensions
# ---------------------------------------------------------------------------


class TestGroundTruthTable:
    """ground_truth table must exist with CHECK constraints for DSPy training data."""

    def test_ground_truth_table_exists(self, conn):
        tables = _table_names(conn)
        assert "ground_truth" in tables, f"Missing 'ground_truth' table. Found: {sorted(tables)}"

    VALID_TARGET_SURFACES = (
        "skill", "mcp_tool", "preference", "instructions_rule",
        "claude_md", "hook", "workflow",
    )

    @pytest.mark.parametrize("surface", VALID_TARGET_SURFACES)
    def test_ground_truth_target_surface_accepts_valid(self, conn, surface):
        conn.execute(
            "INSERT INTO ground_truth "
            "(pattern_id, error_example, target_surface, label, source, created_at) "
            "VALUES (1, 'example error', ?, 'pending', 'seed', '2026-01-01T00:00:00Z')",
            (surface,),
        )
        row = conn.execute(
            "SELECT target_surface FROM ground_truth WHERE target_surface = ?",
            (surface,),
        ).fetchone()
        assert row[0] == surface

    def test_ground_truth_target_surface_check(self, conn):
        """CHECK constraint rejects invalid target_surface values."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_example, target_surface, label, source, created_at) "
                "VALUES (1, 'err', 'INVALID_SURFACE', 'pending', 'seed', '2026-01-01T00:00:00Z')"
            )

    VALID_LABELS = ("pending", "positive", "negative")

    @pytest.mark.parametrize("label", VALID_LABELS)
    def test_ground_truth_label_accepts_valid(self, conn, label):
        conn.execute(
            "INSERT INTO ground_truth "
            "(pattern_id, error_example, target_surface, label, source, created_at) "
            "VALUES (1, 'example', 'skill', ?, 'seed', '2026-01-01T00:00:00Z')",
            (label,),
        )

    def test_ground_truth_label_check(self, conn):
        """CHECK constraint rejects invalid label values."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_example, target_surface, label, source, created_at) "
                "VALUES (1, 'err', 'skill', 'INVALID_LABEL', 'seed', '2026-01-01T00:00:00Z')"
            )

    VALID_SOURCES = ("agent", "seed", "approved", "edited", "rejected")

    @pytest.mark.parametrize("source", VALID_SOURCES)
    def test_ground_truth_source_accepts_valid(self, conn, source):
        conn.execute(
            "INSERT INTO ground_truth "
            "(pattern_id, error_example, target_surface, label, source, created_at) "
            "VALUES (1, 'example', 'skill', 'pending', ?, '2026-01-01T00:00:00Z')",
            (source,),
        )

    def test_ground_truth_source_check(self, conn):
        """CHECK constraint rejects invalid source values."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_example, target_surface, label, source, created_at) "
                "VALUES (1, 'err', 'skill', 'pending', 'INVALID_SOURCE', '2026-01-01T00:00:00Z')"
            )


class TestOptimizedModulesTable:
    """optimized_modules table must exist for storing DSPy module checkpoints."""

    def test_optimized_modules_table_exists(self, conn):
        tables = _table_names(conn)
        assert "optimized_modules" in tables, (
            f"Missing 'optimized_modules' table. Found: {sorted(tables)}"
        )


class TestSuggestionsNewColumns:
    """suggestions table must have new columns for DSPy integration."""

    def test_suggestions_target_surface_column(self, conn):
        columns = _column_info(conn, "suggestions")
        col_names = {c["name"] for c in columns}
        assert "target_surface" in col_names, (
            f"suggestions table missing 'target_surface' column. Has: {sorted(col_names)}"
        )

    def test_suggestions_reasoning_trace_column(self, conn):
        columns = _column_info(conn, "suggestions")
        col_names = {c["name"] for c in columns}
        assert "reasoning_trace" in col_names, (
            f"suggestions table missing 'reasoning_trace' column. Has: {sorted(col_names)}"
        )
