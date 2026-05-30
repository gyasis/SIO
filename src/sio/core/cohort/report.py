"""A/B report engine for the cohort primitive.

PRD §6 Phase 3. ``build_report`` assembles a report dict comparing an
experiment's cohort window against a prior baseline window:

  * error-rate delta (per-hour normalized)        — T017 (this wave)
  * new error classes appearing in the experiment — T018 (Wave 14)
  * flow delta (emerged / died)                    — T019 (Wave 15)
  * scoped suggestions                             — T020 (Wave 16)

The three renderers (text / html / json) consume the same dict, so the
later waves extend ``build_report`` without touching the renderers.
``render_report`` is the dispatcher the CLI ``experiment close --report``
calls (T024).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sio.core.cohort.store import ExperimentNotFound, get_experiment

_BASELINE_RE = re.compile(r"^\s*(\d+)\s*([dhw]?)\s*$", re.IGNORECASE)


def parse_baseline(spec: str) -> timedelta:
    """Parse a baseline spec like '7d', '14d', '48h', '2w' into a timedelta.

    Bare integers are interpreted as days. Raises ``ValueError`` on junk.
    """
    m = _BASELINE_RE.match(spec or "")
    if not m:
        raise ValueError(f"Unparseable baseline spec: {spec!r}")
    n = int(m.group(1))
    unit = (m.group(2) or "d").lower()
    if unit == "h":
        return timedelta(hours=n)
    if unit == "w":
        return timedelta(weeks=n)
    return timedelta(days=n)


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (tolerates trailing Z)."""
    cleaned = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _hours_between(start: str, end: str) -> float:
    delta = _parse_iso(end) - _parse_iso(start)
    return max(delta.total_seconds() / 3600.0, 0.0)


def _count_errors(conn: sqlite3.Connection, start: str, end: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM error_records WHERE timestamp >= ? AND timestamp <= ?",
        (start, end),
    ).fetchone()
    return row[0] if row else 0


def compute_error_rate_delta(
    conn: sqlite3.Connection,
    exp_start: str,
    exp_end: str,
    baseline_start: str,
    baseline_end: str,
) -> dict[str, Any]:
    """Per-hour-normalized error-rate delta between the two windows.

    Normalization matters because the experiment and baseline windows
    are rarely the same length — comparing raw counts would be
    misleading. T017.
    """
    exp_hours = _hours_between(exp_start, exp_end)
    base_hours = _hours_between(baseline_start, baseline_end)
    exp_count = _count_errors(conn, exp_start, exp_end)
    base_count = _count_errors(conn, baseline_start, baseline_end)

    exp_rate = (exp_count / exp_hours) if exp_hours > 0 else 0.0
    base_rate = (base_count / base_hours) if base_hours > 0 else 0.0
    delta_rate = exp_rate - base_rate
    if base_rate > 0:
        delta_pct = (delta_rate / base_rate) * 100.0
    else:
        delta_pct = None  # undefined — no baseline activity to compare against

    return {
        "experiment": {
            "count": exp_count,
            "hours": round(exp_hours, 2),
            "per_hour": round(exp_rate, 4),
        },
        "baseline": {
            "count": base_count,
            "hours": round(base_hours, 2),
            "per_hour": round(base_rate, 4),
        },
        "delta_per_hour": round(delta_rate, 4),
        "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
    }


