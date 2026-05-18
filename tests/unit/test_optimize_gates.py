"""Regression tests for the two optimizer gates shipped 2026-05-16/18:

1. Tier 5 ladder gate (Constitution XIV proposed) — refuses
   `sio optimize --optimizer gepa --trainset-file <X>` when no prior
   MIPROv2 run exists for the same module+dataset. `--skip-ladder`
   overrides.
2. MIPROv2 data-size gate — refuses `sio optimize --optimizer mipro`
   when `valset_size < max(25, trainset_size * 0.2)`. `--skip-data-gate`
   overrides.

Both gates fire BEFORE the LLM is invoked, so all tests use `--dry-run`
where the gate is supposed to pass, and assert SystemExit codes (2 for
ladder, 3 for data-size) where it's supposed to refuse.

PRD: ~/dev/prd/scratch/sio_optimizer_ladder_2026-05-16.md
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from sio.cli.main import cli
from sio.core.datasets.registry import ensure_schema, hash_file
from sio.core.db.bootstrap import ensure_canonical_db_ready


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test SQLite DB with full SIO schema. SIO_DB_PATH is pointed at it.

    The ladder gate in main.py hardcodes ``os.path.expanduser("~/.sio/sio.db")``
    (line 4579) and `find_by_hash` reads SIO_DB_PATH but also defaults via
    ``Path.home()``. To make both paths resolve to the tmp DB, we redirect
    HOME to tmp_path and place the DB at the canonical ``~/.sio/sio.db``
    location relative to that home.
    """
    home = tmp_path / "home"
    sio_dir = home / ".sio"
    sio_dir.mkdir(parents=True)
    db = sio_dir / "sio.db"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SIO_DB_PATH", str(db))
    ensure_canonical_db_ready(db)
    ensure_schema(str(db))  # trainsets table + optimized_modules.trainset_id
    return db


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def trainset_file(tmp_path: Path) -> Path:
    """Minimal JSONL trainset for hashing — content doesn't matter, only sha."""
    p = tmp_path / "trainset.jsonl"
    p.write_text('{"input": "x", "expected": "y"}\n')
    return p


def _register_trainset(db: Path, jsonl: Path, slug: str = "test-set") -> int:
    """Insert a trainsets row for `jsonl` and return its id."""
    sha = hash_file(jsonl)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO trainsets (slug, content_sha256, row_count, stored_path, "
            "created_at, description, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (slug, sha, 1, str(jsonl), now, "test fixture", "test"),
        )
        return cur.lastrowid


def _insert_mipro_run(db: Path, module_type: str, trainset_id: int, score: float = 0.7) -> None:
    """Seed an optimized_modules row representing a successful MIPROv2 run."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO optimized_modules (module_type, optimizer_used, "
            "optimizer_name, file_path, training_count, score, is_active, "
            "created_at, trainset_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (module_type, "mipro", "mipro", "/tmp/fake.json", 80, score, 1, now, trainset_id),
        )


# ---------------------------------------------------------------------------
# MIPROv2 data-size gate (exit code 3)
# ---------------------------------------------------------------------------


class TestMiproDataSizeGate:
    def test_refuses_below_threshold(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "mipro",
                "--trainset-file", str(trainset_file),
                "--trainset-size", "20",
                "--valset-size", "5",
                "--dry-run",
            ],
        )
        assert result.exit_code == 3, result.output
        assert "DATA-SIZE VIOLATION" in result.output

    def test_passes_at_threshold(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        # trainset 80 → min_valset = max(25, 16) = 25; valset 25 just meets.
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "mipro",
                "--trainset-file", str(trainset_file),
                "--trainset-size", "80",
                "--valset-size", "25",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "DATA-SIZE VIOLATION" not in result.output

    def test_skip_data_gate_overrides(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "mipro",
                "--trainset-file", str(trainset_file),
                "--trainset-size", "20",
                "--valset-size", "5",
                "--skip-data-gate",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "DATA-SIZE VIOLATION" not in result.output
        assert "skip-data-gate" in result.output

    def test_does_not_apply_to_bootstrap(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "bootstrap",
                "--trainset-file", str(trainset_file),
                "--trainset-size", "20",
                "--valset-size", "5",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "DATA-SIZE VIOLATION" not in result.output

    def test_does_not_apply_to_gepa(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        # --skip-ladder avoids the ladder gate so we isolate the data-size gate
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "gepa",
                "--trainset-file", str(trainset_file),
                "--trainset-size", "20",
                "--valset-size", "5",
                "--skip-ladder",
                "--dry-run",
            ],
        )
        assert "DATA-SIZE VIOLATION" not in result.output
        # exit code 3 means the data-size gate fired, which it shouldn't for gepa
        assert result.exit_code != 3, result.output


# ---------------------------------------------------------------------------
# GEPA ladder gate (exit code 2)
# ---------------------------------------------------------------------------


class TestGepaLadderGate:
    def test_refuses_gepa_without_prior_mipro(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        _register_trainset(tmp_db, trainset_file)
        # NOTE: no MIPROv2 row inserted
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "gepa",
                "--trainset-file", str(trainset_file),
                "--dry-run",
            ],
        )
        assert result.exit_code == 2, result.output
        assert "LADDER VIOLATION" in result.output

    def test_allows_gepa_with_prior_mipro(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        ts_id = _register_trainset(tmp_db, trainset_file)
        _insert_mipro_run(tmp_db, module_type="suggestions", trainset_id=ts_id, score=0.7)
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--module", "suggestions",
                "--optimizer", "gepa",
                "--trainset-file", str(trainset_file),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ladder-gate: ok" in result.output
        assert "LADDER VIOLATION" not in result.output

    def test_skip_ladder_overrides_refuse(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        _register_trainset(tmp_db, trainset_file)
        # No prior MIPRO row — but --skip-ladder bypasses the gate
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "gepa",
                "--trainset-file", str(trainset_file),
                "--skip-ladder",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "LADDER VIOLATION" not in result.output
        assert "skip-ladder" in result.output

    def test_no_op_without_trainset_file(
        self, runner: CliRunner, tmp_db: Path
    ) -> None:
        # No --trainset-file → ladder gate can't enforce against live ground_truth
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "gepa",
                "--dry-run",
            ],
        )
        assert result.exit_code != 2, result.output
        assert "LADDER VIOLATION" not in result.output

    def test_no_op_for_unregistered_trainset(
        self, runner: CliRunner, tmp_db: Path, trainset_file: Path
    ) -> None:
        # trainset_file exists on disk but no trainsets row → ladder check skipped
        result = runner.invoke(
            cli,
            [
                "optimize",
                "--optimizer", "gepa",
                "--trainset-file", str(trainset_file),
                "--dry-run",
            ],
        )
        assert result.exit_code != 2, result.output
        assert "LADDER VIOLATION" not in result.output
        assert "unregistered" in result.output
