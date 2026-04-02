"""Append-only transaction log for the autoresearch loop (FR-044).

Writes to the ``autoresearch_txlog`` SQL table — NOT a JSONL file.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


class TxLog:
    """Transaction log backed by the ``autoresearch_txlog`` table.

    All writes are append-only.  Rows are never updated or deleted.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(
        self,
        cycle_number: int,
        action: str,
        status: str,
        details: str = "",
        suggestion_id: int | None = None,
        experiment_branch: str | None = None,
        assertion_results: dict | list | None = None,
    ) -> int:
        """Append a new entry to the transaction log.

        Args:
            cycle_number: Cycle ordinal (1-based).
            action: One of mine, cluster, grade, generate, assert,
                experiment_create, validate, promote, rollback, error, stop.
            status: One of success, failure, skipped, pending_approval.
            details: Human-readable description of the action.
            suggestion_id: Related suggestion row ID (optional).
            experiment_branch: Git branch/worktree name (optional).
            assertion_results: Dict or list serialised as JSON (optional).

        Returns:
            The row ID of the inserted entry.
        """
        now = datetime.now(timezone.utc).isoformat()
        assertion_json: str | None = None
        if assertion_results is not None:
            assertion_json = json.dumps(assertion_results)

        cursor = self._db.execute(
            "INSERT INTO autoresearch_txlog "
            "(cycle_number, action, status, details, suggestion_id, "
            " experiment_branch, assertion_results, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cycle_number,
                action,
                status,
                details,
                suggestion_id,
                experiment_branch,
                assertion_json,
                now,
            ),
        )
        self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_log(self, cycle: int | None = None) -> list[dict]:
        """Read transaction log entries.

        Args:
            cycle: If provided, filter to a specific cycle number.

        Returns:
            List of dicts (one per row), ordered by id ascending.
        """
        if cycle is not None:
            rows = self._db.execute(
                "SELECT * FROM autoresearch_txlog "
                "WHERE cycle_number = ? ORDER BY id",
                (cycle,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM autoresearch_txlog ORDER BY id",
            ).fetchall()

        result: list[dict] = []
        for row in rows:
            d = dict(row)
            # Deserialise assertion_results JSON if present
            ar = d.get("assertion_results")
            if ar is not None:
                try:
                    d["assertion_results"] = json.loads(ar)
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def active_experiment_count(self) -> int:
        """Count experiments that have not been promoted or rolled back.

        An active experiment is one with ``action='experiment_create'``
        and ``status='success'`` that does NOT have a corresponding
        ``promote`` or ``rollback`` entry for the same experiment branch.
        """
        row = self._db.execute(
            "SELECT COUNT(DISTINCT experiment_branch) FROM autoresearch_txlog "
            "WHERE action = 'experiment_create' AND status = 'success' "
            "AND experiment_branch NOT IN ("
            "  SELECT experiment_branch FROM autoresearch_txlog "
            "  WHERE action IN ('promote', 'rollback') "
            "  AND experiment_branch IS NOT NULL"
            ")",
        ).fetchone()
        return int(row[0]) if row else 0
