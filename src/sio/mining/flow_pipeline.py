"""Flow mining pipeline — discovers positive tool sequence patterns.

Public API
----------
    run_flow_mine(db_conn, source_dirs, since, source_type, project) -> dict
        Mine sessions for tool flow patterns and store in flow_events.

    query_flows(db_conn, since=None, min_count=3, limit=20) -> list[dict]
        Query aggregated flows from the database.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sio.core.session_handle import from_path
from sio.mining.flow_extractor import (
    compressed_to_tool_indices,
    extract_flows_from_session,
    indexed_ngrams,
)
from sio.mining.jsonl_parser import parse_jsonl
from sio.mining.time_filter import filter_files

logger = logging.getLogger(__name__)


def _collect_jsonl_files(source_dirs: list[Path], since: str, project: str | None) -> list[Path]:
    """Collect JSONL files from source directories."""
    all_files = []
    for d in source_dirs:
        if d.exists():
            all_files.extend(d.rglob("*.jsonl"))

    # Filter by time
    filtered = filter_files(all_files, since)

    # Filter by project
    if project:
        filtered = [f for f in filtered if project.lower() in str(f).lower()]

    return filtered


def _session_id_from_path(path: Path) -> str:
    """Map a session JSONL path to a canonical ``agent:native_id`` handle.

    This returned ``path.stem`` -- the bare native id, carrying no agent.
    Flow mining only walks ``~/.claude/projects`` today (``sio flows`` in
    cli/main.py builds that one source dir), so every stored value was
    correct-for-claude and nothing misread it: ``session_match_clause``
    matches a claude handle in BOTH forms.

    The cost was latent rather than live.  A stem cannot say WHICH agent
    produced it, so the moment flow mining gains a second source directory,
    two agents' sessions that happen to share a stem merge into one id with
    no error raised.  ``from_path`` infers the agent from the path location
    and is the same helper ``--session`` already resolves through, so the
    write path and the read path now agree by construction.
    """
    return from_path(str(path))


def _store_session_flows(
    db_conn: sqlite3.Connection,
    parsed: list[dict],
    session_id: str,
    file_path: str,
    agent: str,
    mined_at: str,
) -> int:
    """Extract tool-flow n-grams from one session and insert them into flow_events.

    Returns the number of n-grams written. Dedup is the table's
    UNIQUE(file_path, session_id, flow_hash), via INSERT OR IGNORE.
    """
    flow_data = extract_flows_from_session(parsed)
    if not flow_data["ngrams"]:
        return 0

    # Message indices near success markers, and compressed position -> tool indices.
    success_indices = flow_data["success_indices"]
    tool_seq = flow_data["tool_sequence"]
    comp_to_tool = compressed_to_tool_indices(tool_seq)
    ngrams_with_pos = indexed_ngrams(flow_data["compressed"])

    written = 0
    for ngram, comp_start in ngrams_with_pos:
        seq_str = " → ".join(ngram)
        flow_hash = hashlib.sha256(seq_str.encode()).hexdigest()[:16]

        # An n-gram succeeded when ITS tool indices overlap a success marker.
        ngram_tool_indices: set[int] = set()
        for comp_pos in range(comp_start, comp_start + len(ngram)):
            if comp_pos < len(comp_to_tool):
                for tool_idx in comp_to_tool[comp_pos]:
                    ngram_tool_indices.add(tool_seq[tool_idx]["idx"])
        was_successful = 1 if ngram_tool_indices & success_indices else 0

        # Timestamp of this n-gram's first tool.
        ts = mined_at
        if comp_start < len(comp_to_tool) and comp_to_tool[comp_start]:
            first_tool_idx = comp_to_tool[comp_start][0]
            ts = tool_seq[first_tool_idx].get("timestamp") or mined_at

        db_conn.execute(
            """INSERT OR IGNORE INTO flow_events
               (session_id, flow_hash, sequence, ngram_size,
                was_successful, duration_seconds, source_file,
                file_path, timestamp, mined_at, agent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                flow_hash,
                seq_str,
                len(ngram),
                was_successful,
                flow_data["duration_seconds"] / max(len(flow_data["ngrams"]), 1),
                file_path,
                file_path,
                ts,
                mined_at,
                agent,
            ),
        )
        written += 1
    return written


