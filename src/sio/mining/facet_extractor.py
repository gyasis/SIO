"""Facet extraction for session qualitative summaries.

Extracts four categorical facets from parsed session data using keyword-based
heuristics (no LLM required):
  - tool_mastery: tool diversity + approval rate
  - error_prone_area: most frequent error type
  - user_satisfaction: average sentiment score
  - session_complexity: message count * log(token count)

Results are cached by session file content hash under ``~/.sio/facets/``.

Implements FR-049 and FR-050.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

_FACETS_DIR = os.path.expanduser("~/.sio/facets")


def _hash_content(content: str) -> str:
    """Return SHA-256 hex digest of *content*."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _cache_path(file_hash: str) -> Path:
    return Path(_FACETS_DIR) / f"{file_hash}.json"


def _load_cache(file_hash: str) -> dict[str, Any] | None:
    """Return cached facets dict or ``None``."""
    p = _cache_path(file_hash)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            from sio.core.observability import log_failure  # noqa: PLC0415
            log_failure(
                "cache_errors", str(p), e,
                stage="facet_cache_read", severity="debug",
            )
            return None
    return None


def _save_cache(file_hash: str, facets: dict[str, Any]) -> None:
    p = _cache_path(file_hash)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(facets, indent=2))


# ---------------------------------------------------------------------------
# Facet computation helpers
# ---------------------------------------------------------------------------


def _compute_tool_mastery(
    parsed_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute tool mastery from tool diversity and approval rate.

    Returns dict with ``level`` ("high"/"medium"/"low"), ``distinct_tools``,
    and ``approval_rate``.
    """
    tools_used: set[str] = set()
    total_tool_calls = 0
    approved_calls = 0

    for msg in parsed_messages:
        tool = msg.get("tool_name") or msg.get("tool")
        if tool:
            tools_used.add(tool)
            total_tool_calls += 1
            # A tool call is "approved" if the next user message is not a
            # correction/rejection.  We use the ``approved`` flag when present,
            # otherwise fall back to absence of correction signal.
            if msg.get("approved") is True or msg.get("approved") is None:
                approved_calls += 1

    diversity = len(tools_used)
    approval_rate = approved_calls / total_tool_calls if total_tool_calls > 0 else 0.0

    if diversity >= 5 and approval_rate >= 0.8:
        level = "high"
    elif diversity >= 3 and approval_rate >= 0.5:
        level = "medium"
    else:
        level = "low"

    return {
        "level": level,
        "distinct_tools": diversity,
        "approval_rate": round(approval_rate, 3),
    }


def _compute_error_prone_area(
    session_metrics: dict[str, Any] | None,
    parsed_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Identify the most frequent error type.

    Checks ``session_metrics`` first (if it has ``error_type_counts``), then
    falls back to scanning parsed messages for ``error_type`` fields.
    """
    counts: dict[str, int] = {}

    # Try session_metrics
    if session_metrics:
        etcounts = session_metrics.get("error_type_counts")
        if isinstance(etcounts, dict):
            counts = dict(etcounts)

    # Fallback: scan messages
    if not counts:
        for msg in parsed_messages:
            etype = msg.get("error_type")
            if etype:
                counts[etype] = counts.get(etype, 0) + 1

    if not counts:
        return {"area": "none", "error_type": None, "count": 0}

    top_type = max(counts, key=counts.get)  # type: ignore[arg-type]
    return {
        "area": top_type,
        "error_type": top_type,
        "count": counts[top_type],
    }


def _compute_user_satisfaction(
    parsed_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Average sentiment score across user messages.

    Sentiment values are expected in [-1.0, +1.0]. Messages without a score
    are skipped.
    """
    scores: list[float] = []
    for msg in parsed_messages:
        score = msg.get("sentiment_score")
        if score is not None:
            try:
                scores.append(float(score))
            except (TypeError, ValueError) as e:
                from sio.core.observability import log_failure  # noqa: PLC0415
                log_failure(
                    "parse_errors", f"sentiment_score={score!r}", e,
                    stage="sentiment_float", severity="debug",
                )

    if not scores:
        return {"level": "neutral", "avg_score": 0.0, "scored_messages": 0}

    avg = sum(scores) / len(scores)

    if avg >= 0.3:
        level = "positive"
    elif avg <= -0.3:
        level = "negative"
    else:
        level = "neutral"

    return {
        "level": level,
        "avg_score": round(avg, 3),
        "scored_messages": len(scores),
    }


def _compute_session_complexity(
    parsed_messages: list[dict[str, Any]],
    session_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Classify session complexity using ``message_count * log(token_count)``.

    Thresholds:
      - simple:   score < 50
      - moderate: 50 <= score < 200
      - complex:  score >= 200
    """
    message_count = len(parsed_messages)
    token_count = 0

    if session_metrics:
        token_count = (session_metrics.get("total_input_tokens") or 0) + (
            session_metrics.get("total_output_tokens") or 0
        )

    # Fallback: sum per-message tokens
    if token_count == 0:
        for msg in parsed_messages:
            token_count += msg.get("input_tokens", 0) + msg.get("output_tokens", 0)

    # Avoid log(0)
    log_tokens = math.log(max(token_count, 1))
    score = message_count * log_tokens

    if score < 50:
        level = "simple"
    elif score < 200:
        level = "moderate"
    else:
        level = "complex"

    return {
        "level": level,
        "score": round(score, 2),
        "message_count": message_count,
        "token_count": token_count,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_facets(
    parsed_messages: list[dict[str, Any]],
    session_metrics: dict[str, Any] | None = None,
    *,
    file_hash: str | None = None,
) -> dict[str, Any]:
    """Extract four qualitative facets from a session.

    Parameters
    ----------
    parsed_messages:
        List of parsed message dicts (from JSONL/SpecStory parser).
    session_metrics:
        Optional session-level aggregate metrics dict.
    file_hash:
        Content hash of the source file.  When provided, results are
        cached to ``~/.sio/facets/<hash>.json`` and returned from cache
        on subsequent calls with the same hash.

    Returns
    -------
    dict with keys: ``tool_mastery``, ``error_prone_area``,
    ``user_satisfaction``, ``session_complexity``.
    """
    # Check cache
    if file_hash:
        cached = _load_cache(file_hash)
        if cached is not None:
            return cached

    facets = {
        "tool_mastery": _compute_tool_mastery(parsed_messages),
        "error_prone_area": _compute_error_prone_area(
            session_metrics,
            parsed_messages,
        ),
        "user_satisfaction": _compute_user_satisfaction(parsed_messages),
        "session_complexity": _compute_session_complexity(
            parsed_messages,
            session_metrics,
        ),
    }

    # Persist cache
    if file_hash:
        _save_cache(file_hash, facets)

    return facets
