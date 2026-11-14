"""session_signals — positive signals, approvals, sentiment and metrics for one non-Claude session.

The Claude file-scan path (``mining/pipeline.py``) records these for every
transcript. The adapter mine paths (``sio mine --session <agent:id>`` and
``sio mine --agent <name>``) call :func:`record_session_signals` so every
harness fills ``positive_records`` and ``session_metrics`` too.

Rows are keyed by the canonical ``<agent>:<native_id>``, the id the session's
error rows carry. Re-mining a grown session replaces its rows: positive
records for that session are removed and written again, and the metrics row is
``INSERT OR REPLACE`` on its session id. Claude rows are never touched.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sio.adapters.messages import pair_message_dicts

logger = logging.getLogger(__name__)


def record_session_signals(
    conn: sqlite3.Connection,
    messages: list[dict],
    canonical: str,
    agent: str,
    source_path: str | None,
    error_records: list[dict],
) -> dict[str, Any]:
    """Write positive signals and session metrics for one non-Claude session.

    ``messages`` are the unpaired per-event dicts the mine paths build; they
    are paired here (each tool call joined to its result) before analysis.
    Returns ``{"positive": n, "metrics": bool}``. A failing stage is logged
    with the session id and does not stop the other stage.
    """
    from sio.core.db.queries import insert_positive_record, insert_session_metrics
    from sio.mining.approval_detector import detect_approvals
    from sio.mining.pipeline import _compute_session_metrics
    from sio.mining.positive_extractor import extract_positive_signals

    if agent == "claude":
        raise ValueError("record_session_signals is for non-Claude sessions only")

    paired = pair_message_dicts(messages)
    now = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {"positive": 0, "metrics": False}

    try:
        signals = extract_positive_signals(paired)
        conn.execute(
            "DELETE FROM positive_records WHERE session_id = ? AND agent = ?",
            (canonical, agent),
        )
        for sig in signals:
            insert_positive_record(
                conn,
                {
                    "session_id": canonical,
                    "timestamp": sig.get("timestamp") or now,
                    "signal_type": sig["signal_type"],
                    "signal_text": sig.get("signal_text", ""),
                    "context_before": sig.get("context_before"),
                    "tool_name": sig.get("tool_name"),
                    "sentiment_score": None,
                    "source_file": source_path or canonical,
                    "mined_at": now,
                    "agent": agent,
                },
            )
        result["positive"] = len(signals)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Positive signal extraction failed for %s: %s: %s",
            canonical, type(exc).__name__, exc,
        )

    try:
        approvals = detect_approvals(paired)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        metrics = _compute_session_metrics(
            paired, error_records, Path(source_path or canonical), digest
        )
        metrics.update(
            session_id=canonical,
            file_path=source_path or canonical,
            positive_signal_count=result["positive"],
            correction_count=approvals.get("rejected", 0),
            agent=agent,
        )
        insert_session_metrics(conn, metrics)
        result["metrics"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Session metrics failed for %s: %s: %s", canonical, type(exc).__name__, exc
        )
    return result
