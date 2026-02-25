"""sio.clustering.ranker — frequency × recency pattern scoring.

Exported public API
-------------------
rank_patterns(patterns: list[dict]) -> list[dict]
    Accept a list of pattern dicts, compute a rank_score for each, and
    return them sorted by rank_score descending (highest urgency first).

Algorithm
---------
    rank_score = error_count * recency_weight

    recency_weight = 1.0 / (1.0 + days_since_last_seen)

    days_since_last_seen = (now - last_seen).total_seconds() / 86400

Properties:
- A pattern seen right now has days_since_last_seen ≈ 0, giving
  recency_weight ≈ 1.0  (maximum).
- A pattern from 1 day ago has recency_weight = 0.5.
- A pattern from 30 days ago has recency_weight ≈ 0.032.
- rank_score is always positive for error_count > 0.
- Ties on error_count are naturally broken by recency because
  recency_weight is strictly monotone in recency.

The function is a pure function: it does not read or write any external
state, performs no I/O, and does not mutate the input dicts in-place
(each dict gets a fresh 'rank_score' key written on a shallow copy).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def rank_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank patterns by combined frequency × recency score.

    Parameters
    ----------
    patterns:
        A list of pattern dicts.  Each dict must contain at least:
        - ``error_count`` (int): number of errors associated with this pattern.
        - ``last_seen`` (str): ISO 8601 datetime string (timezone-aware or
          naive UTC) indicating when the pattern was last observed.

    Returns
    -------
    list[dict]
        A new list of shallow-copied pattern dicts, each with
        ``rank_score`` set to a float, sorted by ``rank_score``
        descending.  The input list is not mutated.  Returns ``[]``
        for empty input.

    Examples
    --------
    >>> from datetime import datetime, timezone, timedelta
    >>> now = datetime.now(timezone.utc).isoformat()
    >>> ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    >>> recent = {"error_count": 5, "last_seen": now, "pattern_id": "a"}
    >>> old = {"error_count": 5, "last_seen": ago, "pattern_id": "b"}
    >>> ranked = rank_patterns([old, recent])
    >>> ranked[0]["pattern_id"]
    'a'
    """
    if not patterns:
        return []

    now: datetime = datetime.now(timezone.utc)
    scored: list[dict[str, Any]] = []

    for pattern in patterns:
        last_seen_raw: str = pattern["last_seen"]
        last_seen: datetime = datetime.fromisoformat(last_seen_raw)

        # Ensure both datetimes are timezone-aware so subtraction is valid.
        # If last_seen is naive we treat it as UTC (matches test helper _ts).
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        days_since: float = (now - last_seen).total_seconds() / 86400.0
        # Guard against tiny negative values caused by sub-millisecond clock skew.
        days_since = max(days_since, 0.0)

        recency_weight: float = 1.0 / (1.0 + days_since)
        rank_score: float = float(pattern["error_count"]) * recency_weight

        # Shallow copy so we do not mutate the caller's dict.
        scored.append({**pattern, "rank_score": rank_score})

    scored.sort(key=lambda p: p["rank_score"], reverse=True)
    return scored
