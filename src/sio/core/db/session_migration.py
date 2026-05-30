"""Non-destructive backfill of canonical ``agent:native_id`` session ids.

Legacy SIO rows store a bare Claude session id (no colon). This rewrites them
in place to canonical ``claude:<id>`` form across every session-keyed table.

Properties:
- **Idempotent** — rows already containing a colon are skipped, so re-running is
  a no-op.
- **Non-destructive** — only adds a ``claude:`` prefix; the original id is the
  suffix, so it is trivially reversible by stripping the prefix.
- **Schema-driven** — discovers session-keyed tables/columns from the live
  schema, so it adapts as tables are added.

See PRD sio_absorb_session_search, Phase A.
"""

from __future__ import annotations

import sqlite3

# Columns that hold a session identifier and should be namespaced.
_SESSION_COLUMNS = ("session_id", "parent_session_id")


def session_keyed_tables(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Map each table to the session-id columns it actually has."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    out: dict[str, list[str]] = {}
    for table in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        present = [c for c in _SESSION_COLUMNS if c in cols]
        if present:
            out[table] = present
    return out


def backfill_canonical_session_ids(
    conn: sqlite3.Connection, *, agent: str = "claude", dry_run: bool = False
) -> dict[str, int]:
    """Prefix bare session ids with ``{agent}:`` across all session-keyed tables.

    Returns a report mapping ``"table.column" -> rows_migrated`` (or, in
    ``dry_run`` mode, rows that *would* be migrated). Table/column names come
    from the trusted live schema (never user input), so f-string interpolation
    here is safe.
    """
    prefix = f"{agent}:"
    report: dict[str, int] = {}
    for table, cols in session_keyed_tables(conn).items():
        for col in cols:
            guard = f"{col} IS NOT NULL AND {col} != '' AND {col} NOT LIKE '%:%'"
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {guard}"  # noqa: S608
            ).fetchone()[0]
            report[f"{table}.{col}"] = n
            if n and not dry_run:
                conn.execute(
                    f"UPDATE {table} SET {col} = ? || {col} WHERE {guard}",  # noqa: S608
                    (prefix,),
                )
    if not dry_run:
        conn.commit()
    return report
