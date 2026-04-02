"""Binary assertion checks for experiment validation (FR-037, FR-038).

Each assertion returns an AssertionResult with pass/fail, the actual value,
and the threshold it was compared against.  Custom assertions can be passed
as callables in the context dict.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    passed: bool
    name: str
    actual_value: float
    threshold: float
    detail: str = ""


# ---------------------------------------------------------------------------
# Built-in assertions
# ---------------------------------------------------------------------------


def error_rate_decreased(pre: dict, post: dict) -> AssertionResult:
    """Assert that post error rate is lower than pre.

    Args:
        pre: Dict with at least ``error_rate`` (float 0-1).
        post: Same structure, measured after experiment.
    """
    pre_rate = float(pre.get("error_rate", 0.0))
    post_rate = float(post.get("error_rate", 0.0))
    passed = post_rate <= pre_rate
    return AssertionResult(
        passed=passed,
        name="error_rate_decreased",
        actual_value=post_rate,
        threshold=pre_rate,
        detail=f"pre={pre_rate:.4f} post={post_rate:.4f}",
    )


def no_new_regressions(pre: dict, post: dict) -> AssertionResult:
    """Assert that no new error types appeared after the change.

    Expects ``error_types`` key containing a set or list of type strings.
    """
    pre_types = set(pre.get("error_types", []))
    post_types = set(post.get("error_types", []))
    new_types = post_types - pre_types
    passed = len(new_types) == 0
    return AssertionResult(
        passed=passed,
        name="no_new_regressions",
        actual_value=float(len(new_types)),
        threshold=0.0,
        detail=f"new_types={sorted(new_types)}" if new_types else "none",
    )


def confidence_above_threshold(
    pattern: dict, threshold: float = 0.7,
) -> AssertionResult:
    """Assert that the pattern confidence exceeds *threshold*.

    Args:
        pattern: Dict with ``confidence`` or ``rank_score`` key.
        threshold: Minimum acceptable confidence (default 0.7).
    """
    confidence = float(
        pattern.get("confidence", pattern.get("rank_score", 0.0)),
    )
    passed = confidence >= threshold
    return AssertionResult(
        passed=passed,
        name="confidence_above_threshold",
        actual_value=confidence,
        threshold=threshold,
    )


def budget_within_limits(
    file_path: str, config: Any,
) -> AssertionResult:
    """Assert that the target file has not exceeded its line budget.

    Args:
        file_path: Path to the instruction file (e.g. CLAUDE.md).
        config: SIOConfig or object with ``budget_cap_primary`` attribute.
    """
    cap = int(getattr(config, "budget_cap_primary", 100))
    line_count = 0
    if os.path.exists(file_path):
        with open(file_path) as f:
            line_count = sum(1 for _ in f)
    passed = line_count <= cap
    return AssertionResult(
        passed=passed,
        name="budget_within_limits",
        actual_value=float(line_count),
        threshold=float(cap),
        detail=f"{line_count}/{cap} lines",
    )


def no_collisions(
    suggestion: dict,
    existing_suggestions: list[dict],
    threshold: float = 0.85,
) -> AssertionResult:
    """Assert that the new suggestion does not collide with existing ones.

    Uses simple SequenceMatcher ratio as a proxy for semantic similarity.
    """
    from difflib import SequenceMatcher

    new_text = (suggestion.get("proposed_change") or "").lower()
    max_sim = 0.0
    for existing in existing_suggestions:
        existing_text = (existing.get("proposed_change") or "").lower()
        sim = SequenceMatcher(None, new_text, existing_text).ratio()
        if sim > max_sim:
            max_sim = sim

    passed = max_sim < threshold
    return AssertionResult(
        passed=passed,
        name="no_collisions",
        actual_value=max_sim,
        threshold=threshold,
        detail=f"max_similarity={max_sim:.4f}",
    )


# ---------------------------------------------------------------------------
# Registry + runner
# ---------------------------------------------------------------------------

_BUILTIN_ASSERTIONS: dict[str, Callable] = {
    "error_rate_decreased": error_rate_decreased,
    "no_new_regressions": no_new_regressions,
    "confidence_above_threshold": confidence_above_threshold,
    "budget_within_limits": budget_within_limits,
    "no_collisions": no_collisions,
}


def run_assertions(
    names: list[str],
    context: dict[str, Any],
) -> list[AssertionResult]:
    """Run a list of named assertions against the provided context.

    Built-in assertion names are resolved from the registry.  Any key in
    *context* whose value is a callable and whose key matches a name in
    *names* will be invoked as a custom assertion — it must accept
    ``(context,)`` and return an ``AssertionResult``.

    Args:
        names: List of assertion names to execute.
        context: Dict providing data for assertions (pre, post, pattern,
            file_path, config, suggestion, existing_suggestions, etc.)
            and optionally custom assertion callables.

    Returns:
        List of AssertionResult, one per name.
    """
    results: list[AssertionResult] = []
    for name in names:
        # Check for custom callable in context first
        custom_fn = context.get(name)
        if callable(custom_fn) and name not in _BUILTIN_ASSERTIONS:
            result = custom_fn(context)
            results.append(result)
            continue

        fn = _BUILTIN_ASSERTIONS.get(name)
        if fn is None:
            results.append(AssertionResult(
                passed=False,
                name=name,
                actual_value=0.0,
                threshold=0.0,
                detail=f"Unknown assertion: {name}",
            ))
            continue

        # Dispatch with correct arguments per assertion
        if name == "error_rate_decreased":
            result = fn(context.get("pre", {}), context.get("post", {}))
        elif name == "no_new_regressions":
            result = fn(context.get("pre", {}), context.get("post", {}))
        elif name == "confidence_above_threshold":
            result = fn(
                context.get("pattern", {}),
                context.get("confidence_threshold", 0.7),
            )
        elif name == "budget_within_limits":
            result = fn(
                context.get("file_path", ""),
                context.get("config", object()),
            )
        elif name == "no_collisions":
            result = fn(
                context.get("suggestion", {}),
                context.get("existing_suggestions", []),
                context.get("collision_threshold", 0.85),
            )
        else:
            result = fn(context)

        results.append(result)

    return results
