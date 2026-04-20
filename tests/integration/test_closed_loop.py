"""T042 [US1] — Failing integration tests for the optimizer closed loop.

Tests the golden thread:
  gold_standards (5 rows) → run_optimize(gepa) → optimized_modules row + artifact file

Run to confirm RED before T043:
    uv run pytest tests/integration/test_closed_loop.py -v

Uses monkeypatching to avoid real API calls while keeping the GEPA code path real.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_sio_db(tmp_path):
    """File-based SIO DB with init_db schema."""
    from sio.core.db.schema import init_db  # noqa: PLC0415

    db_path = tmp_path / "sio.db"
    conn = init_db(str(db_path))

    # Add columns needed by run_optimize (from 004 migration)
    for table, col_def in [
        ("gold_standards", "promoted_by TEXT DEFAULT 'auto'"),
        ("gold_standards", "dspy_example_json TEXT"),
        ("optimized_modules", "optimizer_name TEXT"),
        ("optimized_modules", "metric_name TEXT"),
        ("optimized_modules", "trainset_size INTEGER"),
        ("optimized_modules", "valset_size INTEGER"),
        ("optimized_modules", "score REAL"),
        ("optimized_modules", "reflection_lm TEXT"),
        ("optimized_modules", "task_lm TEXT"),
        ("optimized_modules", "artifact_path TEXT"),
        ("optimized_modules", "module_name TEXT"),
        ("optimized_modules", "active INTEGER DEFAULT 1"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass

    return conn, str(db_path)


def _seed_gold_standards(conn, db_path: str, n: int = 5) -> None:
    """Seed N valid gold_standards rows with dspy_example_json."""
    now = datetime.now(timezone.utc).isoformat()

    for i in range(n):
        # Insert a behavior_invocations row first (FK)
        cur = conn.execute(
            """
            INSERT INTO behavior_invocations
                (session_id, timestamp, platform, user_message, behavior_type,
                 actual_action, user_satisfied, correct_outcome, activated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"seed-session-{i}",
                now,
                "claude-code",
                f"Improve rule for pattern {i}",
                "skill",
                "suggestion_generator",
                1,
                1,
                1,
            ),
        )
        invocation_id = cur.lastrowid

        example_json = json.dumps({
            "inputs": ["pattern_description", "example_errors", "project_context"],
            "data": {
                "pattern_description": f"Pattern {i}: repeated tool failure",
                "example_errors": [f"Error {i}.{j}" for j in range(3)],
                "project_context": "SIO self-improving agent",
                "rule_title": f"Rule {i}: Prevent tool failure",
                "rule_body": f"Never call the tool without checking param {i}.",
                "rule_rationale": f"This prevents pattern {i} from recurring.",
            },
        })

        conn.execute(
            """
            INSERT INTO gold_standards
                (invocation_id, platform, skill_name, user_message,
                 expected_action, expected_outcome, created_at,
                 promoted_by, dspy_example_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation_id,
                "claude-code",
                "suggestion_generator",
                f"Improve rule for pattern {i}",
                "suggestion_generator",
                "1",
                now,
                "auto",
                example_json,
            ),
        )

    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClosedLoop:
    """run_optimize golden thread: gold_standards → artifact → optimized_modules."""

    def test_run_optimize_returns_expected_keys(
        self, tmp_sio_db, tmp_path, monkeypatch
    ):
        """run_optimize returns dict with artifact, score, optimizer keys."""
        conn, db_path = tmp_sio_db
        _seed_gold_standards(conn, db_path, n=5)

        # Override SIO_HOME to use tmp_path
        monkeypatch.setenv("SIO_DB_PATH", db_path)
        monkeypatch.setenv("SIO_HOME", str(tmp_path))

        # Patch dspy to avoid real API calls
        import dspy  # noqa: PLC0415

        monkeypatch.setattr(
            dspy, "configure", lambda **kwargs: None, raising=False
        )

        from sio.core.dspy.optimizer import run_optimize  # noqa: PLC0415

        result = run_optimize(
            module_name="suggestion_generator",
            optimizer_name="gepa",
            trainset_size=5,
            valset_size=2,
        )

        assert isinstance(result, dict), "run_optimize must return a dict"
        assert "artifact" in result, "result must contain 'artifact' key"
        assert "score" in result, "result must contain 'score' key"
        assert "optimizer" in result, "result must contain 'optimizer' key"
        assert result["optimizer"] == "gepa"

    def test_run_optimize_creates_artifact_file(
        self, tmp_sio_db, tmp_path, monkeypatch
    ):
        """run_optimize creates a non-empty JSON artifact file."""
        conn, db_path = tmp_sio_db
        _seed_gold_standards(conn, db_path, n=5)

        monkeypatch.setenv("SIO_DB_PATH", db_path)
        monkeypatch.setenv("SIO_HOME", str(tmp_path))

        import dspy  # noqa: PLC0415

        monkeypatch.setattr(dspy, "configure", lambda **kwargs: None, raising=False)

        from sio.core.dspy.optimizer import run_optimize  # noqa: PLC0415

        result = run_optimize(
            module_name="suggestion_generator",
            optimizer_name="gepa",
            trainset_size=5,
            valset_size=2,
        )

        artifact_path = Path(result["artifact"])
        assert artifact_path.exists(), (
            f"Artifact file must exist at {artifact_path}"
        )
        assert artifact_path.stat().st_size > 0, "Artifact file must not be empty"
        parsed = json.loads(artifact_path.read_text())
        assert isinstance(parsed, dict), "Artifact must be valid JSON dict"

    def test_run_optimize_inserts_optimized_modules_row(
        self, tmp_sio_db, tmp_path, monkeypatch
    ):
        """run_optimize inserts a row into optimized_modules with active=1."""
        conn, db_path = tmp_sio_db
        _seed_gold_standards(conn, db_path, n=5)

        monkeypatch.setenv("SIO_DB_PATH", db_path)
        monkeypatch.setenv("SIO_HOME", str(tmp_path))

        import dspy  # noqa: PLC0415

        monkeypatch.setattr(dspy, "configure", lambda **kwargs: None, raising=False)

        from sio.core.dspy.optimizer import run_optimize  # noqa: PLC0415

        run_optimize(
            module_name="suggestion_generator",
            optimizer_name="gepa",
            trainset_size=5,
            valset_size=2,
        )

        # Re-read from DB
        check_conn = sqlite3.connect(db_path)
        check_conn.row_factory = sqlite3.Row
        rows = check_conn.execute(
            "SELECT * FROM optimized_modules"
        ).fetchall()
        check_conn.close()

        assert len(rows) >= 1, "optimized_modules must have at least 1 row after optimize"
        active_rows = [r for r in rows if r["is_active"] == 1 or r["active"] == 1]
        assert len(active_rows) >= 1, "At least 1 row must be active"

        # Active row must have artifact_path pointing to existing file
        active_row = active_rows[0]
        artifact_col = (
            active_row["artifact_path"]
            if "artifact_path" in active_row.keys()
            else None
        )
        assert artifact_col is not None, "artifact_path column must be populated"
        assert Path(artifact_col).exists(), (
            f"artifact_path must point to existing file: {artifact_col}"
        )

    def test_run_optimize_second_call_transitions_active(
        self, tmp_sio_db, tmp_path, monkeypatch
    ):
        """Second run_optimize: old active row → active=0, new row → active=1."""
        conn, db_path = tmp_sio_db
        _seed_gold_standards(conn, db_path, n=5)

        monkeypatch.setenv("SIO_DB_PATH", db_path)
        monkeypatch.setenv("SIO_HOME", str(tmp_path))

        import dspy  # noqa: PLC0415

        monkeypatch.setattr(dspy, "configure", lambda **kwargs: None, raising=False)

        from sio.core.dspy.optimizer import run_optimize  # noqa: PLC0415

        run_optimize(
            module_name="suggestion_generator",
            optimizer_name="gepa",
            trainset_size=5,
            valset_size=2,
        )
        run_optimize(
            module_name="suggestion_generator",
            optimizer_name="gepa",
            trainset_size=5,
            valset_size=2,
        )

        check_conn = sqlite3.connect(db_path)
        check_conn.row_factory = sqlite3.Row
        rows = check_conn.execute(
            "SELECT * FROM optimized_modules"
        ).fetchall()
        check_conn.close()

        assert len(rows) == 2, (
            f"Expected 2 rows after two optimize calls, got {len(rows)}"
        )
        # Exactly one active
        active_count = sum(
            1 for r in rows
            if r["is_active"] == 1 or (
                "active" in r.keys() and r["active"] == 1
            )
        )
        assert active_count == 1, (
            f"Expected exactly 1 active row, got {active_count}"
        )

    def test_run_optimize_raises_insufficient_data(
        self, tmp_sio_db, tmp_path, monkeypatch
    ):
        """run_optimize raises InsufficientData when gold_standards has < 5 rows."""
        conn, db_path = tmp_sio_db
        # Seed only 2 rows — below the minimum threshold
        _seed_gold_standards(conn, db_path, n=2)

        monkeypatch.setenv("SIO_DB_PATH", db_path)
        monkeypatch.setenv("SIO_HOME", str(tmp_path))

        from sio.core.dspy.optimizer import InsufficientData, run_optimize  # noqa: PLC0415

        with pytest.raises(InsufficientData):
            run_optimize(
                module_name="suggestion_generator",
                optimizer_name="gepa",
                trainset_size=5,
                valset_size=2,
            )
