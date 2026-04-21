"""Artifact writer — writes optimization diffs to config files."""

from __future__ import annotations

import difflib
import os
import subprocess


def generate_diff(before: str, after: str) -> str:
    """Generate a unified diff string from before/after content."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    diff_lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="before",
        tofile="after",
    )
    return "".join(diff_lines)


def write_optimization(path: str, content: str) -> str:
    """Write optimization content to a file.

    Creates parent directories if needed. Overwrites existing file.

    Args:
        path: Target file path.
        content: Content to write.

    Returns:
        The path that was written to.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w") as f:
        f.write(content)

    return path


def commit_artifact(path: str, message: str) -> None:
    """Commit an artifact file to git.

    Runs git add + git commit. Raises on failure.

    Args:
        path: Path to the file to commit.
        message: Git commit message.

    Raises:
        RuntimeError: If git command fails.
    """
    add_result = subprocess.run(
        ["git", "add", path],
        capture_output=True,
        text=True,
    )
    if add_result.returncode != 0:
        raise RuntimeError(f"git add failed: {add_result.stderr}")

    commit_result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True,
        text=True,
    )
    if commit_result.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit_result.stderr}")
