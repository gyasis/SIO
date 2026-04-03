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
    """Extract session ID from JSONL file path."""
    return path.stem


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

    for file_path in files:
        try:
            parsed = parse_jsonl(file_path)
            if not parsed:
                continue

            session_id = _session_id_from_path(file_path)
            flow_data = extract_flows_from_session(parsed)

            if not flow_data["ngrams"]:
                continue

            # Check which tool indices are near success signals
            success_indices = flow_data["success_indices"]
            tool_seq = flow_data["tool_sequence"]

            # Build mapping: compressed position -> tool_sequence indices
            comp_to_tool = compressed_to_tool_indices(tool_seq)

            # Use indexed_ngrams to get (ngram, start_position) pairs
            ngrams_with_pos = indexed_ngrams(flow_data["compressed"])

            # Insert each n-gram as a flow_event
            for ngram, comp_start in ngrams_with_pos:
                seq_str = " → ".join(ngram)
                flow_hash = hashlib.sha256(seq_str.encode()).hexdigest()[:16]

                # BUG 1 FIX: Check if this specific ngram's tool indices
                # overlap with success_indices (message indices near success
                # markers), not just whether the session had any success.
                ngram_tool_indices: set[int] = set()
                for comp_pos in range(comp_start, comp_start + len(ngram)):
                    if comp_pos < len(comp_to_tool):
                        for tool_idx in comp_to_tool[comp_pos]:
                            # Get the message-level index from the tool_seq
                            ngram_tool_indices.add(tool_seq[tool_idx]["idx"])

                was_successful = (
                    1 if ngram_tool_indices & success_indices else 0
                )

                # BUG 2 FIX: Use the timestamp of this ngram's first tool,
                # not the first tool in the entire session.
                ts = mined_at
                if comp_start < len(comp_to_tool) and comp_to_tool[comp_start]:
                    first_tool_idx = comp_to_tool[comp_start][0]
                    ts = tool_seq[first_tool_idx].get("timestamp") or mined_at

                db_conn.execute(
                    """INSERT INTO flow_events
                       (session_id, flow_hash, sequence, ngram_size,
                        was_successful, duration_seconds, source_file,
                        timestamp, mined_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        flow_hash,
                        seq_str,
                        len(ngram),
                        was_successful,
                        flow_data["duration_seconds"] / max(len(flow_data["ngrams"]), 1),
                        str(file_path),
                        ts,
                        mined_at,
                    ),
                )
                total_flow_events += 1

        except Exception as e:
            logger.warning("Flow extraction failed for %s: %s", file_path, e)
            continue

    db_conn.commit()
    return {
        "total_files_scanned": len(files),
        "flows_found": total_flow_events,
    }


def query_flows(
    db_conn: sqlite3.Connection,
    since: str | None = None,
    min_count: int = 3,
    limit: int = 20,
) -> list[dict]:
    """Query aggregated flows sorted by confidence.

    Returns list of dicts:
        {sequence, count, success_count, success_rate, avg_duration,
         confidence, ngram_size, last_seen, session_count}
    """
    where_clause = ""
    params: list = []

    if since:
        # Parse "N days" style into a date
        from sio.mining.time_filter import parse_since
        cutoff = parse_since(since)
        if cutoff:
            where_clause = "WHERE fe.timestamp >= ?"
            params.append(cutoff.isoformat())

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

        if count >= 10 and success_rate >= 80:
            confidence = "HIGH"
        elif count >= 5 and success_rate >= 60:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        results.append({
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
        })

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
