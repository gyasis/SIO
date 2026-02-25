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

import logging
import sqlite3
from pathlib import Path
from typing import Any

from sio.core.db.queries import insert_error_record
from sio.mining.error_extractor import extract_errors
from sio.mining.jsonl_parser import parse_jsonl
from sio.mining.specstory_parser import parse_specstory
from sio.mining.time_filter import filter_files

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
) -> list[dict[str, Any]]:
    """Parse a single file and return a list of ErrorRecord dicts.

    Parameters
    ----------
    file_path:
        Path to the file to parse.
    source_type_label:
        "specstory" or "jsonl" — passed through to extract_errors.

    Returns
    -------
    list[dict]
        Zero or more ErrorRecord dicts.  Never raises; exceptions are re-raised
        to the caller for handling.
    """
    if file_path.suffix == ".md":
        blocks = parse_specstory(file_path)
        messages = _flatten_specstory_blocks(blocks)
        return extract_errors(messages, str(file_path), "specstory")
    else:
        messages = parse_jsonl(file_path)
        return extract_errors(messages, str(file_path), "jsonl")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_mine(
    db_conn: sqlite3.Connection,
    source_dirs: list[Path],
    since: str,
    source_type: str = "both",
    project: str | None = None,
) -> dict[str, Any]:
    """Run the full mining pipeline.

    Steps
    -----
    1. Collect all .md / .jsonl files from source_dirs according to source_type.
    2. Apply the time-window filter (since).
    3. Apply the optional project substring filter on file paths.
    4. Parse each surviving file, extract errors, and insert into the DB.
    5. Return a summary dict.

    Parameters
    ----------
    db_conn:
        Open SQLite connection with the v2 ``error_records`` table present.
    source_dirs:
        Directories to search for session files.
    since:
        Human-readable look-back window accepted by filter_files, e.g.
        ``"3 days"`` or ``"1 week"``.
    source_type:
        One of ``"specstory"``, ``"jsonl"``, or ``"both"``.  Controls which
        file types are collected.
    project:
        Optional project name.  When not None, only files whose path contains
        this string (case-sensitive substring match) are processed.

    Returns
    -------
    dict
        ``total_files_scanned`` (int) — number of files processed after all filters.
        ``errors_found`` (int)        — total error records inserted.
        ``error_records`` (list[int]) — auto-assigned row IDs of inserted records.
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

    for file_path in project_filtered:
        # Determine the source type label for this specific file.
        source_label = "specstory" if file_path.suffix == ".md" else "jsonl"

        try:
            error_records = _process_file(file_path, source_label)
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

    # --- 5. Return summary -------------------------------------------------
    return {
        "total_files_scanned": total_files_scanned,
        "errors_found": len(inserted_ids),
        "error_records": inserted_ids,
    }
