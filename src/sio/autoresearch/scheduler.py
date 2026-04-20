"""Autoresearch scheduler — candidate evaluation and txlog recording (T075, T076).

US4: Automatically evaluates active suggestions against the arena gate and
metric threshold, writing one autoresearch_txlog row per ``run_once()`` call
(a single aggregate summary row — not one per suggestion).

Approval gate (FR-006, Risk Mitigation §6):
- Default mode is ``pending`` — no auto-promotion unless ``auto_approve_above``
  is explicitly passed.
- ``arena_passed=1`` is REQUIRED for any promotion path.
- ``arena_passed=0`` always yields ``rejected_arena``, regardless of metric.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_APPROVAL_MODE = "pending"
"""Safe default: suggestions require explicit human approval even when
metric_score exceeds a threshold. Auto-promotion must be opt-in via
``auto_approve_above`` parameter."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _score_suggestion(row: sqlite3.Row) -> float:
    """Compute metric score for a suggestion.

    Uses ``arena_score`` from the suggestions table as the metric.
    Falls back to ``confidence`` when arena_score is NULL.
    """
    arena_score = row["arena_score"]
    if arena_score is not None:
        return float(arena_score)
    confidence = row["confidence"]
    if confidence is not None:
        return float(confidence)
    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def autoresearch_run_once(
    conn: sqlite3.Connection,
    auto_approve_above: float | None = None,
) -> dict[str, Any]:
    """Evaluate all active suggestions and record one aggregate outcome row
    in autoresearch_txlog per call.

    For each active suggestion (status != 'rejected'):

    - ``arena_passed=0``  → counted as ``rejected_arena``.
    - ``arena_passed=1``  AND ``auto_approve_above`` is None → ``pending_approval``.
    - ``arena_passed=1``  AND metric_score >= auto_approve_above → ``promoted``
      with auto_approved=1.
    - ``arena_passed=1``  AND metric_score < auto_approve_above → ``pending_approval``.
    - ``arena_passed`` is NULL (not yet evaluated) → ``pending_approval``.

    ONE autoresearch_txlog row is written per call (aggregate summary).
    The ``outcome`` field reflects the dominant outcome of the run.
    The ``auto_approved`` field is 1 only when at least one suggestion was
    promoted via auto_approve_above.

    Args:
        conn: Open SQLite connection to the SIO database.
        auto_approve_above: When not None, suggestions with ``arena_passed=1``
            and ``metric_score >= auto_approve_above`` are automatically promoted.
            Defaults to None (no auto-promotion — safe default per FR-006).

    Returns:
        Summary dict with keys: ``fired_at``, ``candidates_evaluated``,
        ``promoted``, ``pending_approval``, ``rejected_arena``, ``rejected_metric``.
    """
    fired_at = _utc_now_iso()
    counts: dict[str, int] = {
        "candidates_evaluated": 0,
        "promoted": 0,
        "pending_approval": 0,
        "rejected_arena": 0,
        "rejected_metric": 0,
    }
    avg_metric: float = 0.0
    metric_sum: float = 0.0

    # Fetch active suggestions — support tables with or without superseded_at column
    try:
        rows = conn.execute(
            """
            SELECT * FROM suggestions
            WHERE status != 'rejected'
              AND (superseded_at IS NULL OR superseded_at = '')
            """
        ).fetchall()
    except sqlite3.OperationalError:
        # superseded_at column may not exist in minimal test schema
        rows = conn.execute(
            "SELECT * FROM suggestions WHERE status != 'rejected'"
        ).fetchall()

    for row in rows:
        counts["candidates_evaluated"] += 1
        arena_passed = row["arena_passed"]
        metric_score = _score_suggestion(row)
        metric_sum += metric_score

        if arena_passed == 0:
            counts["rejected_arena"] += 1
        elif arena_passed == 1:
            if auto_approve_above is not None and metric_score >= auto_approve_above:
                counts["promoted"] += 1
            else:
                counts["pending_approval"] += 1
        else:
            # arena_passed is NULL — awaiting arena evaluation
            counts["pending_approval"] += 1

    n = counts["candidates_evaluated"]
    avg_metric = (metric_sum / n) if n > 0 else 0.0

    # Determine aggregate outcome label for the summary row
    if counts["promoted"] > 0:
        aggregate_outcome = "promoted"
    elif counts["pending_approval"] > 0:
        aggregate_outcome = "pending_approval"
    elif counts["rejected_arena"] > 0:
        aggregate_outcome = "rejected_arena"
    else:
        aggregate_outcome = "no_candidates"

    auto_approved_flag = 1 if counts["promoted"] > 0 else 0

    notes = (
        f"candidates={n} promoted={counts['promoted']} "
        f"pending={counts['pending_approval']} "
        f"rejected_arena={counts['rejected_arena']} "
        f"avg_metric={avg_metric:.4f}"
    )

    # Write exactly ONE aggregate row per call
    conn.execute(
        """
        INSERT INTO autoresearch_txlog
            (suggestion_id, outcome, metric_score, auto_approved, run_timestamp, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            None,            # suggestion_id: NULL for aggregate row
            aggregate_outcome,
            avg_metric,
            auto_approved_flag,
            fired_at,
            notes,
        ),
    )
    conn.commit()

    return {"fired_at": fired_at, **counts}


# Alias — some callers import run_once directly
run_once = autoresearch_run_once