def run_flow_mine_agent(
    db_conn: sqlite3.Connection,
    agent: str,
    since: str | None,
    project: str | None = None,
) -> dict:
    """Mine tool-flow patterns from one non-Claude harness's sessions.

    Sessions are listed from the harness store and loaded through its adapter
    (``sio.cli.session_source``), then stored like Claude flows with the
    canonical ``<agent>:<native_id>`` session id. A session is skipped when
    its listing mtime is unchanged since the last flow mine. A session that
    cannot be loaded is a recorded failure, never a silent skip.
    """
    from sio.cli.session_source import (  # noqa: PLC0415
        SessionLoadError,
        _latest_candidates,
        load_session_messages,
    )
    from sio.mining.pipeline import _get_session_state, _update_session_state  # noqa: PLC0415

    cutoff = None
    if since:
        from sio.mining.time_filter import parse_since  # noqa: PLC0415

        cutoff = parse_since(since).timestamp()

    mined_at = datetime.now(timezone.utc).isoformat()
    scanned = flows = 0
    failures: list[dict] = []
    for mtime, target, text in _latest_candidates(agent):
        if cutoff is not None and mtime < cutoff:
            continue
        if project and project.lower() not in text.lower():
            continue
        scanned += 1
        state_key = f"flows:{agent}:{target}"
        state = _get_session_state(db_conn, state_key)
        if state["last_mtime"] is not None and abs(state["last_mtime"] - mtime) < 1.0:
            continue
        try:
            messages, label = load_session_messages(target)
        except SessionLoadError as exc:
            failures.append({"file": target, "stage": "load", "error": str(exc)})
            continue
        try:
            flows += _store_session_flows(db_conn, messages, label, target, agent, mined_at)
            _update_session_state(db_conn, state_key, len(messages), mtime, agent=agent)
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {"file": target, "stage": "extract", "error": f"{type(exc).__name__}: {exc}"}
            )
    db_conn.commit()
    if failures:
        from sio.core.observability import log_failure  # noqa: PLC0415

        for f in failures:
            log_failure(
                "flow_failures", f["file"], f["error"],
                stage=f.get("stage"), extra={"mined_at": mined_at, "agent": agent},
            )
    return {
        "total_files_scanned": scanned,
        "flows_found": flows,
        "failed_files": len(failures),
        "failures": failures,
    }


def run_flow_mine(
    db_conn: sqlite3.Connection,
    source_dirs: list[Path],
    since: str,
    source_type: str = "jsonl",
    project: str | None = None,
) -> dict:
    """Mine sessions for tool flow patterns.

    Returns summary dict with total_files_scanned, flows_found.
    """
    files = _collect_jsonl_files(source_dirs, since, project)
    mined_at = datetime.now(timezone.utc).isoformat()
    total_flow_events = 0
    # Observability: collect per-file failures so the caller (and user) can see them,
    # not just a silent logger.warning stream. No more silent errors — all failures
    # are returned and also mirrored to ~/.sio/logs/flow_failures.log.
    failures: list[dict] = []

    for file_path in files:
        try:
            # T087 [US5] — FR-008: Honor processed_sessions for dedup
            from sio.mining.pipeline import (  # noqa: PLC0415
                _get_session_state,
                _update_session_state,
            )

            file_str = str(file_path)
            try:
                current_size = file_path.stat().st_size
                current_mtime = file_path.stat().st_mtime
            except OSError as e:
                logger.warning("Flow mine: file not accessible %s", file_str)
                failures.append({
                    "file": file_str,
                    "stage": "stat",
                    "error": f"{type(e).__name__}: {e}",
                })
                continue

            session_state = _get_session_state(db_conn, file_str)

            # Skip if file is unchanged (same size and mtime has not progressed)
            if (
                session_state["last_offset"] >= current_size
                and session_state["last_mtime"] is not None
                and abs(session_state["last_mtime"] - current_mtime) < 1.0
            ):
                logger.debug("Flow mine: skipping unchanged file %s", file_str)
                continue

            parsed = parse_jsonl(file_path)
            if not parsed:
                continue

            session_id = _session_id_from_path(file_path)
            total_flow_events += _store_session_flows(
                db_conn, parsed, session_id, str(file_path), "claude", mined_at
            )

            # T087 [US5]: Update processed_sessions after successful file mine
            _update_session_state(db_conn, file_str, current_size, current_mtime)

        except Exception as e:
            logger.warning("Flow extraction failed for %s: %s", file_path, e)
            failures.append({
                "file": str(file_path),
                "stage": "extract",
                "error": f"{type(e).__name__}: {e}",
            })
            continue

    db_conn.commit()

    # Observability: persist failures via central module so a silent warning
    # never swallows a systemic bug (cf. the message_count regression).
    if failures:
        from sio.core.observability import log_failure  # noqa: PLC0415
        for f in failures:
            log_failure(
                "flow_failures",
                f["file"],
                f["error"],
                stage=f.get("stage"),
                extra={"mined_at": mined_at},
            )

    return {
        "total_files_scanned": len(files),
        "flows_found": total_flow_events,
        "failed_files": len(failures),
        "failures": failures,
    }


