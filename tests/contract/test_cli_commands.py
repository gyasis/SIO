"""Contract tests for SIO CLI health commands — T067 [US6].

Tests the `sio health` CLI command surface using Click's CliRunner.
These tests are expected to FAIL until the health command is implemented in cli/main.py.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from sio.cli.main import cli


@pytest.fixture
def runner():
    """Provide a Click CliRunner for invoking CLI commands."""
    return CliRunner()


class TestHealthCommand:
    """Tests for `sio health` CLI command."""

    def test_sio_health_exit_code_0(self, runner):
        """The health command should exit with code 0 on success."""
        result = runner.invoke(cli, ["health"])
        assert result.exit_code == 0, (
            f"Expected exit code 0, got {result.exit_code}. Output: {result.output}"
        )

    def test_health_format_json(self, runner):
        """--format json should produce valid JSON output."""
        result = runner.invoke(cli, ["health", "--format", "json"])
        assert result.exit_code == 0, (
            f"Expected exit code 0, got {result.exit_code}. Output: {result.output}"
        )
        # Output must be parseable as JSON
        parsed = json.loads(result.output)
        assert isinstance(parsed, (list, dict))

    def test_health_skill_filter(self, runner):
        """--skill Read should filter output to only the Read skill."""
        result = runner.invoke(cli, ["health", "--skill", "Read"])
        assert result.exit_code == 0, (
            f"Expected exit code 0, got {result.exit_code}. Output: {result.output}"
        )
        # Output should reference the filtered skill
        assert "Read" in result.output or result.output.strip() == ""

    def test_health_unknown_skill(self, runner):
        """Filtering by a nonexistent skill should still exit 0 (empty results)."""
        result = runner.invoke(cli, ["health", "--skill", "NonexistentSkill"])
        assert result.exit_code == 0

    def test_sio_version(self, runner):
        """The --version flag should work on the root CLI group."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output
