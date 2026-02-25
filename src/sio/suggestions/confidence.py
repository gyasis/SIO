"""sio.suggestions.confidence — confidence scoring for improvement suggestions.

Public API
----------
    score_confidence(pattern: dict, dataset: dict) -> float

The score is a weighted combination of three signals:

- error_count weight  (40 %): capped at 30 errors for normalisation
- dataset coverage    (30 %): log-scaled total examples, capped at 90
- rank_score          (30 %): passed through directly (already 0–1)

The final score is clamped to [0.0, 1.0].
"""

from __future__ import annotations

import math


def score_confidence(pattern: dict, dataset: dict) -> float:
    """Compute a confidence score for a suggestion based on pattern and dataset quality.

    Parameters
    ----------
    pattern:
        Pattern dict with at minimum ``error_count`` (int) and
        ``rank_score`` (float 0–1).
    dataset:
        Dataset metadata dict with ``positive_count`` (int) and
        ``negative_count`` (int).

    Returns
    -------
    float
        Confidence value in [0.0, 1.0].
    """
    error_count: int = int(pattern.get("error_count") or 0)
    rank_score: float = float(pattern.get("rank_score") or 0.0)
    positive_count: int = int(dataset.get("positive_count") or 0)
    negative_count: int = int(dataset.get("negative_count") or 0)

    # --- Error-count signal (0–1, capped at 30) ----------------------------
    error_cap = 30.0
    error_signal = min(error_count / error_cap, 1.0)

    # --- Dataset coverage signal (0–1, log-scaled, capped at 90 examples) -
    total_examples = positive_count + negative_count
    if total_examples > 0:
        # log(1 + x) / log(1 + cap) gives a nice concave curve
        coverage_cap = 90.0
        dataset_signal = math.log1p(total_examples) / math.log1p(coverage_cap)
        dataset_signal = min(dataset_signal, 1.0)
    else:
        dataset_signal = 0.0

    # --- Rank score (already normalised 0–1) --------------------------------
    rank_signal = max(0.0, min(float(rank_score), 1.0))

    # --- Weighted combination -----------------------------------------------
    raw = 0.40 * error_signal + 0.30 * dataset_signal + 0.30 * rank_signal

    return float(max(0.0, min(raw, 1.0)))
