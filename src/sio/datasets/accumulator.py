"""T030b [US3] Dataset accumulator — incremental dataset growth across patterns.

Feeds a batch of new error records into existing pattern datasets (appending)
and creates fresh datasets for patterns that have no dataset yet.  Returns a
summary dict with ``updated_count`` and ``created_count``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sio.datasets.builder import _error_to_example, _load_existing_file, _resolve_dataset_dir

_DEFAULT_MIN_THRESHOLD = 5


def _get_dataset_for_pattern(db_conn: sqlite3.Connection, pattern_row_id: int) -> dict | None:
    """Return the datasets row for a pattern, or None if absent."""
    row = db_conn.execute(
        "SELECT * FROM datasets WHERE pattern_id = ?",
        (pattern_row_id,),
    ).fetchone()
    return dict(row) if row else None


def _insert_dataset_row(
    db_conn: sqlite3.Connection,
    pattern_row_id: int,
    file_path: str,
    positive_count: int,
    negative_count: int,
    min_threshold: int,
) -> int:
    """Insert a new datasets row and return its rowid."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = db_conn.execute(
        """
        INSERT INTO datasets
            (pattern_id, file_path, positive_count, negative_count,
             min_threshold, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pattern_row_id,
            file_path,
            positive_count,
            negative_count,
            min_threshold,
            now_iso,
            now_iso,
        ),
    )
    db_conn.commit()
    return cur.lastrowid


def _update_dataset_counts(
    db_conn: sqlite3.Connection,
    dataset_id: int,
    positive_count: int,
    negative_count: int,
) -> None:
    """Update positive_count and negative_count on an existing datasets row."""
    now_iso = datetime.now(timezone.utc).isoformat()
    db_conn.execute(
        "UPDATE datasets SET positive_count = ?, negative_count = ?, updated_at = ? WHERE id = ?",
        (positive_count, negative_count, now_iso, dataset_id),
    )
    db_conn.commit()


def _write_dataset_file(
    file_path: Path,
    pattern_id: str,
    examples: list[dict],
) -> None:
    """Write the combined examples list to a dataset JSON file."""
    positive_count = sum(1 for ex in examples if ex.get("label") == 1)
    negative_count = sum(1 for ex in examples if ex.get("label") == 0)
    now_iso = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "metadata": {
            "pattern_id": pattern_id,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "updated_at": now_iso,
        },
        "examples": examples,
    }
    file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _errors_for_pattern(errors: list[dict], pattern: dict) -> list[dict]:
    """Filter errors that match a pattern by tool_name."""
    tool_name: str | None = pattern.get("tool_name")
    if not tool_name:
        return list(errors)
    return [e for e in errors if e.get("tool_name") == tool_name]


def accumulate(
    errors: list[dict],
    patterns: list[dict],
    db_conn: sqlite3.Connection,
    dataset_dir: str | Path | None = None,
    min_threshold: int = _DEFAULT_MIN_THRESHOLD,
) -> dict:
    """Feed new errors into existing datasets or create new ones per pattern.

    For each pattern in ``patterns``:

    - If a ``datasets`` row already exists, append the matching errors to the
      existing JSON file and update the DB counts.
    - If no ``datasets`` row exists, create a new file and DB row (only when
      the matching error count meets ``min_threshold``).

    Args:
        errors: Batch of new error record dicts to process.
        patterns: List of pattern dicts.  Each must have at minimum ``id``
            and ``pattern_id`` keys.
        db_conn: Active SQLite connection with the SIO v2 schema.
        dataset_dir: Directory for JSON files.  Defaults to
            ``~/.sio/datasets/``.
        min_threshold: Minimum total examples to create a brand-new dataset.
            Existing datasets are always updated regardless of count.

    Returns:
        Dict with ``updated_count`` (existing datasets touched) and
        ``created_count`` (new datasets created).
    """
    output_dir = _resolve_dataset_dir(dataset_dir)
    updated_count = 0
    created_count = 0

    for pattern in patterns:
        pattern_row_id: int = pattern["id"]
        pattern_id: str = pattern["pattern_id"]

        # Find errors that match this pattern.
        matching_errors = _errors_for_pattern(errors, pattern)

        # Build example dicts for the new batch.
        new_examples = [_error_to_example(e) for e in matching_errors]

        # Check for an existing dataset row.
        existing_dataset = _get_dataset_for_pattern(db_conn, pattern_row_id)

        if existing_dataset is not None:
            # --- Update path ---
            file_path = Path(existing_dataset["file_path"])

            # Load the existing file (may not be on disk if DB is out of sync).
            existing_payload = _load_existing_file(file_path)
            existing_examples: list[dict] = existing_payload.get("examples", [])

            # Deduplicate by example id.
            existing_ids: set[Any] = {
                ex.get("id") for ex in existing_examples if ex.get("id") is not None
            }
            truly_new = [ex for ex in new_examples if ex.get("id") not in existing_ids]

            combined = existing_examples + truly_new

            _write_dataset_file(file_path, pattern_id, combined)

            positive_count = sum(1 for ex in combined if ex.get("label") == 1)
            negative_count = sum(1 for ex in combined if ex.get("label") == 0)
            _update_dataset_counts(db_conn, existing_dataset["id"], positive_count, negative_count)
            updated_count += 1

        else:
            # --- Create path ---
            if len(new_examples) < min_threshold:
                # Not enough examples to justify a new dataset.
                continue

            safe_name = pattern_id.replace("/", "_").replace("\\", "_")
            file_path = output_dir / f"{safe_name}.json"

            _write_dataset_file(file_path, pattern_id, new_examples)

            positive_count = sum(1 for ex in new_examples if ex.get("label") == 1)
            negative_count = sum(1 for ex in new_examples if ex.get("label") == 0)
            _insert_dataset_row(
                db_conn,
                pattern_row_id,
                str(file_path),
                positive_count,
                negative_count,
                min_threshold,
            )
            created_count += 1

    return {"updated_count": updated_count, "created_count": created_count}
