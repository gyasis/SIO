"""Real per-session metrics from the transcript, instead of a row of zeros.

The Stop and PreCompact hooks both wrote `total_input_tokens: 0`, `total_cost_usd: 0`,
`message_count: 0`, `sidechain_count: 0` and `model_used: None`. They left duration and
the stop-reason distribution NULL. Measured 2026-09-08: 514 `session_metrics` rows, and
every recent one read 0/0/0. One of them was a session that `sio live show` reported as
491 events with 4 errors. A zero there means "not counted", never "nothing happened".
Downstream, nothing tells the two apart: `sio report` sums `total_cost_usd` and
`reports/html_report.py` charts it, so a placeholder zero reads as a free session.

Every number here comes from the transcript the hook already holds. One streaming pass
over `jsonl_parser.iter_events`, which never loads the file into memory. Measured on a
29 MB transcript of 13,768 lines: 0.61 s. The import costs 1 ms.

This code sums `total_cost_usd`, and on Claude Code the sum is 0. That is a finding, not
a placeholder: **a Claude Code transcript carries no cost field.** The same 29 MB file
holds 2,547 `message.usage` blocks. It holds no `costUSD`, `costUsd` or `cost_usd`.
`jsonl_parser` reads `cost_usd` when a harness does send one, so the sum is right
wherever a cost exists. A cost derived from the token counts would need a price table
inside a hook. We could not cite that number, and it would go stale in
silence. So this code keeps the token counts and leaves the pricing to the reader.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def transcript_metrics(transcript_path: str | None) -> dict:
    """Aggregate the `session_metrics` fields that only the transcript knows.

    Returns a dict ready to merge over a hook's record, or an empty dict when the
    transcript is missing, unreadable or only partly read. Empty means "unknown" and
    leaves the caller's own values in place — a partial count is worse than no count,
    because it looks exactly like a complete one.

    Never raises: a metrics snapshot must not be able to take a hook down.
    """
    if not transcript_path:
        return {}
    path = Path(transcript_path)
    if not path.is_file():
        return {}

    try:
        from sio.mining.jsonl_parser import iter_events  # noqa: PLC0415
    except Exception:
        logger.debug("jsonl_parser unavailable — leaving session metrics unknown")
        return {}

    tokens_in = tokens_out = cache_read = cache_create = 0
    messages = sidechains = 0
    cost = 0.0
    stop_reasons: Counter[str] = Counter()
    models: Counter[str] = Counter()
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    try:
        for event, _offset in iter_events(path):
            messages += 1
            tokens_in += event.get("input_tokens") or 0
            tokens_out += event.get("output_tokens") or 0
            cache_read += event.get("cache_read_input_tokens") or 0
            cache_create += event.get("cache_creation_input_tokens") or 0
            cost += event.get("cost_usd") or 0.0
            if event.get("is_sidechain"):
                sidechains += 1
            reason = event.get("stop_reason")
            if reason:
                stop_reasons[str(reason)] += 1
            model = event.get("model")
            if model:
                models[str(model)] += 1
            ts = _parse_ts(event.get("timestamp"))
            if ts is not None:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
    except Exception:
        logger.exception("transcript read failed — leaving session metrics unknown")
        return {}

    if not messages:
        return {}

    import json  # noqa: PLC0415

    denom = cache_read + tokens_in
    duration = None
    if first_ts is not None and last_ts is not None and last_ts > first_ts:
        duration = (last_ts - first_ts).total_seconds()

    return {
        "total_input_tokens": tokens_in,
        "total_output_tokens": tokens_out,
        "total_cache_read_tokens": cache_read,
        "total_cache_create_tokens": cache_create,
        "cache_hit_ratio": (cache_read / denom) if denom else None,
        "total_cost_usd": cost,
        "session_duration_seconds": duration,
        "message_count": messages,
        "sidechain_count": sidechains,
        "stop_reason_distribution": (
            json.dumps(dict(stop_reasons)) if stop_reasons else None
        ),
        "model_used": models.most_common(1)[0][0] if models else None,
    }
