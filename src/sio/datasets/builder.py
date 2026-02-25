"""T030 [US3] Dataset builder — positive/negative example construction.

Builds labeled JSON dataset files from error records associated with a
pattern.  Positive examples are successful tool calls (empty error_text);
negative examples are failed calls (non-empty error_text).

Supports incremental updates: calling build_dataset twice on the same
pattern appends new examples rather than rebuilding from scratch.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DATASET_DIR = Path("~/.sio/datasets").expanduser()


def _resolve_dataset_dir(dataset_dir: str | Path | None) -> Path:
    """Resolve the dataset directory, creating it if necessary."""
    if dataset_dir is None:
        path = _DEFAULT_DATASET_DIR
    else:
        path = Path(dataset_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_linked_error_ids(db_conn: sqlite3.Connection, pattern_row_id: int) -> set[int]:
    """Return the set of error record IDs linked to a pattern via pattern_errors."""
    rows = db_conn.execute(
        "SELECT error_id FROM pattern_errors WHERE pattern_id = ?",
        (pattern_row_id,),
    ).fetchall()
    return {row[0] for row in rows}


def _error_to_example(error: dict) -> dict:
    """Convert an error record dict to a dataset example dict."""
    error_text: str = error.get("error_text") or ""
    label = 1 if not error_text else 0
    return {
        "id": error.get("id"),
        "label": label,
        "error_text": error_text,
        "tool_name": error.get("tool_name"),
        "session_id": error.get("session_id"),
        "timestamp": error.get("timestamp"),
        "user_message": error.get("user_message"),
        "error_type": error.get("error_type"),
        "source_file": error.get("source_file"),
    }


def _load_existing_file(file_path: Path) -> dict[str, Any]:
    """Load an existing dataset JSON file, returning an empty structure on missing."""
    if file_path.exists():
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"metadata": {}, "examples": []}


def build_dataset(
    pattern: dict,
    all_errors: list[dict],
    db_conn: sqlite3.Connection,
    dataset_dir: str | Path | None = None,
    min_threshold: int = 5,
) -> dict | None:
    """Build or incrementally update a labeled dataset for a pattern.

    Finds all error records linked to the pattern in the DB, filters them
    from ``all_errors``, and writes a JSON dataset file.  Positive examples
    have empty ``error_text``; negative examples have non-empty ``error_text``.

    Args:
        pattern: Pattern dict with at minimum ``id`` and ``pattern_id`` keys.
        all_errors: Full list of error record dicts to draw examples from.
        db_conn: Active SQLite connection with the SIO v2 schema.
        dataset_dir: Directory to write JSON files.  Defaults to
            ``~/.sio/datasets/``.
        min_threshold: Minimum total examples required to write/return a
            dataset.  Returns ``None`` when the total falls below this.

    Returns:
        Metadata dict with ``pattern_id``, ``positive_count``,
        ``negative_count``, and ``file_path``, or ``None`` when below
        the minimum threshold.
    """
    pattern_row_id: int = pattern["id"]
    pattern_id: str = pattern["pattern_id"]

    # Determine which error IDs belong to this pattern.
    linked_ids = _get_linked_error_ids(db_conn, pattern_row_id)

    # Index all_errors by id for fast lookup.
    errors_by_id: dict[int, dict] = {e["id"]: e for e in all_errors if "id" in e}

    # Build the set of new examples from linked errors present in all_errors.
    new_examples: list[dict] = []
    for eid in linked_ids:
        if eid in errors_by_id:
            new_examples.append(_error_to_example(errors_by_id[eid]))

    # Resolve the output file path (stable per pattern_id).
    output_dir = _resolve_dataset_dir(dataset_dir)
    safe_name = pattern_id.replace("/", "_").replace("\\", "_")
    file_path = output_dir / f"{safe_name}.json"

    # Load existing data for incremental append.
    existing = _load_existing_file(file_path)
    existing_examples: list[dict] = existing.get("examples", [])

    # Deduplicate by example id — only append genuinely new records.
    existing_example_ids: set[Any] = {
        ex.get("id") for ex in existing_examples if ex.get("id") is not None
    }
    truly_new = [ex for ex in new_examples if ex.get("id") not in existing_example_ids]

    combined_examples = existing_examples + truly_new

    # Check minimum threshold against combined total.
    if len(combined_examples) < min_threshold:
        return None

    positive_count = sum(1 for ex in combined_examples if ex["label"] == 1)
    negative_count = sum(1 for ex in combined_examples if ex["label"] == 0)

    now_iso = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "metadata": {
            "pattern_id": pattern_id,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "updated_at": now_iso,
        },
        "examples": combined_examples,
    }
    file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "pattern_id": pattern_id,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "file_path": str(file_path),
    }


def collect_dataset(
    db_conn: sqlite3.Connection,
    since: str | None = None,
    error_type: str | None = None,
    sessions: list[str] | None = None,
    dataset_dir: str | Path | None = None,
) -> dict:
    """Collect error records on demand using DB filters.

    Args:
        db_conn: Active SQLite connection with the SIO v2 schema.
        since: ISO date string (e.g. ``"2026-02-15"``).  Only errors with
            ``timestamp >= since`` are returned.
        error_type: Filter by the ``error_type`` column.
        sessions: List of ``session_id`` values to restrict to.
        dataset_dir: Unused at collection time; reserved for future use.

    Returns:
        Dict with an ``"errors"`` key containing the list of matching error
        record dicts.
    """
    query = "SELECT * FROM error_records WHERE 1=1"
    params: list[Any] = []

    if since:
        query += " AND timestamp >= ?"
        params.append(since)

    if error_type:
        query += " AND error_type = ?"
        params.append(error_type)

    if sessions:
        placeholders = ", ".join("?" * len(sessions))
        query += f" AND session_id IN ({placeholders})"
        params.extend(sessions)

    query += " ORDER BY timestamp DESC"

    rows = db_conn.execute(query, params).fetchall()
    errors = [dict(row) for row in rows]

    return {"errors": errors}
