"""Experiment lifecycle management using git worktrees (FR-039 to FR-041).

Creates isolated experiment branches, validates after N sessions,
and supports promote (with human gate) or rollback.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timezone

from sio.core.arena.assertions import run_assertions

logger = logging.getLogger(__name__)


def _git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a git command via subprocess."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )


def _repo_root() -> str:
    """Detect the git repo root for the current working directory."""
    result = _git("rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise RuntimeError(f"Not inside a git repository: {result.stderr.strip()}")
    return result.stdout.strip()


def create_experiment(
    suggestion_id: int,
    db: sqlite3.Connection,
) -> str:
    """Create a git worktree for an experiment branch.

    The branch is named ``experiment/sug-{id}-{timestamp}``.
    The suggestion status is updated to ``experiment``.

    Args:
        suggestion_id: ID of the suggestion to experiment with.
        db: Open SQLite connection.

    Returns:
        Branch name (e.g. ``experiment/sug-15-20260401T1430``).

    Raises:
        RuntimeError: If git operations fail.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    branch = f"experiment/sug-{suggestion_id}-{ts}"

    repo = _repo_root()
    worktree_dir = os.path.join(repo, ".worktrees", branch.replace("/", "-"))

    # Create the branch from current HEAD
    result = _git("branch", branch, cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create branch {branch}: {result.stderr}")

    # Add worktree
    os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)
    result = _git("worktree", "add", worktree_dir, branch, cwd=repo)
    if result.returncode != 0:
        # Clean up the branch on failure
        _git("branch", "-D", branch, cwd=repo)
        raise RuntimeError(f"Failed to create worktree for {branch}: {result.stderr}")

    # Update suggestion status
    db.execute(
        "UPDATE suggestions SET status = 'experiment' WHERE id = ?",
        (suggestion_id,),
    )
    db.commit()

    logger.info("Created experiment %s at %s", branch, worktree_dir)
    return branch


def validate_experiment(
    branch: str,
    db: sqlite3.Connection,
    assertion_names: list[str],
    context: dict,
) -> bool:
    """Run assertions against an experiment branch.

    Args:
        branch: The experiment branch name.
        db: Open SQLite connection.
        assertion_names: List of assertion names to run.
        context: Context dict for assertions (pre, post, pattern, etc.).

    Returns:
        True if all assertions pass, False otherwise.
    """
    results = run_assertions(assertion_names, context)
    all_passed = all(r.passed for r in results)

    logger.info(
        "Experiment %s validation: %s (%d/%d assertions passed)",
        branch,
        "PASS" if all_passed else "FAIL",
        sum(1 for r in results if r.passed),
        len(results),
    )

    return all_passed


def promote_experiment(
    branch: str,
    db: sqlite3.Connection,
) -> None:
    """Mark an experiment as pending human approval for promotion.

    Does NOT auto-merge.  Sets a ``pending_approval`` flag so the
    human can review and merge manually.

    Args:
        branch: Experiment branch name.
        db: Open SQLite connection.
    """
    # Extract suggestion_id from branch name
    suggestion_id = _extract_suggestion_id(branch)

    if suggestion_id is not None:
        db.execute(
            "UPDATE suggestions SET status = 'pending_approval' WHERE id = ?",
            (suggestion_id,),
        )
        db.commit()

    # Clean up the worktree but keep the branch for merge
    repo = _repo_root()
    worktree_dir = os.path.join(
        repo,
        ".worktrees",
        branch.replace("/", "-"),
    )
    if os.path.isdir(worktree_dir):
        _git("worktree", "remove", worktree_dir, "--force", cwd=repo)

    logger.info(
        "Experiment %s promoted to pending_approval (human gate active)",
        branch,
    )


def rollback_experiment(
    branch: str,
    db: sqlite3.Connection,
) -> None:
    """Rollback an experiment — delete worktree and mark suggestion failed.

    Args:
        branch: Experiment branch name.
        db: Open SQLite connection.
    """
    suggestion_id = _extract_suggestion_id(branch)

    if suggestion_id is not None:
        db.execute(
            "UPDATE suggestions SET status = 'failed_experiment' WHERE id = ?",
            (suggestion_id,),
        )
        db.commit()

    repo = _repo_root()
    worktree_dir = os.path.join(
        repo,
        ".worktrees",
        branch.replace("/", "-"),
    )

    # Remove the worktree
    if os.path.isdir(worktree_dir):
        _git("worktree", "remove", worktree_dir, "--force", cwd=repo)

    # Delete the branch
    _git("branch", "-D", branch, cwd=repo)

    logger.info("Experiment %s rolled back and branch deleted", branch)


def _extract_suggestion_id(branch: str) -> int | None:
    """Extract suggestion ID from branch name like experiment/sug-15-..."""
    parts = branch.split("/")[-1].split("-")
    if len(parts) >= 2 and parts[0] == "sug":
        try:
            return int(parts[1])
        except ValueError:
            pass
    return None
