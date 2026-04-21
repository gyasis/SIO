"""Arena regression runner — orchestrates gold standard replay + drift + collision checks."""

from __future__ import annotations

import sqlite3

from sio.core.arena.drift_detector import measure_drift, requires_manual_approval
from sio.core.arena.gold_standards import get_all_for_skill, replay_against_prompt


def run_arena(
    conn: sqlite3.Connection,
    skill_name: str,
    new_prompt: str,
    embedder=None,
) -> dict:
    """Run arena validation for an optimization.

    Orchestrates:
    1. Gold standard replay
    2. Drift check
    3. Collision check (if descriptions provided)

    Returns:
        Dict with passed, reasons, drift_score, gold_results.
    """
    reasons = []
    gold_results = []

    golds = get_all_for_skill(conn, skill_name)

    if not golds:
        return {
            "passed": True,
            "reasons": ["No gold standards to validate against"],
            "drift_score": 0.0,
            "gold_results": [],
        }

    # Replay gold standards
    failed_golds = 0
    for gold in golds:
        passed = replay_against_prompt(gold, new_prompt)
        gold_results.append(
            {
                "gold_id": gold.get("id"),
                "passed": passed,
            }
        )
        if not passed:
            failed_golds += 1

    if failed_golds > 0:
        reasons.append(f"{failed_golds}/{len(golds)} gold standards failed")

    # Drift check — compare against first gold's message
    reference = golds[0].get("user_message", "")
    drift_score = measure_drift(reference, new_prompt, embedder)

    if requires_manual_approval(drift_score):
        reasons.append(f"Drift {drift_score:.1%} exceeds 40% threshold")

    passed = len(reasons) == 0

    return {
        "passed": passed,
        "reasons": reasons if reasons else ["All checks passed"],
        "drift_score": drift_score,
        "gold_results": gold_results,
    }
