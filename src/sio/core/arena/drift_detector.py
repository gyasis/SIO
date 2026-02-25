"""Drift detector — measures semantic distance between prompts."""

from __future__ import annotations

from difflib import SequenceMatcher


def measure_drift(
    original_prompt: str,
    new_prompt: str,
    embedder=None,
) -> float:
    """Measure semantic drift between original and new prompt.

    V0.1: Uses SequenceMatcher ratio as a proxy for cosine distance.
    Full implementation will use embedding cosine distance.

    Returns:
        Float between 0.0 (identical) and 1.0 (completely different).
    """
    if original_prompt == new_prompt:
        return 0.0

    similarity = SequenceMatcher(
        None, original_prompt.lower(), new_prompt.lower(),
    ).ratio()

    return 1.0 - similarity


def requires_manual_approval(
    drift_score: float, threshold: float = 0.40,
) -> bool:
    """Check if drift exceeds threshold requiring manual approval.

    Args:
        drift_score: Cosine distance (0 = identical, 1 = opposite).
        threshold: Max auto-approved drift (default 40%).

    Returns:
        True if manual approval is required.
    """
    return drift_score >= threshold
