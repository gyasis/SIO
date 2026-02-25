"""Unit tests for sio.adapters.claude_code.artifact_writer — T047 [US4].

Tests diff generation, file writing, and git commit operations
for optimization artifact output.
These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

import pytest

from sio.adapters.claude_code.artifact_writer import (
    generate_diff,
    write_optimization,
    commit_artifact,
)


class TestGenerateDiff:
    """generate_diff(before, after) produces a unified diff string."""

    def test_diff_contains_before_and_after(self):
        """Diff output includes both before and after content markers."""
        before = "# Old prompt\nDo the thing."
        after = "# Improved prompt\nDo the thing better with context."

        diff = generate_diff(before, after)

        assert "---" in diff, "Diff should contain --- marker for before"
        assert "+++" in diff, "Diff should contain +++ marker for after"

    def test_diff_shows_removed_lines(self):
        """Lines only in 'before' appear as removals (prefixed with -)."""
        before = "line1\nold_line\nline3"
        after = "line1\nnew_line\nline3"

        diff = generate_diff(before, after)

        assert "-old_line" in diff or "- old_line" in diff

    def test_diff_shows_added_lines(self):
        """Lines only in 'after' appear as additions (prefixed with +)."""
        before = "line1\nold_line\nline3"
        after = "line1\nnew_line\nline3"

        diff = generate_diff(before, after)

        assert "+new_line" in diff or "+ new_line" in diff

    def test_identical_content_produces_empty_diff(self):
        """When before == after, diff should be empty or indicate no changes."""
        content = "no changes here"

        diff = generate_diff(content, content)

        # Either empty string or a diff with no +/- change lines
        has_changes = any(
            line.startswith("+") or line.startswith("-")
            for line in diff.splitlines()
            if not line.startswith("---") and not line.startswith("+++")
        )
        assert not has_changes, "Identical content should produce no change lines"

    def test_diff_is_string(self):
        """Return type is always str."""
        diff = generate_diff("a", "b")
        assert isinstance(diff, str)


class TestWriteOptimization:
    """write_optimization(path, diff_content) creates a file with the diff."""

    def test_creates_file_with_diff_content(self, tmp_path):
        """Output file contains the full diff content."""
        output_file = tmp_path / "optimization.diff"
        diff_content = "--- before\n+++ after\n-old\n+new"

        write_optimization(str(output_file), diff_content)

        assert output_file.exists()
        assert output_file.read_text() == diff_content

    def test_creates_parent_directories(self, tmp_path):
        """Intermediate directories are created if they do not exist."""
        output_file = tmp_path / "deep" / "nested" / "dir" / "optimization.diff"
        diff_content = "--- a\n+++ b\n"

        write_optimization(str(output_file), diff_content)

        assert output_file.exists()

    def test_overwrites_existing_file(self, tmp_path):
        """Existing file at path is replaced with new content."""
        output_file = tmp_path / "optimization.diff"
        output_file.write_text("old content")

        new_content = "--- updated\n+++ updated\n"
        write_optimization(str(output_file), new_content)

        assert output_file.read_text() == new_content

    def test_returns_path(self, tmp_path):
        """Returns the path to the written file."""
        output_file = tmp_path / "optimization.diff"

        result = write_optimization(str(output_file), "diff content")

        assert result == str(output_file) or result == Path(output_file)


class TestCommitArtifact:
    """commit_artifact(path, message) runs git add + git commit via subprocess."""

    @patch("sio.adapters.claude_code.artifact_writer.subprocess.run")
    def test_calls_git_add_and_commit(self, mock_run, tmp_path):
        """Runs git add followed by git commit with the given message."""
        artifact_path = str(tmp_path / "optimization.diff")
        message = "SIO: optimize Read skill (bootstrap, score=0.82)"

        mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": ""})()

        commit_artifact(artifact_path, message)

        # Should call git add and git commit
        assert mock_run.call_count >= 2
        add_call = mock_run.call_args_list[0]
        commit_call = mock_run.call_args_list[1]

        # Verify git add includes the artifact path
        add_cmd = add_call[0][0] if add_call[0] else add_call[1].get("args", [])
        assert "add" in add_cmd
        assert artifact_path in add_cmd

        # Verify git commit includes the message
        commit_cmd = commit_call[0][0] if commit_call[0] else commit_call[1].get("args", [])
        assert "commit" in commit_cmd
        assert message in commit_cmd or any(message in str(a) for a in commit_cmd)

    @patch("sio.adapters.claude_code.artifact_writer.subprocess.run")
    def test_commit_message_is_descriptive(self, mock_run, tmp_path):
        """Commit message includes skill name and optimizer info."""
        artifact_path = str(tmp_path / "optimization.diff")
        message = "SIO: optimize Read skill (miprov2, score=0.75)"

        mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": ""})()

        commit_artifact(artifact_path, message)

        commit_call = mock_run.call_args_list[-1]
        commit_cmd = commit_call[0][0] if commit_call[0] else commit_call[1].get("args", [])
        full_cmd = " ".join(str(a) for a in commit_cmd)

        assert "SIO" in full_cmd
        assert "Read" in full_cmd or "optimize" in full_cmd

    @patch("sio.adapters.claude_code.artifact_writer.subprocess.run")
    def test_raises_on_git_failure(self, mock_run, tmp_path):
        """Raises an error if git command exits with non-zero status."""
        artifact_path = str(tmp_path / "optimization.diff")

        mock_run.return_value = type(
            "Result", (), {"returncode": 1, "stdout": "", "stderr": "fatal: not a git repo"}
        )()

        with pytest.raises(Exception):
            commit_artifact(artifact_path, "SIO: test commit")
