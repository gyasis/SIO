"""T031 [US3] Dataset lineage tracking — provenance for dataset construction.

Records which sessions contributed to a dataset and the time window that
was used for filtering.  Lineage is stored in the ``lineage_sessions``
column on the ``datasets`` table as a JSON array and is accumulated
(not overwritten) on repeated calls.
"""

from __future__ import annotations

import json
import sqlite3


def track_lineage(
    dataset_id: int,
    sessions: list[str],
    time_window: str,
    db_conn: sqlite3.Connection,
) -> None:
    """Record which sessions and time window contributed to a dataset.

    The sessions list is merged with any previously stored sessions,
    deduplicated, and written back.  The ``time_window`` is always updated
    to the value provided in the most recent call.

    Args:
        dataset_id: Row ID of the target ``datasets`` row.
        sessions: Session IDs that contributed to this build.  May be empty.
        time_window: Human-readable time window string (e.g. ``"2 weeks"``).
        db_conn: Active SQLite connection with the SIO v2 schema.
    """
    # Load the existing lineage_sessions value (may be NULL).
    row = db_conn.execute(
        "SELECT lineage_sessions FROM datasets WHERE id = ?",
        (dataset_id,),
    ).fetchone()

    if row is None:
        # Nothing to update — dataset_id does not exist.
        return

    existing_raw: str | None = row[0]
    existing_sessions: list[str] = []
    if existing_raw:
        try:
            parsed = json.loads(existing_raw)
            if isinstance(parsed, list):
                existing_sessions = parsed
            elif isinstance(parsed, dict):
                existing_sessions = parsed.get("sessions", [])
        except (json.JSONDecodeError, TypeError):
            existing_sessions = []

    # Merge and deduplicate while preserving order of first appearance.
    seen: set[str] = set()
    merged: list[str] = []
    for s in existing_sessions + sessions:
        if s not in seen:
            seen.add(s)
            merged.append(s)

    # Encode sessions + time_window together as a JSON object to avoid a
    # schema migration (lineage_sessions is a TEXT column):
    #   {"sessions": [...], "time_window": "2 weeks"}
    full_lineage = json.dumps({"sessions": merged, "time_window": time_window})
    db_conn.execute(
        "UPDATE datasets SET lineage_sessions = ?, updated_at = datetime('now') WHERE id = ?",
        (full_lineage, dataset_id),
    )
    db_conn.commit()


def get_lineage(dataset_id: int, db_conn: sqlite3.Connection) -> dict | None:
    """Retrieve the lineage record for a dataset.

    Args:
        dataset_id: Row ID of the target ``datasets`` row.
        db_conn: Active SQLite connection with the SIO v2 schema.

    Returns:
        Dict with ``dataset_id`` (int), ``sessions`` (list[str]), and
        ``time_window`` (str), or ``None`` if the dataset_id is not found
        or has no tracked lineage.
    """
    row = db_conn.execute(
        "SELECT id, lineage_sessions FROM datasets WHERE id = ?",
        (dataset_id,),
    ).fetchone()

    if row is None:
        return None

    raw: str | None = row[1]
    if not raw:
        # Dataset exists but lineage was never tracked.
        return None

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    # Handle both the legacy plain-list format and the current object format.
    if isinstance(payload, list):
        sessions: list[str] = payload
        time_window: str = ""
    elif isinstance(payload, dict):
        sessions = payload.get("sessions", [])
        time_window = payload.get("time_window", "")
    else:
        return None

    return {
        "dataset_id": dataset_id,
        "sessions": sessions,
        "time_window": time_window,
    }
