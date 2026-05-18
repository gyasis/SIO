"""Learning velocity tracking — FR-014, FR-015, FR-016.

Computes error frequency per type over rolling windows, measures correction
decay rate after rule application, and reports adaptation speed (sessions
until error rate drops below threshold).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


def compute_velocity_snapshot(
    db: sqlite3.Connection,
    error_type: str,
    window_days: int = 7,
) -> dict:
    """Compute a velocity snapshot for a given error type.

    Queries error_records for errors of the given type within a rolling
    window, computes error rate, checks if any suggestion targeting this
    error type has been applied, and calculates correction_decay_rate and
    adaptation_speed when applicable.

    The result is persisted into the velocity_snapshots table.

    Args:
        db: Open sqlite3.Connection with SIO schema.
        error_type: The error_type value to track (e.g. "unused_import").
        window_days: Rolling window size in days (default 7).

    Returns:
        Dict with keys: error_type, error_rate, error_count_in_window,
        correction_decay_rate, adaptation_speed, rule_applied,
        rule_suggestion_id, window_start, window_end, created_at.
    """
    now = datetime.now(timezone.utc)
    window_end = now.isoformat()
    window_start = (now - timedelta(days=window_days)).isoformat()

    # Count errors of this type in the window
    row = db.execute(
        "SELECT COUNT(*) FROM error_records "
        "WHERE error_type = ? AND timestamp >= ? AND timestamp <= ?",
        (error_type, window_start, window_end),
    ).fetchone()
    error_count_in_window = row[0]

    # Count total errors in the window (all types)
    total_row = db.execute(
        "SELECT COUNT(*) FROM error_records WHERE timestamp >= ? AND timestamp <= ?",
        (window_start, window_end),
    ).fetchone()
    total_errors_in_window = total_row[0]

    # Compute error rate
    if total_errors_in_window > 0:
        error_rate = error_count_in_window / total_errors_in_window
    else:
        error_rate = 0.0

    # Check if any suggestion targeting this error type has been applied.
    # We join suggestions with applied_changes, and look for suggestions
    # whose description or proposed_change references this error_type,
    # or whose linked pattern's error records include this error_type.
    applied_row = db.execute(
        "SELECT s.id, ac.applied_at FROM suggestions s "
        "JOIN applied_changes ac ON ac.suggestion_id = s.id "
        "WHERE ac.rolled_back_at IS NULL "
        "AND (s.description LIKE ? OR s.proposed_change LIKE ?) "
        "ORDER BY ac.applied_at DESC LIMIT 1",
        (f"%{error_type}%", f"%{error_type}%"),
    ).fetchone()

    rule_applied = applied_row is not None
    rule_suggestion_id = applied_row[0] if applied_row else None
    applied_at = applied_row[1] if applied_row else None

    # Compute correction_decay_rate and adaptation_speed
    correction_decay_rate: float | None = None
    adaptation_speed: int | None = None

    if rule_applied and applied_at:
        # Pre-rule error rate: count errors of this type in a window of
        # the same size BEFORE the rule was applied
        pre_window_end = applied_at
        pre_window_start = (
            datetime.fromisoformat(applied_at) - timedelta(days=window_days)
        ).isoformat()

        pre_type_row = db.execute(
            "SELECT COUNT(*) FROM error_records "
            "WHERE error_type = ? AND timestamp >= ? AND timestamp <= ?",
            (error_type, pre_window_start, pre_window_end),
        ).fetchone()
        pre_type_count = pre_type_row[0]

        pre_total_row = db.execute(
            "SELECT COUNT(*) FROM error_records WHERE timestamp >= ? AND timestamp <= ?",
            (pre_window_start, pre_window_end),
        ).fetchone()
        pre_total_count = pre_total_row[0]

        pre_rate = pre_type_count / pre_total_count if pre_total_count > 0 else 0.0

        # correction_decay_rate = (pre_rate - post_rate) / pre_rate
        if pre_rate > 0:
            correction_decay_rate = (pre_rate - error_rate) / pre_rate
        else:
            correction_decay_rate = 0.0

        # adaptation_speed: count distinct sessions between rule application
        # and when the error rate dropped below a threshold.
        # We define the threshold as 50% of the pre-application rate.
        threshold = pre_rate * 0.5 if pre_rate > 0 else 0.0

        # Get distinct sessions after the rule was applied, ordered by time
        session_rows = db.execute(
            "SELECT DISTINCT session_id FROM error_records WHERE timestamp > ? ORDER BY timestamp",
            (applied_at,),
        ).fetchall()

        if threshold > 0 and session_rows:
            sessions_checked = 0
            cumulative_type_count = 0
            cumulative_total_count = 0

            for srow in session_rows:
                sid = srow[0]
                sessions_checked += 1

                # Count errors in this session
                s_type = db.execute(
                    "SELECT COUNT(*) FROM error_records WHERE session_id = ? AND error_type = ?",
                    (sid, error_type),
                ).fetchone()[0]
                s_total = db.execute(
                    "SELECT COUNT(*) FROM error_records WHERE session_id = ?",
                    (sid,),
                ).fetchone()[0]

                cumulative_type_count += s_type
                cumulative_total_count += s_total

                running_rate = (
                    cumulative_type_count / cumulative_total_count
                    if cumulative_total_count > 0
                    else 0.0
                )

                if running_rate <= threshold:
                    adaptation_speed = sessions_checked
                    break

            # If we never dropped below threshold, report total sessions checked
            if adaptation_speed is None:
                adaptation_speed = sessions_checked

    # Get session_id for this snapshot (most recent session in window)
    latest_session_row = db.execute(
        "SELECT session_id FROM error_records "
        "WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (window_start, window_end),
    ).fetchone()
    session_id = latest_session_row[0] if latest_session_row else "no-session"

    created_at = now.isoformat()

    # Persist to velocity_snapshots
    db.execute(
        "INSERT INTO velocity_snapshots "
        "(error_type, session_id, error_rate, error_count_in_window, "
        "window_start, window_end, rule_applied, rule_suggestion_id, "
        "created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            error_type,
            session_id,
            error_rate,
            error_count_in_window,
            window_start,
            window_end,
            1 if rule_applied else 0,
            rule_suggestion_id,
            created_at,
        ),
    )
    db.commit()

    return {
        "error_type": error_type,
        "error_rate": error_rate,
        "error_count_in_window": error_count_in_window,
        "correction_decay_rate": correction_decay_rate,
        "adaptation_speed": adaptation_speed,
        "rule_applied": rule_applied,
        "rule_suggestion_id": rule_suggestion_id,
        "window_start": window_start,
        "window_end": window_end,
        "created_at": created_at,
    }


def compute_per_rule_velocity(
    db: sqlite3.Connection,
    window_days: int = 30,
    min_after: int = 10,
) -> list[dict]:
    """Compute per-rule error-rate attribution from error_records.active_rules.

    T1.L.3 (PRD sio_backend_dead_loop_2026-05-15). For each distinct rule
    id appearing in ``error_records.active_rules``, compute:

    * ``records_with_rule`` — count of errors where the JSON array
      contains this rule id (the "AFTER rule landed" sample)
    * ``records_without_rule`` — count of errors where the JSON array
      exists but does NOT contain this rule id (the "BEFORE" sample)
    * ``rate_with`` / ``rate_without`` — normalised over each sub-sample
    * ``delta_pct`` — relative change ``(with - without) / without``
    * ``sign`` — ``"good"`` if delta < 0 (errors decreased), ``"bad"`` if > 0

    Rules with fewer than ``min_after`` records-with-rule are excluded —
    statistical noise.

    NOTE: this is a PROXY for true rule effectiveness. ``active_rules`` is
    stamped at mine time, not at session-start time, so a rule edited
    between session-time and mine-time will appear "active" for both
    pre- and post-edit errors. T1.L (future) could use session-start
    rule snapshots from the SessionStart hook for a tighter signal.
    """
    import json  # noqa: PLC0415

    # Pull all (active_rules, error_type) pairs from records that have
    # the column populated. JSON parsing happens in Python — sqlite's
    # json_each is available but optional, and a few-thousand-row
    # in-memory tally is fine.
    rows = db.execute(
        "SELECT active_rules, error_type FROM error_records "
        "WHERE active_rules IS NOT NULL AND active_rules != ''"
    ).fetchall()

    if not rows:
        return []

    # rule_id -> {with_count, with_types_counter}
    # universe of "without" = total_records - with_count for that rule_id
    from collections import Counter  # noqa: PLC0415

    total_records = len(rows)
    rule_with_count: Counter = Counter()
    rule_with_by_type: dict[str, Counter] = {}
    total_by_type: Counter = Counter()
    seen_rules: set[str] = set()

    for r in rows:
        ar_json = r[0]
        et = r[1] or ""
        total_by_type[et] += 1
        try:
            rule_ids = json.loads(ar_json)
            if not isinstance(rule_ids, list):
                continue
        except Exception:
            continue
        for rid in rule_ids:
            seen_rules.add(rid)
            rule_with_count[rid] += 1
            rule_with_by_type.setdefault(rid, Counter())[et] += 1

    results: list[dict] = []
    for rid in seen_rules:
        with_n = rule_with_count[rid]
        without_n = total_records - with_n
        if with_n < min_after or without_n == 0:
            continue
        # By-type breakdown
        with_types = rule_with_by_type.get(rid, Counter())
        type_deltas: list[dict] = []
        for etype, tot in total_by_type.most_common():
            with_t = with_types.get(etype, 0)
            without_t = tot - with_t
            if with_n == 0 or without_n == 0:
                continue
            rate_with = with_t / with_n
            rate_without = without_t / without_n
            if rate_without == 0:
                delta_pct = None
            else:
                delta_pct = ((rate_with - rate_without) / rate_without) * 100
            type_deltas.append({
                "error_type": etype,
                "with_count": with_t,
                "without_count": without_t,
                "rate_with": rate_with,
                "rate_without": rate_without,
                "delta_pct": delta_pct,
            })
        # Overall aggregate rate is meaningless (records-with-rule ratio); we
        # surface per-type deltas instead. Mark the rule by its best/worst
        # type delta.
        meaningful = [d for d in type_deltas if d["delta_pct"] is not None]
        best = min(meaningful, key=lambda d: d["delta_pct"]) if meaningful else None
        worst = max(meaningful, key=lambda d: d["delta_pct"]) if meaningful else None
        results.append({
            "rule_id": rid,
            "with_count": with_n,
            "without_count": without_n,
            "by_type": type_deltas,
            "best_delta": best,
            "worst_delta": worst,
        })

    # Sort by "best" delta — biggest negative (most error-suppressing) first
    results.sort(
        key=lambda r: (
            r["best_delta"]["delta_pct"] if r["best_delta"] else 0.0
        )
    )
    return results


def _confidence_tier(n_after: int) -> str:
    """Sample-size based confidence tier (per PRD §3 Surface 1)."""
    if n_after >= 100:
        return "high"
    if n_after >= 25:
        return "medium"
    return "low"


def _recommend_text(delta_pct: float | None, tier: str, n_after: int) -> str:
    """Rule-of-thumb recommendation text — NEVER an action."""
    if n_after < 10 or delta_pct is None:
        return "needs more data"
    if tier == "low":
        return "needs more data"
    if delta_pct <= -20:
        return "looks fine"
    if delta_pct >= 10:
        return "look at this"
    return "no clear signal"


def compute_rule_outcomes(
    db: sqlite3.Connection,
    rule_id_filter: str | None = None,
    window_days: int = 7,
) -> list[dict]:
    """Per-(rule_id, error_type, target_surface) outcome breakdown.

    Implements Tier 1 of PRD ``sio_rule_outcomes_audit_2026-05-18.md``.

    For each rule_id present in ``error_records.active_rules`` (optionally
    filtered to one), compute:
      * ``first_seen``: min(timestamp) where rule_id appears in active_rules
      * ``n_before`` / ``n_after``: errors in [first_seen - window_days,
        first_seen) vs [first_seen, first_seen + window_days)
      * ``delta_pct``: (n_after - n_before) / n_before * 100, broken down
        per (error_type, target_surface)
      * ``confidence``: low/medium/high tier from sample size
      * ``recommend``: text hint
      * ``target_surface``: derived from rule_id's file path
    """
    import json  # noqa: PLC0415

    where = "WHERE active_rules IS NOT NULL AND active_rules != ''"
    rows = db.execute(
        f"SELECT timestamp, active_rules, error_type, source_file "
        f"FROM error_records {where}"
    ).fetchall()
    if not rows:
        return []

    # Build per-rule first-seen + by-(error_type, target_surface) tallies.
    # target_surface = top-level rule file (e.g. "tools/gemini.md").
    rule_first_seen: dict[str, str] = {}
    rule_events: dict[str, list[tuple[str, str]]] = {}  # rule_id -> [(ts, etype)]
    rule_target_surface: dict[str, str] = {}

    for r in rows:
        ts = r[0] or ""
        ar_json = r[1]
        etype = r[2] or ""
        try:
            rule_ids = json.loads(ar_json)
            if not isinstance(rule_ids, list):
                continue
        except Exception:
            continue
        for rid in rule_ids:
            if rule_id_filter and rid != rule_id_filter:
                continue
            cur = rule_first_seen.get(rid)
            if cur is None or ts < cur:
                rule_first_seen[rid] = ts
            rule_events.setdefault(rid, []).append((ts, etype))
            if rid not in rule_target_surface:
                # rule_id format: "<path>#<sha>"
                rule_target_surface[rid] = rid.split("#", 1)[0]

    # For each rule, count errors in the pre/post windows.
    results: list[dict] = []
    for rid, first_seen in rule_first_seen.items():
        if not first_seen:
            continue
        try:
            ts0 = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        except Exception:
            continue
        pre_start = (ts0 - timedelta(days=window_days)).isoformat()
        pre_end = first_seen
        post_start = first_seen
        post_end = (ts0 + timedelta(days=window_days)).isoformat()

        # Per (error_type, target_surface) — surface is fixed for the rule.
        target_surface = rule_target_surface[rid]
        by_type_before: dict[str, int] = {}
        by_type_after: dict[str, int] = {}

        before_rows = db.execute(
            "SELECT error_type FROM error_records "
            "WHERE timestamp >= ? AND timestamp < ?",
            (pre_start, pre_end),
        ).fetchall()
        for br in before_rows:
            et = br[0] or ""
            by_type_before[et] = by_type_before.get(et, 0) + 1

        after_rows = db.execute(
            "SELECT error_type FROM error_records "
            "WHERE timestamp >= ? AND timestamp <= ?",
            (post_start, post_end),
        ).fetchall()
        for ar in after_rows:
            et = ar[0] or ""
            by_type_after[et] = by_type_after.get(et, 0) + 1

        all_types = set(by_type_before) | set(by_type_after)
        breakdowns: list[dict] = []
        for et in sorted(all_types):
            nb = by_type_before.get(et, 0)
            na = by_type_after.get(et, 0)
            if nb == 0:
                delta_pct = None
            else:
                delta_pct = ((na - nb) / nb) * 100
            tier = _confidence_tier(na)
            breakdowns.append({
                "rule_id": rid,
                "error_type": et,
                "target_surface": target_surface,
                "n_before": nb,
                "n_after": na,
                "delta_pct": delta_pct,
                "confidence": tier,
                "recommend": _recommend_text(delta_pct, tier, na),
            })

        n_before_total = sum(by_type_before.values())
        n_after_total = sum(by_type_after.values())
        if n_before_total > 0:
            agg_delta = ((n_after_total - n_before_total) / n_before_total) * 100
        else:
            agg_delta = None
        agg_tier = _confidence_tier(n_after_total)

        # Related-rules detection: any rule_id active in records with same
        # error_type within the post window — possible confound.
        related: set[str] = set()
        for ts, _et in rule_events.get(rid, []):
            if ts < first_seen:
                continue
        # Sibling: rules that co-appear in active_rules JSON for the same
        # error rows in the post window.
        sibling_rows = db.execute(
            "SELECT active_rules FROM error_records "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "AND active_rules IS NOT NULL AND active_rules != ''",
            (post_start, post_end),
        ).fetchall()
        for sr in sibling_rows:
            try:
                sibs = json.loads(sr[0])
            except Exception:
                continue
            if rid not in sibs:
                continue
            for s in sibs:
                if s != rid:
                    related.add(s)

        results.append({
            "rule_id": rid,
            "target_surface": target_surface,
            "first_seen": first_seen,
            "window_days": window_days,
            "n_before_total": n_before_total,
            "n_after_total": n_after_total,
            "delta_pct_total": agg_delta,
            "confidence_total": agg_tier,
            "recommend_total": _recommend_text(agg_delta, agg_tier, n_after_total),
            "by_type": breakdowns,
            "related_rules": sorted(related),
        })

    # Sort: lowest (most-negative) aggregate delta first.
    results.sort(
        key=lambda r: (
            r["delta_pct_total"] if r["delta_pct_total"] is not None else 0.0
        )
    )
    return results


def sample_errors_around_rule(
    db: sqlite3.Connection,
    rule_id: str,
    n_samples: int = 10,
    window_days: int = 7,
) -> dict:
    """Return ``n_samples`` error rows from before & after rule first-seen.

    Used by ``sio rule-audit`` to display representative evidence. Sampling
    is deterministic — orders by timestamp, takes evenly-spaced rows so
    repeated audits surface the same evidence.
    """
    import json  # noqa: PLC0415

    rows = db.execute(
        "SELECT timestamp, active_rules FROM error_records "
        "WHERE active_rules IS NOT NULL AND active_rules != '' "
        "ORDER BY timestamp ASC"
    ).fetchall()
    first_seen: str | None = None
    for r in rows:
        try:
            ids = json.loads(r[1])
        except Exception:
            continue
        if rule_id in ids:
            first_seen = r[0]
            break
    if not first_seen:
        return {"rule_id": rule_id, "first_seen": None, "before": [], "after": []}
    try:
        ts0 = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
    except Exception:
        return {"rule_id": rule_id, "first_seen": first_seen, "before": [], "after": []}

    pre_start = (ts0 - timedelta(days=window_days)).isoformat()
    post_end = (ts0 + timedelta(days=window_days)).isoformat()

    before = db.execute(
        "SELECT id, timestamp, session_id, error_type, error_text "
        "FROM error_records "
        "WHERE timestamp >= ? AND timestamp < ? "
        "ORDER BY timestamp ASC",
        (pre_start, first_seen),
    ).fetchall()
    after = db.execute(
        "SELECT id, timestamp, session_id, error_type, error_text "
        "FROM error_records "
        "WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC",
        (first_seen, post_end),
    ).fetchall()

    def _evenly(rows_in, n):
        if not rows_in:
            return []
        if len(rows_in) <= n:
            return [dict(zip(
                ["id", "timestamp", "session_id", "error_type", "error_text"], r
            )) for r in rows_in]
        step = len(rows_in) // n
        out = [rows_in[i * step] for i in range(n)]
        return [dict(zip(
            ["id", "timestamp", "session_id", "error_type", "error_text"], r
        )) for r in out]

    return {
        "rule_id": rule_id,
        "first_seen": first_seen,
        "before": _evenly(before, n_samples),
        "after": _evenly(after, n_samples),
    }


def get_velocity_trends(
    db: sqlite3.Connection,
    error_type: str | None = None,
) -> list[dict]:
    """Retrieve velocity snapshots ordered by time.

    Args:
        db: Open sqlite3.Connection with SIO schema.
        error_type: If given, filter to this error type only.

    Returns:
        List of snapshot dicts ordered by created_at ascending.
    """
    if error_type:
        rows = db.execute(
            "SELECT * FROM velocity_snapshots WHERE error_type = ? ORDER BY created_at",
            (error_type,),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM velocity_snapshots ORDER BY created_at").fetchall()

    return [_row_to_dict(r) for r in rows]


def get_skill_effectiveness(
    db: sqlite3.Connection,
) -> list[dict]:
    """Measure effectiveness of suggestions that have been promoted to skills.

    Joins suggestions (where ``skill_file_path IS NOT NULL``) with
    velocity_snapshots to compute per-skill improvement metrics.

    For each skill-linked suggestion, finds the error_type from the linked
    pattern, then compares pre-rule and post-rule error rates from the
    velocity snapshot history.

    Parameters
    ----------
    db:
        Open sqlite3.Connection with SIO schema.

    Returns
    -------
    list[dict]
        Per-skill effectiveness records, each containing:
        ``skill_path``, ``target_error_type``, ``pre_rate``, ``post_rate``,
        ``improvement_pct``, ``sessions_tracked``.
    """
    # Find all suggestions that have a skill file path and have been applied
    skill_rows = db.execute(
        """
        SELECT
            s.id as suggestion_id,
            s.skill_file_path,
            s.description,
            p.tool_name,
            er_types.error_type
        FROM suggestions s
        LEFT JOIN patterns p ON p.id = s.pattern_id
        LEFT JOIN (
            SELECT pe.pattern_id,
                   er.error_type,
                   COUNT(*) as cnt
            FROM pattern_errors pe
            JOIN error_records er ON er.id = pe.error_id
            GROUP BY pe.pattern_id, er.error_type
            ORDER BY cnt DESC
        ) er_types ON er_types.pattern_id = s.pattern_id
        WHERE s.skill_file_path IS NOT NULL
          AND s.skill_file_path != ''
        GROUP BY s.id
        """
    ).fetchall()

    results: list[dict] = []

    for row in skill_rows:
        rd = dict(row)
        skill_path = rd["skill_file_path"]
        suggestion_id = rd["suggestion_id"]
        error_type = rd.get("error_type")

        if not error_type:
            # Try to infer error_type from suggestion description
            desc = rd.get("description") or ""
            # Use a simple heuristic: look for common error type names
            for candidate in (
                "tool_failure",
                "user_correction",
                "repeated_attempt",
                "undo",
                "agent_admission",
            ):
                if candidate.replace("_", " ") in desc.lower() or candidate in desc.lower():
                    error_type = candidate
                    break

        if not error_type:
            # Cannot measure effectiveness without an error type
            results.append(
                {
                    "skill_path": skill_path,
                    "target_error_type": None,
                    "pre_rate": None,
                    "post_rate": None,
                    "improvement_pct": None,
                    "sessions_tracked": 0,
                }
            )
            continue

        # Find velocity snapshots for this error type, linked to this suggestion
        snapshots = db.execute(
            """
            SELECT error_rate, error_count_in_window, rule_applied,
                   rule_suggestion_id, created_at
            FROM velocity_snapshots
            WHERE error_type = ?
            ORDER BY created_at
            """,
            (error_type,),
        ).fetchall()

        if not snapshots:
            results.append(
                {
                    "skill_path": skill_path,
                    "target_error_type": error_type,
                    "pre_rate": None,
                    "post_rate": None,
                    "improvement_pct": None,
                    "sessions_tracked": 0,
                }
            )
            continue

        snap_dicts = [dict(s) for s in snapshots]

        # Split into pre-rule and post-rule snapshots
        # Find the first snapshot where rule_suggestion_id matches
        rule_idx = None
        for i, s in enumerate(snap_dicts):
            if s["rule_suggestion_id"] == suggestion_id and s["rule_applied"]:
                rule_idx = i
                break

        if rule_idx is not None and rule_idx > 0:
            # Pre-rule: average rate of snapshots before the rule
            pre_snapshots = snap_dicts[:rule_idx]
            pre_rate = sum(s["error_rate"] for s in pre_snapshots) / len(pre_snapshots)

            # Post-rule: average rate of snapshots after the rule
            post_snapshots = snap_dicts[rule_idx:]
            post_rate = sum(s["error_rate"] for s in post_snapshots) / len(post_snapshots)

            # Improvement percentage
            if pre_rate > 0:
                improvement_pct = ((pre_rate - post_rate) / pre_rate) * 100
            else:
                improvement_pct = 0.0

            sessions_tracked = len(snap_dicts)
        else:
            # No clear pre/post split; use first and last snapshots
            pre_rate = snap_dicts[0]["error_rate"]
            post_rate = snap_dicts[-1]["error_rate"]

            if pre_rate > 0 and len(snap_dicts) > 1:
                improvement_pct = ((pre_rate - post_rate) / pre_rate) * 100
            else:
                improvement_pct = 0.0

            sessions_tracked = len(snap_dicts)

        results.append(
            {
                "skill_path": skill_path,
                "target_error_type": error_type,
                "pre_rate": round(pre_rate, 4),
                "post_rate": round(post_rate, 4),
                "improvement_pct": round(improvement_pct, 1),
                "sessions_tracked": sessions_tracked,
            }
        )

    return results
