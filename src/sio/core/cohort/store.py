"""Cohort persistence helpers — thin wrapper around the experiments table.

PRD: ``~/dev/prd/scratch/sio_autotag_experiments_2026-05-23.md``.

Keeps row<->dataclass conversions in one place so the CLI layer in
``sio.cli.main`` doesn't deal with column ordering or sqlite quirks.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sio.core.cohort.models import Experiment


def _utc_now_iso() -> str:
    """ISO-8601 timestamp with Z suffix — matches the rest of SIO."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_experiment(row: sqlite3.Row) -> Experiment:
    return Experiment(
        id=row["id"],
        name=row["name"],
        start_ts=row["start_ts"],
        close_ts=row["close_ts"],
        note=row["note"],
        config_hash=row["config_hash"],
        project=row["project"],
        status=row["status"],
    )


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class ExperimentExists(Exception):
    """An open experiment with this name already exists."""


class ExperimentNotFound(Exception):
    """No experiment with this name exists in the database."""


class ExperimentAlreadyClosed(Exception):
    """Close was called on an already-closed experiment."""


def create_experiment(
    db_path: str | Path,
    name: str,
    *,
    note: Optional[str] = None,
    project: Optional[str] = None,
    config_hash: Optional[str] = None,
    start_ts: Optional[str] = None,
) -> Experiment:
    """Create a new ``open`` experiment row and return it.

    Raises:
        ExperimentExists: if any experiment (open or closed) with this
            name exists — names are UNIQUE.
    """
    ts = start_ts or _utc_now_iso()
    conn = _connect(db_path)
    try:
        try:
            cur = conn.execute(
                "INSERT INTO experiments "
                "(name, start_ts, close_ts, note, config_hash, project, status) "
                "VALUES (?, ?, NULL, ?, ?, ?, 'open')",
                (name, ts, note, config_hash, project),
            )
        except sqlite3.IntegrityError as exc:
            raise ExperimentExists(
                f"Experiment {name!r} already exists"
            ) from exc
        conn.commit()
        row = conn.execute(
            "SELECT * FROM experiments WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return _row_to_experiment(row)
    finally:
        conn.close()


def close_experiment(
    db_path: str | Path,
    name: str,
    *,
    close_ts: Optional[str] = None,
) -> Experiment:
    """Stamp ``close_ts`` and flip status to 'closed'.

    Idempotent on the timestamp side — if the experiment is already
    closed this raises ``ExperimentAlreadyClosed`` rather than silently
    overwriting. Callers that want force-close semantics should delete
    + recreate or call ``UPDATE`` directly.
    """
    ts = close_ts or _utc_now_iso()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM experiments WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise ExperimentNotFound(f"No experiment named {name!r}")
        if row["status"] == "closed":
            raise ExperimentAlreadyClosed(
                f"Experiment {name!r} already closed at {row['close_ts']}"
            )
        conn.execute(
            "UPDATE experiments SET close_ts = ?, status = 'closed' WHERE name = ?",
            (ts, name),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM experiments WHERE name = ?",
            (name,),
        ).fetchone()
        return _row_to_experiment(row)
    finally:
        conn.close()


def get_experiment(db_path: str | Path, name: str) -> Optional[Experiment]:
    """Return the named experiment, or None if it doesn't exist."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM experiments WHERE name = ?",
            (name,),
        ).fetchone()
        return _row_to_experiment(row) if row else None
    finally:
        conn.close()


def list_experiments(
    db_path: str | Path,
    *,
    status: Optional[str] = None,
    project: Optional[str] = None,
) -> list[Experiment]:
    """Return all experiments, newest first.

    Optional ``status`` filter ('open' or 'closed') and ``project``
    filter (exact match — empty / global cohorts have NULL project).
    """
    sql = "SELECT * FROM experiments"
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if project is not None:
        clauses.append("project = ?")
        params.append(project)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY start_ts DESC, id DESC"
    conn = _connect(db_path)
    try:
        return [_row_to_experiment(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
