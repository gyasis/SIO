"""T068 [US8] Unit tests for experiment lifecycle management."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sio.core.arena.experiment import (
    _extract_suggestion_id,
    create_experiment,
    promote_experiment,
    rollback_experiment,
    validate_experiment,
)
from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory database with schema initialized."""
    conn = init_db(":memory:")
    # Insert a test suggestion
    conn.execute(
        "INSERT INTO suggestions "
        "(pattern_id, description, confidence, proposed_change, "
        " target_file, change_type, status, created_at) "
        "VALUES (NULL, 'Test rule', 0.9, 'Do X always', "
        " 'CLAUDE.md', 'claude_md_rule', 'pending', '2026-04-01T00:00:00Z')",
    )
    conn.commit()
    yield conn
    conn.close()


def _mock_git_success(*args, **kwargs):
    """Mock subprocess.run for git — always succeeds."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "/fake/repo\n"
    mock.stderr = ""
    return mock


def _mock_git_failure(*args, **kwargs):
    """Mock subprocess.run for git — always fails."""
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = ""
    mock.stderr = "fatal: some error"
    return mock


# ---------------------------------------------------------------------------
# _extract_suggestion_id
# ---------------------------------------------------------------------------


class TestExtractSuggestionId:
    def test_extracts_from_branch_name(self):
        assert _extract_suggestion_id("experiment/sug-15-20260401T1430") == 15

    def test_returns_none_for_invalid(self):
        assert _extract_suggestion_id("main") is None

    def test_returns_none_for_non_numeric(self):
        assert _extract_suggestion_id("experiment/sug-abc-20260401T1430") is None


# ---------------------------------------------------------------------------
# create_experiment
# ---------------------------------------------------------------------------


class TestCreateExperiment:
    @patch("sio.core.arena.experiment.subprocess.run", side_effect=_mock_git_success)
    @patch("sio.core.arena.experiment.os.makedirs")
    @patch("sio.core.arena.experiment.os.path.isdir", return_value=False)
    def test_creates_branch_and_updates_status(
        self,
        mock_isdir,
        mock_makedirs,
        mock_run,
        db,
    ):
        branch = create_experiment(1, db)

        assert branch.startswith("experiment/sug-1-")
        # Verify suggestion status was updated
        row = db.execute(
            "SELECT status FROM suggestions WHERE id = 1",
        ).fetchone()
        assert row["status"] == "experiment"

    @patch("sio.core.arena.experiment.subprocess.run", side_effect=_mock_git_failure)
    def test_raises_on_git_failure(self, mock_run, db):
        with pytest.raises(RuntimeError):
            create_experiment(1, db)


# ---------------------------------------------------------------------------
# validate_experiment
# ---------------------------------------------------------------------------


class TestValidateExperiment:
    def test_all_pass_returns_true(self, db):
        context = {
            "pre": {"error_rate": 0.3, "error_types": ["a"]},
            "post": {"error_rate": 0.1, "error_types": ["a"]},
        }
        result = validate_experiment(
            "experiment/sug-1-20260401",
            db,
            ["error_rate_decreased", "no_new_regressions"],
            context,
        )
        assert result is True

    def test_one_fails_returns_false(self, db):
        context = {
            "pre": {"error_rate": 0.1, "error_types": ["a"]},
            "post": {"error_rate": 0.5, "error_types": ["a", "b"]},
        }
        result = validate_experiment(
            "experiment/sug-1-20260401",
            db,
            ["error_rate_decreased", "no_new_regressions"],
            context,
        )
        assert result is False

    def test_empty_assertions_passes(self, db):
        result = validate_experiment(
            "experiment/sug-1-20260401",
            db,
            [],
            {},
        )
        assert result is True


# ---------------------------------------------------------------------------
# promote_experiment
# ---------------------------------------------------------------------------


class TestPromoteExperiment:
    @patch("sio.core.arena.experiment.subprocess.run", side_effect=_mock_git_success)
    @patch("sio.core.arena.experiment.os.path.isdir", return_value=False)
    def test_sets_pending_approval(self, mock_isdir, mock_run, db):
        # First set status to experiment
        db.execute(
            "UPDATE suggestions SET status = 'experiment' WHERE id = 1",
        )
        db.commit()

        promote_experiment("experiment/sug-1-20260401T1430", db)

        row = db.execute(
            "SELECT status FROM suggestions WHERE id = 1",
        ).fetchone()
        assert row["status"] == "pending_approval"

    @patch("sio.core.arena.experiment.subprocess.run", side_effect=_mock_git_success)
    @patch("sio.core.arena.experiment.os.path.isdir", return_value=True)
    def test_removes_worktree_when_exists(self, mock_isdir, mock_run, db):
        promote_experiment("experiment/sug-1-20260401T1430", db)
        # The worktree remove command should have been called
        calls = [c for c in mock_run.call_args_list if "remove" in str(c)]
        assert len(calls) >= 1


# ---------------------------------------------------------------------------
# rollback_experiment
# ---------------------------------------------------------------------------


class TestRollbackExperiment:
    @patch("sio.core.arena.experiment.subprocess.run", side_effect=_mock_git_success)
    @patch("sio.core.arena.experiment.os.path.isdir", return_value=False)
    def test_marks_suggestion_failed(self, mock_isdir, mock_run, db):
        db.execute(
            "UPDATE suggestions SET status = 'experiment' WHERE id = 1",
        )
        db.commit()

        rollback_experiment("experiment/sug-1-20260401T1430", db)

        row = db.execute(
            "SELECT status FROM suggestions WHERE id = 1",
        ).fetchone()
        assert row["status"] == "failed_experiment"

    @patch("sio.core.arena.experiment.subprocess.run", side_effect=_mock_git_success)
    @patch("sio.core.arena.experiment.os.path.isdir", return_value=True)
    def test_deletes_worktree_and_branch(self, mock_isdir, mock_run, db):
        rollback_experiment("experiment/sug-1-20260401T1430", db)
        # Should call worktree remove and branch -D
        git_args = [str(c) for c in mock_run.call_args_list]
        assert any("remove" in a for a in git_args)
        assert any("-D" in a for a in git_args)
