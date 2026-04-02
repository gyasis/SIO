"""Full mining pipeline for SIO v2 session error extraction.

Public API
----------
    run_mine(db_conn, source_dirs, since, source_type, project) -> dict

The pipeline:
1. Collects all .md and .jsonl files from source_dirs (recursively).
2. Filters by source_type ("specstory", "jsonl", or "both").
3. Filters by time window using filter_files(paths, since).
4. Optionally narrows to files whose path contains the project name substring.
5. For each surviving file:
   - .md  → parse_specstory -> flatten tool_calls -> extract_errors
   - .jsonl → parse_jsonl -> extract_errors
   - Any exception during parsing is logged as a warning; processing continues.
6. Each ErrorRecord is stored via insert_error_record.
7. Returns a summary dict with total_files_scanned, errors_found, error_records.

SpecStory flattening
--------------------
parse_specstory returns block dicts of the form::

    {"role": str, "content": str, "tool_calls": list[dict]}

where each tool_calls entry has: tool_name, tool_input, tool_output, error.

extract_errors expects flat message dicts with: role, content, tool_name,
tool_input, tool_output, error, and optionally timestamp/session_id.

For assistant blocks the flat stream is:
  1. The block itself (role=assistant, content=..., tool_name=None, error=None)
  2. One synthetic "tool" message per tool_calls entry carrying tool_name/error.

Human blocks are emitted as-is with None tool fields.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sio.core.db.queries import insert_error_record, insert_session_metrics
from sio.mining.error_extractor import extract_errors
from sio.mining.jsonl_parser import parse_jsonl
from sio.mining.specstory_parser import parse_specstory
from sio.mining.time_filter import filter_files

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _file_hash(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_already_processed(
    conn: sqlite3.Connection,
    file_path: Path,
    file_hash: str,
) -> bool:
    """Return True if (file_path, file_hash) already exists in processed_sessions."""
    row = conn.execute(
        "SELECT 1 FROM processed_sessions WHERE file_path = ? AND file_hash = ?",
        (str(file_path), file_hash),
    ).fetchone()
    return row is not None


def _mark_processed(
    conn: sqlite3.Connection,
    file_path: Path,
    file_hash: str,
    message_count: int,
    tool_call_count: int,
) -> None:
    """Insert a row into processed_sessions after successful mining."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO processed_sessions "
        "(file_path, file_hash, message_count, tool_call_count, skipped, mined_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (str(file_path), file_hash, message_count, tool_call_count, now),
    )
    conn.commit()


def _collect_files(
    source_dirs: list[Path],
    source_type: str,
) -> list[Path]:
    """Walk source_dirs and return all files matching the requested source_type.

    Parameters
    ----------
    source_dirs:
        Directories to search.  Non-existent directories are silently skipped.
    source_type:
        "specstory" — collect only .md files
        "jsonl"     — collect only .jsonl files
        "both"      — collect both .md and .jsonl files

    Returns
    -------
    list[Path]
        Unsorted list of matching file paths.
    """
    collected: list[Path] = []
    extensions: set[str]

    if source_type == "specstory":
        extensions = {".md"}
    elif source_type == "jsonl":
        extensions = {".jsonl"}
    else:
        extensions = {".md", ".jsonl"}

    for directory in source_dirs:
        if not directory.is_dir():
            logger.debug("Source directory does not exist or is not a directory: %s", directory)
            continue
        for file_path in directory.rglob("*"):
            if file_path.is_file() and file_path.suffix in extensions:
                collected.append(file_path)

    return collected


def _flatten_specstory_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert parse_specstory block dicts into the flat message format expected
    by extract_errors.

    parse_specstory produces blocks like::

        {"role": "assistant", "content": "...", "tool_calls": [
            {"tool_name": "Read", "tool_input": "...", "tool_output": "...", "error": None},
        ]}

    extract_errors expects flat records like::

        {"role": "assistant", "content": "...", "tool_name": None, "error": None, ...}
        {"role": "assistant", "content": "", "tool_name": "Read", "error": None, ...}

    For each block the function emits:
    - One flat record for the block itself (tool_name=None, error=None).
    - One synthetic flat record per tool_calls entry, carrying the tool_name
      and error from that entry.

    Human blocks have no tool_calls and are emitted as a single flat record.

    Parameters
    ----------
    blocks:
        Output of parse_specstory.

    Returns
    -------
    list[dict]
        Flat message dicts compatible with extract_errors.
    """
    flat: list[dict[str, Any]] = []

    for block in blocks:
        role: str = block.get("role", "")
        content: str = block.get("content", "") or ""
        tool_calls: list[dict[str, Any]] = block.get("tool_calls") or []

        # Emit one record for the conversational turn itself.
        flat.append(
            {
                "role": role,
                "content": content,
                "tool_name": None,
                "tool_input": None,
                "tool_output": None,
                "error": None,
                "timestamp": block.get("timestamp"),
                "session_id": block.get("session_id"),
            }
        )

        # Emit one record per tool call — these carry the error field that
        # extract_errors uses for tool_failure detection.
        for tc in tool_calls:
            flat.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_name": tc.get("tool_name"),
                    "tool_input": tc.get("tool_input"),
                    "tool_output": tc.get("tool_output"),
                    "error": tc.get("error"),
                    "timestamp": block.get("timestamp"),
                    "session_id": block.get("session_id"),
                }
            )

    return flat


def _process_file(
    file_path: Path,
    source_type_label: str,
    *,
    exclude_sidechains: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Parse a single file and return error records, messages, plus counts.

    Parameters
    ----------
    file_path:
        Path to the file to parse.
    source_type_label:
        "specstory" or "jsonl" — passed through to extract_errors.
    exclude_sidechains:
        When True, filter out messages where ``is_sidechain`` is True before
        error extraction.

    Returns
    -------
    tuple[list[dict], list[dict], int, int]
        (error_records, parsed_messages, message_count, tool_call_count).
    """
    if file_path.suffix == ".md":
        blocks = parse_specstory(file_path)
        messages = _flatten_specstory_blocks(blocks)
    else:
        messages = parse_jsonl(file_path)

    # Filter out sidechain messages when requested.
    if exclude_sidechains:
        messages = [m for m in messages if not m.get("is_sidechain")]

    message_count = len(messages)
    tool_call_count = sum(1 for m in messages if m.get("tool_name"))

    source_label = "specstory" if file_path.suffix == ".md" else "jsonl"
    error_records = extract_errors(messages, str(file_path), source_label)
    return error_records, messages, message_count, tool_call_count