def build_report(
    db_path: str | Path,
    experiment_name: str,
    *,
    baseline: str = "7d",
) -> dict[str, Any]:
    """Assemble the full A/B report dict for ``experiment_name``.

    The experiment window is [start_ts, close_ts or now]. The baseline
    window is the ``baseline``-long span immediately preceding start_ts.

    Sections for T018-T020 are present as keys with empty defaults so
    the renderers are stable across waves; those waves fill them in.

    Raises:
        ExperimentNotFound: if the experiment doesn't exist.
    """
    exp = get_experiment(db_path, experiment_name)
    if exp is None:
        raise ExperimentNotFound(f"No experiment named {experiment_name!r}")

    exp_start = exp.start_ts
    exp_end = exp.close_ts or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    baseline_delta = parse_baseline(baseline)
    baseline_end = exp_start
    baseline_start = (_parse_iso(exp_start) - baseline_delta).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        error_rate = compute_error_rate_delta(
            conn, exp_start, exp_end, baseline_start, baseline_end
        )
        new_error_classes = compute_new_error_classes(
            conn, exp_start, exp_end, baseline_start, baseline_end
        )
        flow_delta = compute_flow_delta(
            conn, exp_start, exp_end, baseline_start, baseline_end
        )
    finally:
        conn.close()

    suggestions = compute_scoped_suggestions(
        db_path, experiment_name, exp_start, exp_end
    )

    return {
        "experiment": {
            "name": exp.name,
            "start_ts": exp.start_ts,
            "close_ts": exp.close_ts,
            "status": exp.status,
            "project": exp.project,
            "note": exp.note,
            "config_hash": exp.config_hash,
        },
        "windows": {
            "experiment": {"start": exp_start, "end": exp_end},
            "baseline": {"start": baseline_start, "end": baseline_end},
            "baseline_spec": baseline,
        },
        "error_rate": error_rate,
        "new_error_classes": new_error_classes,
        "flow_delta": flow_delta,
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Sections filled by later waves. Defined here as no-op stubs so build_report
# stays import-stable; Waves 14-16 replace the bodies.
# ---------------------------------------------------------------------------


def compute_new_error_classes(
    conn: sqlite3.Connection,
    exp_start: str,
    exp_end: str,
    baseline_start: str,
    baseline_end: str,
) -> list[dict[str, Any]]:
    """New error classes present in the experiment but NOT baseline (T018).

    "Class" = ``error_records.error_type``. An error type that appears in
    the experiment window and never appeared in the baseline window is a
    regression signal the cohort introduced. Returns one row per such
    type with a count and a sample ``error_text``, ordered most-frequent
    first.
    """
    baseline_types = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT error_type FROM error_records "
            "WHERE timestamp >= ? AND timestamp <= ? AND error_type IS NOT NULL",
            (baseline_start, baseline_end),
        ).fetchall()
    }

    rows = conn.execute(
        "SELECT error_type, COUNT(*) AS cnt, MIN(error_text) AS sample "
        "FROM error_records "
        "WHERE timestamp >= ? AND timestamp <= ? AND error_type IS NOT NULL "
        "GROUP BY error_type ORDER BY cnt DESC",
        (exp_start, exp_end),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for error_type, cnt, sample in rows:
        if error_type in baseline_types:
            continue
        out.append(
            {
                "error_type": error_type,
                "count": cnt,
                "sample": (sample or "")[:200],
            }
        )
    return out


def compute_flow_delta(
    conn: sqlite3.Connection,
    exp_start: str,
    exp_end: str,
    baseline_start: str,
    baseline_end: str,
) -> dict[str, Any]:
    """Flows that emerged / died between the two windows (T019).

    * emerged = flow sequences present in the experiment window but NOT in
      the baseline window (new positive patterns the cohort introduced)
    * died    = flow sequences present in the baseline but NOT the
      experiment (patterns that stopped happening)

    Compared by ``sequence`` (the human-readable tool chain). ``min_count``
    is 1 here — for a diff we want presence/absence, not frequency
    thresholds.
    """
    from sio.mining.flow_pipeline import query_flows  # noqa: PLC0415

    exp_flows = query_flows(
        conn, since=exp_start, until=exp_end, min_count=1, limit=1000
    )
    base_flows = query_flows(
        conn, since=baseline_start, until=baseline_end, min_count=1, limit=1000
    )

    exp_by_seq = {f["sequence"]: f for f in exp_flows}
    base_by_seq = {f["sequence"]: f for f in base_flows}

    emerged = [
        {"sequence": seq, "count": f.get("count", 0)}
        for seq, f in exp_by_seq.items()
        if seq not in base_by_seq
    ]
    died = [
        {"sequence": seq, "count": f.get("count", 0)}
        for seq, f in base_by_seq.items()
        if seq not in exp_by_seq
    ]
    emerged.sort(key=lambda x: x["count"], reverse=True)
    died.sort(key=lambda x: x["count"], reverse=True)
    return {"emerged": emerged, "died": died}


def compute_scoped_suggestions(
    db_path: str | Path,
    experiment_name: str,
    exp_start: str,
    exp_end: str,
) -> list[dict[str, Any]]:
    """Suggestions scoped to the experiment window (T020).

    Reads suggestions already persisted in the DB whose underlying error
    records fall inside the experiment window (joined via
    ``pattern_errors``). We deliberately do NOT invoke the DSPy
    ``sio suggest`` generator inline: report generation must be cheap and
    must not trigger paid LM calls (economy-first principle). If the user
    wants fresh suggestions for the cohort they run
    ``sio suggest --experiment NAME`` explicitly.

    Returns one row per suggestion (deduped), highest-confidence first.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT s.id AS id, s.description AS description,
                   s.confidence AS confidence, s.status AS status,
                   s.target_file AS target_file
            FROM suggestions s
            JOIN pattern_errors pe ON pe.pattern_id = s.pattern_id
            JOIN error_records e ON e.id = pe.error_id
            WHERE e.timestamp >= ? AND e.timestamp <= ?
            ORDER BY s.confidence DESC
            """,
            (exp_start, exp_end),
        ).fetchall()
    except sqlite3.OperationalError:
        # pattern_errors.active / older schema quirks — fail soft to empty.
        return []
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "description": r["description"],
            "confidence": r["confidence"],
            "status": r["status"],
            "target_file": r["target_file"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Dispatcher — called by `sio experiment close --report` (T024)
# ---------------------------------------------------------------------------


def render_report(
    db_path: str | Path,
    experiment_name: str,
    *,
    fmt: str = "text",
    baseline: str = "7d",
    console: Optional[object] = None,
) -> str:
    """Build the report and render it in ``fmt`` (text|html|json).

    For ``text``, prints to ``console`` (a rich Console) if provided and
    also returns the string. For ``html`` / ``json`` the string is
    returned (the CLI decides whether to write a file or echo).
    """
    report = build_report(db_path, experiment_name, baseline=baseline)

    if fmt == "json":
        from sio.core.cohort.render_json import render_json  # noqa: PLC0415

        return render_json(report)
    if fmt == "html":
        from sio.core.cohort.render_html import render_html  # noqa: PLC0415

        return render_html(report)

    from sio.core.cohort.render_text import render_text  # noqa: PLC0415

    text = render_text(report)
    if console is not None:
        console.print(text)
    return text
