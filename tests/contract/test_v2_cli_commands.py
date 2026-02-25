"""CLI contract tests — verify all v2 CLI commands respond correctly."""


import pytest
from click.testing import CliRunner

from sio.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


# =========================================================================
# TestCLIHelp
# =========================================================================

class TestCLIHelp:
    """All v2 commands show help without error."""

    def test_main_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "SIO" in result.output

    def test_mine_help(self, runner):
        result = runner.invoke(cli, ["mine", "--help"])
        assert result.exit_code == 0
        assert "--since" in result.output

    def test_patterns_help(self, runner):
        result = runner.invoke(cli, ["patterns", "--help"])
        assert result.exit_code == 0

    def test_datasets_help(self, runner):
        result = runner.invoke(cli, ["datasets", "--help"])
        assert result.exit_code == 0

    def test_suggest_review_help(self, runner):
        result = runner.invoke(cli, ["suggest-review", "--help"])
        assert result.exit_code == 0

    def test_approve_help(self, runner):
        result = runner.invoke(cli, ["approve", "--help"])
        assert result.exit_code == 0

    def test_reject_help(self, runner):
        result = runner.invoke(cli, ["reject", "--help"])
        assert result.exit_code == 0

    def test_rollback_help(self, runner):
        result = runner.invoke(cli, ["rollback", "--help"])
        assert result.exit_code == 0

    def test_schedule_help(self, runner):
        result = runner.invoke(cli, ["schedule", "--help"])
        assert result.exit_code == 0

    def test_status_help(self, runner):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0

    def test_schedule_install_help(self, runner):
        result = runner.invoke(cli, ["schedule", "install", "--help"])
        assert result.exit_code == 0

    def test_schedule_status_help(self, runner):
        result = runner.invoke(cli, ["schedule", "status", "--help"])
        assert result.exit_code == 0


# =========================================================================
# TestCLIStatus
# =========================================================================

class TestCLIStatus:
    """sio status output format."""

    def test_status_no_db(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No SIO database" in result.output or "database" in result.output.lower()

    def test_status_with_db(self, runner, v2_db, tmp_path, monkeypatch):
        # Create a real DB at the expected path
        sio_dir = tmp_path / ".sio"
        sio_dir.mkdir()
        db_path = sio_dir / "sio.db"

        from sio.core.db.schema import init_db
        conn = init_db(str(db_path))
        conn.close()

        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / p.lstrip("~/")))
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "Errors mined" in result.output
        assert "Patterns found" in result.output


# =========================================================================
# TestCLIMine
# =========================================================================

class TestCLIMine:
    """sio mine command contract."""

    def test_mine_requires_since(self, runner):
        result = runner.invoke(cli, ["mine"])
        assert result.exit_code != 0  # --since is required

    def test_mine_no_source_dirs(self, runner, tmp_path, monkeypatch):
        # Point to nonexistent dirs
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(tmp_path / p.lstrip("~/")),
        )
        result = runner.invoke(cli, ["mine", "--since", "1 day"])
        assert result.exit_code == 0
        assert "No source directories" in result.output


# =========================================================================
# TestCLIPatterns
# =========================================================================

class TestCLIPatterns:
    """sio patterns output format."""

    def test_patterns_no_db(self, runner, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "os.path.expanduser",
            lambda p: str(tmp_path / p.lstrip("~/")),
        )
        result = runner.invoke(cli, ["patterns"])
        assert result.exit_code == 0
        assert "No database" in result.output or "No errors" in result.output
