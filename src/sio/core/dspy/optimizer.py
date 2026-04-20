"""DSPy optimizer wrapper — runs prompt optimization with quality gates.

Includes both the legacy behavior_invocations optimizer (optimize()) and
the new DSPy suggestion optimizer (optimize_suggestions()) that uses
BootstrapFewShot / MIPROv2 on the ground truth corpus.
"""

from __future__ import annotations

import copy
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from sio.core.constants import DEFAULT_PLATFORM

logger = logging.getLogger(__name__)


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


@dataclass
class SuggestionOptimizationResult:
    """Result of DSPy suggestion optimization."""

    status: str  # "success", "error", "dry_run"
    optimizer_used: str
    training_count: int
    metric_before: float | None
    metric_after: float | None
    module_id: int | None  # DB row ID if saved
    message: str


# --- Quality gates ---

_MIN_EXAMPLES = 10
_MIN_FAILURES = 5
_MIN_SESSIONS = 3

_VALID_OPTIMIZERS = ("gepa", "miprov2", "bootstrap", "auto")

# --- Auto-selection thresholds (FR-010) ---
_MIPROV2_THRESHOLD = 50  # examples needed for MIPROv2


def check_quality_gates(
    conn: sqlite3.Connection,
    skill: str,
    platform: str = DEFAULT_PLATFORM,
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
    """Weight examples by recency — newer get higher weight.

    Returns a new list of copied dicts; the input list is not mutated.
    """
    if not examples:
        return list(examples)

    sorted_ex = sorted(
        (copy.deepcopy(e) for e in examples),
        key=lambda e: e.get("timestamp", ""),
    )
    n = len(sorted_ex)
    for i, ex in enumerate(sorted_ex):
        ex["weight"] = 0.5 + 0.5 * (i / max(n - 1, 1))

    return sorted_ex


def _compute_satisfaction_rate(examples: list[dict]) -> float | None:
    """Compute satisfaction rate from examples.

    Returns None when there are no labeled examples to compute from.
    """
    labeled = [
        e for e in examples if e.get("user_satisfied") is not None
    ]
    if not labeled:
        return None
    satisfied = sum(1 for e in labeled if e["user_satisfied"] == 1)
    return satisfied / len(labeled)


# DEPRECATED: Remove in v0.3
def _run_dspy_optimization(
    dataset: list[dict],
    skill_name: str,
    optimizer: str,
) -> dict:
    """Run the DSPy optimizer and return result.

    .. deprecated::
        Legacy behavior_invocations optimizer. Use ``optimize_suggestions()``
        for the real DSPy ground-truth-based implementation.

    This is the integration point for DSPy. In V0.1, this
    produces a simple prompt diff based on failure analysis.

    Returns:
        dict with 'proposed_diff' and 'score' keys.
    """
    import warnings

    warnings.warn(
        "_run_dspy_optimization is deprecated; use optimize_suggestions() instead",
        DeprecationWarning,
        stacklevel=2,
    )
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


# ---------------------------------------------------------------------------
# T059-T061: Real DSPy suggestion optimization
# ---------------------------------------------------------------------------


def _select_optimizer(optimizer: str, example_count: int) -> str:
    """Resolve 'auto' to a concrete optimizer based on example count.

    Args:
        optimizer: Requested optimizer name ('auto', 'bootstrap', 'miprov2').
        example_count: Number of training examples available.

    Returns:
        Concrete optimizer name: 'bootstrap' or 'miprov2'.
    """
    if optimizer == "auto":
        if example_count >= _MIPROV2_THRESHOLD:
            return "miprov2"
        return "bootstrap"
    return optimizer


def _evaluate_metric(
    module,
    corpus: list,
    metric_fn,
) -> float:
    """Evaluate average metric score for a module on a corpus.

    Args:
        module: DSPy module to evaluate.
        corpus: List of dspy.Example objects.
        metric_fn: Metric function (example, pred, trace=None) -> float.

    Returns:
        Average metric score across all examples.
    """
    if not corpus:
        return 0.0

    scores = []
    for example in corpus:
        try:
            pred = module(
                error_examples=example.error_examples,
                error_type=example.error_type,
                pattern_summary=example.pattern_summary,
            )
            score = metric_fn(example, pred, trace=None)
            scores.append(float(score))
        except Exception:
            logger.debug("Metric eval failed for example, scoring 0.0")
            scores.append(0.0)

    return sum(scores) / len(scores) if scores else 0.0


def _run_bootstrap_optimization(
    module,
    corpus: list,
    metric_fn,
    max_bootstrapped_demos: int = 4,
    max_labeled_demos: int = 16,
):
    """Run BootstrapFewShot optimization.

    Args:
        module: DSPy SuggestionModule instance.
        corpus: Training examples (dspy.Example list).
        metric_fn: Quality metric function.
        max_bootstrapped_demos: Max bootstrapped demonstrations.
        max_labeled_demos: Max labeled demonstrations.

    Returns:
        Optimized DSPy module.
    """
    import dspy

    optimizer = dspy.BootstrapFewShot(
        metric=metric_fn,
        max_bootstrapped_demos=max_bootstrapped_demos,
        max_labeled_demos=max_labeled_demos,
    )
    return optimizer.compile(module, trainset=corpus)


def _run_miprov2_optimization(
    module,
    corpus: list,
    metric_fn,
    num_trials: int = 10,
):
    """Run MIPROv2 optimization.

    Args:
        module: DSPy SuggestionModule instance.
        corpus: Training examples (dspy.Example list).
        metric_fn: Quality metric function.
        num_trials: Number of optimization trials.

    Returns:
        Optimized DSPy module.
    """
    import dspy

    optimizer = dspy.MIPROv2(
        metric=metric_fn,
        auto="medium",
    )
    return optimizer.compile(module, trainset=corpus, num_trials=num_trials)


def optimize_suggestions(
    conn: sqlite3.Connection,
    optimizer: str = "auto",
    dry_run: bool = False,
    config=None,
) -> SuggestionOptimizationResult:
    """Run DSPy optimization on the ground truth corpus.

    Loads approved ground truth examples, runs BootstrapFewShot or MIPROv2,
    saves the optimized module to disk, and records it in the DB.

    Args:
        conn: SQLite connection with SIO schema.
        optimizer: Optimizer choice ('auto', 'bootstrap', 'miprov2').
            'auto' selects based on corpus size (FR-010):
            <50 examples -> bootstrap, >=50 -> miprov2.
        dry_run: If True, evaluate metrics but do not save the module.
        config: Optional SIOConfig for LM creation. If None, uses default.

    Returns:
        SuggestionOptimizationResult with status and metrics.

    Raises:
        OptimizationError: If DSPy compilation fails.
    """
    import dspy

    from sio.core.dspy.lm_factory import create_lm
    from sio.core.dspy.metrics import suggestion_quality_metric
    from sio.core.dspy.module_store import save_module
    from sio.core.dspy.modules import SuggestionModule
    from sio.ground_truth.corpus import load_training_corpus

    # Load corpus
    corpus = load_training_corpus(conn)
    if not corpus:
        return SuggestionOptimizationResult(
            status="error",
            optimizer_used=optimizer,
            training_count=0,
            metric_before=None,
            metric_after=None,
            module_id=None,
            message="No positive ground truth examples found. "
                    "Run 'sio ground-truth review' to approve examples first.",
        )

    # Configure LM
    if config is not None:
        lm = create_lm(config)
        if lm is not None:
            dspy.configure(lm=lm)

    # Resolve optimizer
    resolved = _select_optimizer(optimizer, len(corpus))
    logger.info(
        "Optimizing suggestions: optimizer=%s (resolved from '%s'), "
        "corpus_size=%d",
        resolved, optimizer, len(corpus),
    )

    # Create base module and evaluate before score
    base_module = SuggestionModule()
    metric_before = _evaluate_metric(
        base_module, corpus, suggestion_quality_metric,
    )

    # Run optimization
    try:
        if resolved == "miprov2":
            optimized_module = _run_miprov2_optimization(
                SuggestionModule(), corpus, suggestion_quality_metric,
            )
        else:
            optimized_module = _run_bootstrap_optimization(
                SuggestionModule(), corpus, suggestion_quality_metric,
            )
    except Exception as exc:
        raise OptimizationError(
            f"DSPy {resolved} optimization failed: {exc}"
        ) from exc

    # Evaluate after score
    metric_after = _evaluate_metric(
        optimized_module, corpus, suggestion_quality_metric,
    )

    if dry_run:
        return SuggestionOptimizationResult(
            status="dry_run",
            optimizer_used=resolved,
            training_count=len(corpus),
            metric_before=metric_before,
            metric_after=metric_after,
            module_id=None,
            message=f"Dry run complete. Before: {metric_before:.3f}, "
                    f"After: {metric_after:.3f}",
        )

    # Save optimized module
    module_id = save_module(
        conn,
        module=optimized_module,
        module_type="suggestion",
        optimizer_used=resolved,
        training_count=len(corpus),
        metric_before=metric_before,
        metric_after=metric_after,
    )

    return SuggestionOptimizationResult(
        status="success",
        optimizer_used=resolved,
        training_count=len(corpus),
        metric_before=metric_before,
        metric_after=metric_after,
        module_id=module_id,
        message=f"Optimization complete. Before: {metric_before:.3f}, "
                f"After: {metric_after:.3f}. Module saved (ID: {module_id}).",
    )


# ---------------------------------------------------------------------------
# Legacy behavior_invocations optimizer (kept for backward compatibility)
# ---------------------------------------------------------------------------


# DEPRECATED: Remove in v0.3
def optimize(
    conn: sqlite3.Connection,
    skill_name: str,
    platform: str = DEFAULT_PLATFORM,
    optimizer: str = "gepa",
    dry_run: bool = False,
) -> dict:
    """Run prompt optimization for a skill.

    .. deprecated::
        Legacy behavior_invocations optimizer. Use ``optimize_suggestions()``
        for the real DSPy ground-truth-based implementation.

    Returns a dict with 'status' and optional 'reason', 'diff',
    'optimization_id' keys.

    Raises:
        ValueError: If optimizer name is invalid.
        OptimizationError/RuntimeError: If DSPy fails after gates pass.
    """
    import warnings

    warnings.warn(
        "optimize() is deprecated; use optimize_suggestions() instead",
        DeprecationWarning,
        stacklevel=2,
    )
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
    # Use 0.0 for DB storage when no data available
    before_rate_for_db = before_rate if before_rate is not None else 0.0

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
            len(examples), before_rate_for_db,
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


# DEPRECATED: Remove in v0.3
def run_optimization(
    conn: sqlite3.Connection,
    skill: str,
    platform: str = DEFAULT_PLATFORM,
    optimizer: str = "gepa",
) -> dict:
    """Public alias for optimize() with 'skill' kwarg.

    .. deprecated::
        Legacy behavior_invocations optimizer. Use ``optimize_suggestions()``
        for the real DSPy ground-truth-based implementation.

    Integration test API.
    """
    import warnings

    warnings.warn(
        "run_optimization() is deprecated; use optimize_suggestions() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return optimize(conn, skill_name=skill, platform=platform,
                    optimizer=optimizer)