def _parse_iso_timestamp(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a datetime, or return None."""
    if not ts:
        return None
    try:
        # Handle trailing Z
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _compute_session_metrics(
    messages: list[dict[str, Any]],
    error_records: list[dict[str, Any]],
    file_path: Path,
    file_hash: str,
) -> dict[str, Any]:
    """Compute per-session aggregate metrics from parsed messages.

    Parameters
    ----------
    messages:
        Flat list of parsed message dicts (from JSONL or flattened SpecStory).
    error_records:
        Error records extracted from the session.
    file_path:
        Path to the session file (used for session_id derivation).
    file_hash:
        SHA-256 hash of the file (used for session_id derivation).

    Returns
    -------
    dict
        A record suitable for ``insert_session_metrics``.
    """
    # --- session_id: derive from file_path + file_hash ---
    session_id = f"{file_path}:{file_hash[:16]}"

    # --- Token aggregation (skip None values) ---
    total_input_tokens = sum(
        m.get("input_tokens") or 0 for m in messages
        if m.get("input_tokens") is not None
    )
    total_output_tokens = sum(
        m.get("output_tokens") or 0 for m in messages
        if m.get("output_tokens") is not None
    )
    total_cache_read_tokens = sum(
        m.get("cache_read_input_tokens") or 0 for m in messages
        if m.get("cache_read_input_tokens") is not None
    )
    total_cache_create_tokens = sum(
        m.get("cache_creation_input_tokens") or 0 for m in messages
        if m.get("cache_creation_input_tokens") is not None
    )

    # --- Cache hit ratio ---
    denom = total_cache_read_tokens + total_input_tokens
    cache_hit_ratio = (
        total_cache_read_tokens / denom if denom > 0 else None
    )

    # --- Cost ---
    total_cost_usd = sum(
        m.get("cost_usd") or 0.0 for m in messages
        if m.get("cost_usd") is not None
    )

    # --- Session duration (first timestamp to last timestamp) ---
    timestamps: list[datetime] = []
    for m in messages:
        dt = _parse_iso_timestamp(m.get("timestamp"))
        if dt is not None:
            timestamps.append(dt)

    session_duration_seconds: float | None = None
    if len(timestamps) >= 2:
        timestamps.sort()
        delta = timestamps[-1] - timestamps[0]
        session_duration_seconds = delta.total_seconds()

    # --- Counts ---
    message_count = len(messages)
    tool_call_count = sum(1 for m in messages if m.get("tool_name"))
    error_count = len(error_records)
    sidechain_count = sum(1 for m in messages if m.get("is_sidechain"))

    # --- Stop reason distribution ---
    stop_reasons: Counter[str] = Counter()
    for m in messages:
        sr = m.get("stop_reason")
        if sr is not None:
            stop_reasons[sr] += 1
    stop_reason_distribution = (
        json.dumps(dict(stop_reasons)) if stop_reasons else None
    )

    # --- Model used (most common) ---
    model_counts: Counter[str] = Counter()
    for m in messages:
        model = m.get("model")
        if model is not None:
            model_counts[model] += 1
    model_used = (
        model_counts.most_common(1)[0][0] if model_counts else None
    )

    now = datetime.now(timezone.utc).isoformat()

    return {
        "session_id": session_id,
        "file_path": str(file_path),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": total_cache_read_tokens,
        "total_cache_create_tokens": total_cache_create_tokens,
        "cache_hit_ratio": cache_hit_ratio,
        "total_cost_usd": total_cost_usd,
        "session_duration_seconds": session_duration_seconds,
        "message_count": message_count,
        "tool_call_count": tool_call_count,
        "error_count": error_count,
        "correction_count": 0,  # populated later by positive_extractor
        "positive_signal_count": 0,  # populated later by positive_extractor
        "sidechain_count": sidechain_count,
        "stop_reason_distribution": stop_reason_distribution,
        "model_used": model_used,
        "mined_at": now,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_mine(
    db_conn: sqlite3.Connection,
    source_dirs: list[Path],
    since: str,
    source_type: str = "both",
    project: str | None = None,
    *,
    exclude_sidechains: bool = False,
) -> dict[str, Any]:
    """Run the full mining pipeline.

    Steps
    -----
    1. Collect all .md / .jsonl files from source_dirs according to source_type.
    2. Apply the time-window filter (since).
    3. Apply the optional project substring filter on file paths.
    4. Compute SHA-256 hash; skip files already in ``processed_sessions``.
    5. Parse each surviving file, extract errors, and insert into the DB.
    6. Record successfully mined files in ``processed_sessions``.
    7. Return a summary dict.

    Parameters
    ----------
    db_conn:
        Open SQLite connection with the v2 ``error_records`` table present.
    source_dirs:
        Directories to search for session files.
    since:
        Human-readable time expression accepted by filter_files, e.g.
        ``"3 days"``, ``"1 week"``, ``"2 months"``, ``"6 hours"``,
        ``"yesterday"``, ``"3 days ago"``, or ``"2026-01-15"``.
    source_type:
        One of ``"specstory"``, ``"jsonl"``, or ``"both"``.  Controls which
        file types are collected.
    project:
        Optional project name.  When not None, only files whose path contains
        this string (case-sensitive substring match) are processed.
    exclude_sidechains:
        When True, messages where ``is_sidechain`` is True are filtered out
        before error extraction / aggregation.

    Returns
    -------
    dict
        ``total_files_scanned`` (int)  — number of files processed after all filters.
        ``errors_found`` (int)         — total error records inserted.
        ``error_records`` (list[int])  — auto-assigned row IDs of inserted records.
        ``skipped_files`` (int)        — files skipped because already processed.
    """
    # --- 1. Collect candidate files ----------------------------------------
    all_files = _collect_files(source_dirs, source_type)

    # --- 2. Time-window filter ---------------------------------------------
    if all_files:
        time_filtered = filter_files(all_files, since)
    else:
        time_filtered = []

    # --- 3. Project substring filter ---------------------------------------
    if project is not None and time_filtered:
        project_filtered = [p for p in time_filtered if project in str(p)]
    else:
        project_filtered = time_filtered

    # total_files_scanned reflects files that survived the time filter (and
    # were candidates for processing).  The project filter is a best-effort
    # scope that controls which candidate files are actually parsed — it does
    # not reduce the reported scan count so that callers can see how many
    # files existed in the window regardless of project narrowing.
    total_files_scanned: int = len(time_filtered)

    # --- 4. Process each file ----------------------------------------------
    inserted_ids: list[int] = []
    skipped_files: int = 0
    total_cost_tracked: float = 0.0

    for file_path in project_filtered:
        # --- 4a. Deduplicate via processed_sessions ------------------------
        fhash = _file_hash(file_path)
        if _is_already_processed(db_conn, file_path, fhash):
            logger.info(
                "Skipping already-processed file: %s (hash=%s)",
                file_path, fhash[:12],
            )
            skipped_files += 1
            continue

        # Determine the source type label for this specific file.
        source_label = "specstory" if file_path.suffix == ".md" else "jsonl"

        try:
            error_records, parsed_messages, message_count, tool_call_count = (
                _process_file(
                    file_path, source_label,
                    exclude_sidechains=exclude_sidechains,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Skipping %s due to exception: %s: %s",
                file_path, type(exc).__name__, exc,
            )
            continue

        for record in error_records:
            try:
                row_id = insert_error_record(db_conn, record)
                inserted_ids.append(row_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to insert error record from %s: %s: %s",
                    file_path, type(exc).__name__, exc,
                )

        # --- 4b. Compute and insert session metrics -----------------------
        try:
            metrics = _compute_session_metrics(
                parsed_messages, error_records, file_path, fhash,
            )
            insert_session_metrics(db_conn, metrics)
            total_cost_tracked += metrics.get("total_cost_usd") or 0.0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to insert session metrics for %s: %s: %s",
                file_path, type(exc).__name__, exc,
            )

        # --- 4c. Mark file as processed ------------------------------------
        try:
            _mark_processed(
                db_conn, file_path, fhash, message_count, tool_call_count,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to record processed session for %s: %s: %s",
                file_path, type(exc).__name__, exc,
            )

    # --- 5. Return summary -------------------------------------------------
    newly_mined = len(project_filtered) - skipped_files
    return {
        "total_files_scanned": total_files_scanned,
        "errors_found": len(inserted_ids),
        "error_records": inserted_ids,
        "skipped_files": skipped_files,
        "newly_mined": newly_mined,
        "total_cost_tracked": total_cost_tracked,
    }
