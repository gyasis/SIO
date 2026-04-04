"""Dataset builder for SIO — exports structured training data for DSPy/ML.

Exports three task types from mined session data:
1. routing: (user_query, tool_choice) pairs for tool routing optimization
2. recovery: (error_context, fix_applied, success) triples for error recovery
3. flow: (current_state, next_tools) for sequence prediction

No LLM required — pure data extraction and formatting.

Public API
----------
    build_routing_dataset(db_conn, since=None) -> list[dict]
    build_recovery_dataset(db_conn, since=None) -> list[dict]
    build_flow_dataset(db_conn, since=None) -> list[dict]
    export_jsonl(records, output_path)
    export_parquet(records, output_path)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def _time_filter_sql(since: str | None) -> tuple[str, list]:
    """Build WHERE clause for time filtering."""
    if not since:
        return "", []
    from sio.mining.time_filter import parse_since
    cutoff = parse_since(since)
    if cutoff:
        return "AND timestamp >= ?", [cutoff.isoformat()]
    return "", []


def build_routing_dataset(
    db_conn: sqlite3.Connection,
    since: str | None = None,
) -> list[dict]:
    """Build (user_query, tool_choice, was_successful) training pairs.

    Extracts from error_records where we know the tool used and the user context.
    Also includes successful tool calls from flow_events.
    """
    time_clause, params = _time_filter_sql(since)

    # Get tool calls with user context from error_records
    sql = f"""
        SELECT
            user_message,
            tool_name,
            error_type,
            error_text,
            context_before,
            session_id,
            timestamp
        FROM error_records
        WHERE tool_name IS NOT NULL
          AND user_message IS NOT NULL
          AND user_message != ''
          {time_clause}
        ORDER BY timestamp DESC
    """
    rows = db_conn.execute(sql, params).fetchall()

    dataset = []
    for row in rows:
        was_successful = row["error_type"] not in ("tool_failure",)
        dataset.append({
            "inputs": {
                "user_query": row["user_message"][:500],  # Truncate long queries
                "context": (row["context_before"] or "")[:300],
            },
            "outputs": {
                "tool_choice": row["tool_name"],
                "was_successful": was_successful,
            },
            "metadata": {
                "session_id": row["session_id"],
                "timestamp": row["timestamp"],
                "error_type": row["error_type"],
                "task": "routing",
            },
        })

    return dataset


def build_recovery_dataset(
    db_conn: sqlite3.Connection,
    since: str | None = None,
) -> list[dict]:
    """Build (error_context, fix_applied, success) training triples.

    Finds tool_failure records and looks for the next successful action
    in the same session as the "fix".
    """
    time_clause, params = _time_filter_sql(since)

    # Get tool failures with context
    sql = f"""
        SELECT
            e1.error_text,
            e1.tool_name as failed_tool,
            e1.tool_input,
            e1.user_message,
            e1.context_after,
            e1.session_id,
            e1.timestamp,
            e1.id
        FROM error_records e1
        WHERE e1.error_type = 'tool_failure'
          AND e1.tool_name IS NOT NULL
          {time_clause}
        ORDER BY e1.session_id, e1.timestamp
    """
    failures = db_conn.execute(sql, params).fetchall()

    dataset = []
    for fail in failures:
        # Look for next record in same session that isn't a failure
        next_sql = """
            SELECT tool_name, error_type, tool_input
            FROM error_records
            WHERE session_id = ?
              AND id > ?
              AND error_type != 'tool_failure'
              AND tool_name IS NOT NULL
            ORDER BY id
            LIMIT 1
        """
        recovery = db_conn.execute(next_sql, (fail["session_id"], fail["id"])).fetchone()

        if recovery:
            dataset.append({
                "inputs": {
                    "error_message": (fail["error_text"] or "")[:500],
                    "failed_tool": fail["failed_tool"],
                    "tool_input": (fail["tool_input"] or "")[:300],
                    "user_context": (fail["user_message"] or "")[:300],
                },
                "outputs": {
                    "recovery_tool": recovery["tool_name"],
                    "recovery_input": (recovery["tool_input"] or "")[:300],
                    "was_successful": True,
                },
                "metadata": {
                    "session_id": fail["session_id"],
                    "timestamp": fail["timestamp"],
                    "task": "recovery",
                },
            })

    return dataset


def build_flow_dataset(
    db_conn: sqlite3.Connection,
    since: str | None = None,
    min_count: int = 3,
) -> list[dict]:
    """Build (current_state, next_tools) training pairs from flow_events.

    Uses discovered flows as training examples for sequence prediction.
    """
    time_clause, params = _time_filter_sql(since)

    # Get high-confidence flows
    sql = f"""
        SELECT
            sequence,
            flow_hash,
            COUNT(*) as count,
            SUM(was_successful) as success_count,
            ROUND(CAST(SUM(was_successful) AS REAL) / COUNT(*) * 100, 1) as success_rate,
            COUNT(DISTINCT session_id) as session_count
        FROM flow_events
        WHERE 1=1 {time_clause}
        GROUP BY flow_hash
        HAVING COUNT(*) >= ?
        ORDER BY COUNT(*) DESC
    """
    params.append(min_count)
    rows = db_conn.execute(sql, params).fetchall()

    dataset = []
    for row in rows:
        tools = row["sequence"].split(" → ")
        if len(tools) < 2:
            continue

        # Create training pair: given first N-1 tools, predict the last
        for split_point in range(1, len(tools)):
            dataset.append({
                "inputs": {
                    "current_tools": " → ".join(tools[:split_point]),
                    "current_tool_count": split_point,
                },
                "outputs": {
                    "next_tool": tools[split_point] if split_point < len(tools) else "",
                    "full_sequence": row["sequence"],
                    "confidence": row["success_rate"],
                },
                "metadata": {
                    "flow_hash": row["flow_hash"],
                    "occurrence_count": row["count"],
                    "success_rate": row["success_rate"],
                    "session_count": row["session_count"],
                    "task": "flow",
                },
            })

    return dataset


def export_jsonl(records: list[dict], output_path: str | Path) -> int:
    """Export records as JSONL (one JSON object per line).

    Returns number of records written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        for record in records:
            f.write(json.dumps(record, default=str) + "\n")

    return len(records)


def export_parquet(records: list[dict], output_path: str | Path) -> int:
    """Export records as Parquet using pandas.

    Returns number of records written. Falls back to JSONL if pandas unavailable.
    """
    try:
        import pandas as pd
    except ImportError:
        import click

        click.echo(
            "[WARNING] pandas not installed — writing JSONL instead of Parquet. "
            "Install with: pip install 'sio[parquet]' or pip install pandas",
            err=True,
        )
        logger.warning("pandas not installed, falling back to JSONL export")
        jsonl_path = str(output_path).replace(".parquet", ".jsonl")
        return export_jsonl(records, jsonl_path)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Flatten nested dicts for Parquet
    flat_records = []
    for r in records:
        flat = {}
        for key in ("inputs", "outputs", "metadata"):
            if key in r and isinstance(r[key], dict):
                for k, v in r[key].items():
                    flat[f"{key}_{k}"] = v
        flat_records.append(flat)

    df = pd.DataFrame(flat_records)
    df.to_parquet(output_path, index=False)
    return len(df)
