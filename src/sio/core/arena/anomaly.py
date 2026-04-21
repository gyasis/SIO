"""MAD-based anomaly detection for session metrics (FR-046).

Uses Median Absolute Deviation to find statistically anomalous sessions
without assuming normal distributions.
"""

from __future__ import annotations

import sqlite3
from typing import Sequence


def compute_mad(values: Sequence[float]) -> tuple[float, float]:
    """Compute the Median Absolute Deviation of *values*.

    Args:
        values: Sequence of numeric values (must have len >= 1).

    Returns:
        Tuple of (median, MAD).  MAD is the median of |x_i - median|.
        Returns (0.0, 0.0) for empty input.
    """
    if not values:
        return 0.0, 0.0

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    # Median
    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0

    # MAD = median(|x_i - median|)
    abs_devs = sorted(abs(v - median) for v in sorted_vals)
    if n % 2 == 1:
        mad = abs_devs[n // 2]
    else:
        mad = (abs_devs[n // 2 - 1] + abs_devs[n // 2]) / 2.0

    return median, mad


def detect_anomalies(
    db: sqlite3.Connection,
    metric_name: str,
    threshold_mads: float = 3.0,
) -> list[str]:
    """Detect anomalous session IDs based on a session_metrics column.

    Queries all values of *metric_name* from session_metrics, computes
    the MAD, and flags sessions whose value deviates by more than
    *threshold_mads* MADs from the median.

    Supported metric names: ``error_count``, ``total_cost_usd``,
    ``session_duration_seconds``, ``total_input_tokens``,
    ``total_output_tokens``.

    Args:
        db: Open SQLite connection with session_metrics table.
        metric_name: Column name in session_metrics to analyze.
        threshold_mads: Number of MADs from median to flag (default 3).

    Returns:
        List of session_id strings flagged as anomalous.
    """
    _ALLOWED_METRICS = {
        "error_count",
        "total_cost_usd",
        "session_duration_seconds",
        "total_input_tokens",
        "total_output_tokens",
    }

    if metric_name not in _ALLOWED_METRICS:
        raise ValueError(f"Unsupported metric: {metric_name}. Allowed: {sorted(_ALLOWED_METRICS)}")

    rows = db.execute(
        f"SELECT session_id, {metric_name} FROM session_metrics "  # noqa: S608
        f"WHERE {metric_name} IS NOT NULL",
    ).fetchall()

    if len(rows) < 3:
        return []  # Not enough data for meaningful anomaly detection

    values = [float(r[1]) for r in rows]
    session_ids = [r[0] for r in rows]

    median, mad = compute_mad(values)

    if mad == 0.0:
        # All values are the same — no anomalies possible unless
        # some deviate from the constant (handles the edge case where
        # most values are identical and one outlier exists).
        return [sid for sid, val in zip(session_ids, values) if val != median]

    anomalous: list[str] = []
    for sid, val in zip(session_ids, values):
        deviation = abs(val - median) / mad
        if deviation > threshold_mads:
            anomalous.append(sid)

    return anomalous
