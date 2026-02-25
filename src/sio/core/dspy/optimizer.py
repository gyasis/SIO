"""DSPy optimizer wrapper — runs prompt optimization with quality gates."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


class OptimizationError(Exception):
    """Raised when optimization fails after passing quality gates."""


@dataclass
class OptimizationResult:
    """Result of quality gate check."""

    passed: bool
    reason: str
    example_count: int
    failure_count: int
    session_count: int


# --- Quality gates ---

_MIN_EXAMPLES = 10
_MIN_FAILURES = 5
_MIN_SESSIONS = 3

_VALID_OPTIMIZERS = ("gepa", "miprov2", "bootstrap")


def check_quality_gates(
    conn: sqlite3.Connection,
    skill: str,
    platform: str = "claude-code",
    min_examples: int = _MIN_EXAMPLES,
    min_failures: int = _MIN_FAILURES,
    min_sessions: int = _MIN_SESSIONS,
) -> OptimizationResult:
    """Check quality gates for optimization eligibility.

    Returns an OptimizationResult with pass/fail status.
    """
    from sio.core.db.queries import get_labeled_for_optimizer

    examples = get_labeled_for_optimizer(
        conn, skill, platform, min_examples=0,
    )

    failures = [
        e for e in examples
        if e.get("user_satisfied") == 0 or e.get("correct_outcome") == 0
    ]
    failing_sessions = {e["session_id"] for e in failures}
    all_sessions = {e["session_id"] for e in examples}

    if len(examples) < min_examples:
        return OptimizationResult(
            passed=False,
            reason=f"Need {min_examples}+ labeled examples, "
                   f"got {len(examples)}",
            example_count=len(examples),
            failure_count=len(failures),
            session_count=len(all_sessions),
        )

    if len(failures) < min_failures:
        return OptimizationResult(
            passed=False,
            reason=f"Need {min_failures}+ failure examples, "
                   f"got {len(failures)}",
            example_count=len(examples),
            failure_count=len(failures),
            session_count=len(all_sessions),
        )

    if len(failing_sessions) < min_sessions:
        return OptimizationResult(
            passed=False,
            reason=f"Need failures across {min_sessions}+ sessions, "
                   f"got {len(failing_sessions)}",
            example_count=len(examples),
            failure_count=len(failures),
            session_count=len(all_sessions),
        )

    return OptimizationResult(
        passed=True,
        reason="",
        example_count=len(examples),
        failure_count=len(failures),
        session_count=len(all_sessions),
    )


def _apply_recency_weighting(examples: list[dict]) -> list[dict]:
    """Weight examples by recency — newer get higher weight."""
    if not examples:
        return examples

    sorted_ex = sorted(
        examples, key=lambda e: e.get("timestamp", ""),
    )
    n = len(sorted_ex)
    for i, ex in enumerate(sorted_ex):
        ex["weight"] = 0.5 + 0.5 * (i / max(n - 1, 1))

    return sorted_ex


def _compute_satisfaction_rate(examples: list[dict]) -> float | None:
    """Compute satisfaction rate from examples."""
    labeled = [
        e for e in examples if e.get("user_satisfied") is not None
    ]
    if not labeled:
        return 0.0
    satisfied = sum(1 for e in labeled if e["user_satisfied"] == 1)
    return satisfied / len(labeled)


def _run_dspy_optimization(
    dataset: list[dict],
    skill_name: str,
    optimizer: str,
) -> dict:
    """Run the DSPy optimizer and return result.

    This is the integration point for DSPy. In V0.1, this
    produces a simple prompt diff based on failure analysis.

    Returns:
        dict with 'proposed_diff' and 'score' keys.
    """
    failures = [e for e in dataset if e.get("user_satisfied") == 0]
    successes = [e for e in dataset if e.get("user_satisfied") == 1]

    failure_actions: dict[str, int] = {}
    for f in failures:
        action = f.get("actual_action", "unknown")
        failure_actions[action] = failure_actions.get(action, 0) + 1

    lines = [f"# Optimization for skill: {skill_name}"]
    lines.append(f"# Optimizer: {optimizer}")
    lines.append(
        f"# Examples: {len(dataset)} "
        f"({len(successes)} success, {len(failures)} failure)"
    )
    lines.append("")
    lines.append("## Proposed changes:")
    lines.append("")

    for action, count in sorted(
        failure_actions.items(), key=lambda x: -x[1],
    ):
        lines.append(
            f"- Action '{action}' failed {count} times"
        )

    diff = "\n".join(lines)
    score = len(successes) / max(len(dataset), 1)
    return {"proposed_diff": diff, "score": score}


def optimize(
    conn: sqlite3.Connection,
    skill_name: str,
    platform: str = "claude-code",
    optimizer: str = "gepa",
    dry_run: bool = False,
) -> dict:
    """Run prompt optimization for a skill.

    Returns a dict with 'status' and optional 'reason', 'diff',
    'optimization_id' keys.

    Raises:
        ValueError: If optimizer name is invalid.
        OptimizationError/RuntimeError: If DSPy fails after gates pass.
    """
    if optimizer not in _VALID_OPTIMIZERS:
        raise ValueError(
            f"Invalid optimizer '{optimizer}'. "
            f"Choose from: {_VALID_OPTIMIZERS}"
        )

    # Quality gates
    from sio.core.db.queries import get_labeled_for_optimizer

    examples = get_labeled_for_optimizer(
        conn, skill_name, platform, min_examples=0,
    )

    failures = [
        e for e in examples
        if e.get("user_satisfied") == 0 or e.get("correct_outcome") == 0
    ]
    failing_sessions = {e["session_id"] for e in failures}

    if len(examples) < _MIN_EXAMPLES:
        return {
            "status": "error",
            "reason": f"Need {_MIN_EXAMPLES}+ labeled examples, "
                      f"got {len(examples)}",
        }

    if len(failures) < _MIN_FAILURES:
        return {
            "status": "error",
            "reason": f"Need {_MIN_FAILURES}+ failure examples, "
                      f"got {len(failures)}",
        }

    if len(failing_sessions) < _MIN_SESSIONS:
        return {
            "status": "error",
            "reason": f"Need failures across {_MIN_SESSIONS}+ sessions, "
                      f"got {len(failing_sessions)}",
        }

    # Recency weighting (FR-027)
    examples = _apply_recency_weighting(examples)
    before_rate = _compute_satisfaction_rate(examples)

    # Run optimization — propagate exceptions for atomic rollback
    result = _run_dspy_optimization(examples, skill_name, optimizer)
    proposed_diff = result["proposed_diff"]

    if dry_run:
        return {
            "status": "pending",
            "diff": proposed_diff,
            "optimization_id": None,
            "before_satisfaction": before_rate,
        }

    # Arena validation (FR-010, FR-011, FR-012)
    from sio.core.arena.regression import run_arena

    arena_result = run_arena(conn, skill_name, proposed_diff)
    arena_passed = 1 if arena_result["passed"] else 0
    drift_score = arena_result.get("drift_score")

    # Record OptimizationRun
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        "INSERT INTO optimization_runs "
        "(platform, skill_name, optimizer, example_count, "
        "before_satisfaction, proposed_diff, status, "
        "arena_passed, drift_score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
        (
            platform, skill_name, optimizer,
            len(examples), before_rate,
            proposed_diff, arena_passed, drift_score, now,
        ),
    )
    conn.commit()

    return {
        "status": "pending",
        "diff": proposed_diff,
        "optimization_id": cursor.lastrowid,
        "before_satisfaction": before_rate,
    }


def run_optimization(
    conn: sqlite3.Connection,
    skill: str,
    platform: str = "claude-code",
    optimizer: str = "gepa",
) -> dict:
    """Public alias for optimize() with 'skill' kwarg.

    Integration test API.
    """
    return optimize(conn, skill_name=skill, platform=platform,
                    optimizer=optimizer)