def query_flows(
    db_conn: sqlite3.Connection,
    since: str | None = None,
    min_count: int = 3,
    limit: int = 20,
    until: str | None = None,
    agent: str | None = None,
) -> list[dict]:
    """Query aggregated flows sorted by confidence.

    Args:
        until: optional ISO-8601 upper bound on ``fe.timestamp``. Used by
            ``--experiment`` scoping so a closed cohort doesn't count flows
            that occurred after its close timestamp.
        agent: only this harness's flows; None means every harness.

    Returns list of dicts:
        {sequence, count, success_count, success_rate, avg_duration,
         confidence, ngram_size, last_seen, session_count}
    """
    conds: list[str] = []
    params: list = []

    if since:
        # Parse "N days" style into a date
        from sio.mining.time_filter import parse_since

        cutoff = parse_since(since)
        if cutoff:
            conds.append("fe.timestamp >= ?")
            params.append(cutoff.isoformat())

    if until:
        conds.append("fe.timestamp <= ?")
        params.append(until)

    if agent:
        conds.append("fe.agent = ?")
        params.append(agent)

    where_clause = ("WHERE " + " AND ".join(conds)) if conds else ""

    sql = f"""
        SELECT
            fe.sequence,
            fe.flow_hash,
            fe.ngram_size,
            COUNT(*) as count,
            SUM(fe.was_successful) as success_count,
            ROUND(CAST(SUM(fe.was_successful) AS REAL) / COUNT(*) * 100, 1) as success_rate,
            ROUND(AVG(fe.duration_seconds), 1) as avg_duration,
            COUNT(DISTINCT fe.session_id) as session_count,
            MAX(fe.timestamp) as last_seen
        FROM flow_events fe
        {where_clause}
        GROUP BY fe.flow_hash
        HAVING COUNT(*) >= ?
        ORDER BY COUNT(*) * (CAST(SUM(fe.was_successful) AS REAL) / COUNT(*)) DESC
        LIMIT ?
    """
    params.extend([min_count, limit])

    rows = db_conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        count = row["count"]
        success_rate = row["success_rate"]

        # Confidence tiers (PRD T4.2). Thresholds of 80%/60% on success rate
        # let NO flow reach HIGH or MEDIUM, because they assume a user says
        # "great" or "perfect" after most flows. Users just move on
        # instead. Empirical floor: a successful flow run rarely
        # exceeds 50% explicit-success-signal rate. Re-tuned thresholds:
        #   HIGH:   count ≥ 20  AND success_rate ≥ 40%
        #   MEDIUM: count ≥ 10  AND success_rate ≥ 20%
        #   LOW:    everything else
        # Override via env: SIO_FLOW_CONFIDENCE_HIGH="count,rate" etc.
        import os as _os  # noqa: PLC0415

        def _parse_tier(env_var: str, default: tuple) -> tuple:
            raw = _os.environ.get(env_var, "")
            if not raw:
                return default
            try:
                c_str, r_str = raw.split(",", 1)
                return (int(c_str.strip()), float(r_str.strip()))
            except Exception:
                return default

        high_c, high_r = _parse_tier("SIO_FLOW_CONFIDENCE_HIGH", (20, 40.0))
        med_c, med_r = _parse_tier("SIO_FLOW_CONFIDENCE_MEDIUM", (10, 20.0))
        if count >= high_c and success_rate >= high_r:
            confidence = "HIGH"
        elif count >= med_c and success_rate >= med_r:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        results.append(
            {
                "sequence": row["sequence"],
                "flow_hash": row["flow_hash"],
                "ngram_size": row["ngram_size"],
                "count": count,
                "success_count": row["success_count"],
                "success_rate": success_rate,
                "avg_duration": row["avg_duration"],
                "confidence": confidence,
                "session_count": row["session_count"],
                "last_seen": row["last_seen"],
            }
        )

    return results


def get_promotable_flows(
    db_conn: sqlite3.Connection,
    min_sessions: int = 5,
) -> list[dict]:
    """Query flows that meet promotion thresholds.

    A flow is promotable when it has been observed across at least
    *min_sessions* distinct sessions AND its success rate exceeds 70%.

    Parameters
    ----------
    db_conn:
        An open sqlite3.Connection with the SIO schema.
    min_sessions:
        Minimum distinct session count required for promotion (default 5).

    Returns
    -------
    list[dict]
        List of flow dicts ready for promotion, each containing:
        ``flow_hash``, ``sequence``, ``count``, ``success_count``,
        ``success_rate``, ``avg_duration``, ``session_count``, ``ngram_size``,
        ``last_seen``.
    """
    sql = """
        SELECT
            fe.flow_hash,
            fe.sequence,
            fe.ngram_size,
            COUNT(*) as count,
            SUM(fe.was_successful) as success_count,
            ROUND(
                CAST(SUM(fe.was_successful) AS REAL) / COUNT(*) * 100, 1
            ) as success_rate,
            ROUND(AVG(fe.duration_seconds), 1) as avg_duration,
            COUNT(DISTINCT fe.session_id) as session_count,
            MAX(fe.timestamp) as last_seen
        FROM flow_events fe
        GROUP BY fe.flow_hash
        HAVING COUNT(DISTINCT fe.session_id) >= ?
           AND (CAST(SUM(fe.was_successful) AS REAL) / COUNT(*)) > 0.7
        ORDER BY COUNT(*) DESC
    """
    rows = db_conn.execute(sql, (min_sessions,)).fetchall()

    return [
        {
            "flow_hash": row["flow_hash"],
            "sequence": row["sequence"],
            "ngram_size": row["ngram_size"],
            "count": row["count"],
            "success_count": row["success_count"],
            "success_rate": row["success_rate"],
            "avg_duration": row["avg_duration"],
            "session_count": row["session_count"],
            "last_seen": row["last_seen"],
        }
        for row in rows
    ]
