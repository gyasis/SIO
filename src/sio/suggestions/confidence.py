"""sio.suggestions.confidence — confidence scoring for improvement suggestions.

Public API
----------
    score_confidence(pattern: dict, dataset: dict, last_seen: str | None) -> float
    _compute_decay_multiplier(last_seen: str, config: SIOConfig | None) -> float

The score is a weighted combination of three signals:

- error_count weight  (40 %): capped at 30 errors for normalisation
- dataset coverage    (30 %): log-scaled total examples, capped at 90
- rank_score          (30 %): passed through directly (already 0–1)

When *last_seen* is provided, the final score is multiplied by a temporal
decay factor based on how recently the pattern was observed:

- Fresh band (0 to decay_fresh_days):  multiplier = 1.0
- Cooling band (decay_fresh_days to decay_stale_days):  linear 1.0 -> 0.6
- Stale band (> decay_stale_days):  linear 0.6 -> decay_floor at 2x stale_days

The final score is clamped to [0.0, 1.0].
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sio.core.config import SIOConfig, load_config


def _compute_decay_multiplier(
    last_seen: str,
    config: SIOConfig | None = None,
) -> float:
    """Compute a temporal decay multiplier based on how recently a pattern was seen.

    Parameters
    ----------
    last_seen:
        ISO-8601 date or datetime string for the pattern's most recent observation.
    config:
        Optional SIOConfig; loaded from disk if not provided.

    Returns
    -------
    float
        Decay multiplier in [config.decay_floor, 1.0].

    The decay has three bands:
    - **Fresh**: 0 to ``decay_fresh_days`` days ago -> 1.0 (no decay)
    - **Cooling**: ``decay_fresh_days`` to ``decay_stale_days`` -> linear from 1.0 to 0.6
    - **Stale**: beyond ``decay_stale_days`` -> linear from 0.6 toward ``decay_floor``,
      reaching floor at 2x ``decay_stale_days``
    """
    if config is None:
        config = load_config()

    fresh_days = config.decay_fresh_days   # default 14
    stale_days = config.decay_stale_days   # default 28
    floor = config.decay_floor             # default 0.3

    # Parse last_seen — handle both date-only and datetime strings
    try:
        dt = datetime.fromisoformat(last_seen)
    except ValueError:
        # If unparseable, assume maximally stale
        return floor

    # Make timezone-aware if naive
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    days_since = (now - dt).total_seconds() / 86400.0
    if days_since < 0:
        days_since = 0.0

    # --- Fresh band: no decay ---
    if days_since <= fresh_days:
        return 1.0

    # --- Cooling band: linear 1.0 -> 0.6 ---
    cooling_end_value = 0.6
    if days_since <= stale_days:
        fraction = (days_since - fresh_days) / (stale_days - fresh_days)
        multiplier = 1.0 - fraction * (1.0 - cooling_end_value)
        return max(multiplier, floor)

    # --- Stale band: linear 0.6 -> floor at 2x stale_days ---
    stale_ceiling = 2.0 * stale_days
    if days_since >= stale_ceiling:
        return floor

    fraction = (days_since - stale_days) / (stale_ceiling - stale_days)
    multiplier = cooling_end_value - fraction * (cooling_end_value - floor)
    return max(multiplier, floor)


def score_confidence(
    pattern: dict,
    dataset: dict,
    last_seen: str | None = None,
    config: SIOConfig | None = None,
) -> float:
    """Compute a confidence score for a suggestion based on pattern and dataset quality.

    Parameters
    ----------
    pattern:
        Pattern dict with at minimum ``error_count`` (int) and
        ``rank_score`` (float 0–1).
    dataset:
        Dataset metadata dict with ``positive_count`` (int) and
        ``negative_count`` (int).
    last_seen:
        Optional ISO-8601 date/datetime string.  When provided, the raw score
        is multiplied by a temporal decay factor (see ``_compute_decay_multiplier``).
    config:
        Optional SIOConfig for decay parameters; loaded from disk if needed.

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

    # --- Temporal decay (4th multiplicative factor) -------------------------
    if last_seen is not None:
        decay = _compute_decay_multiplier(last_seen, config=config)
        raw *= decay

    return float(max(0.0, min(raw, 1.0)))
