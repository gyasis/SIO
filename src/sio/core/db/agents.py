"""Agent isolation for mined data — ONE database, a real ``agent`` column.

Every table that stores per-session mined data carries an ``agent`` column
(``claude``, ``pi``, ``codex`` ... — see :data:`sio.core.session_handle.KNOWN_AGENTS`)
so "which coding agent does this row belong to" is a first-class, indexed,
enforced property rather than a ``session_id LIKE 'pi:%'`` text convention.

What lives here:

* :data:`AGENT_TABLES` — the inventory of tables that get the column, keyed by
  the column the agent is derived from (``session_id``, or ``file_path`` for
  ``processed_sessions`` which has no session id).
* :func:`ensure_agent_columns` / :func:`ensure_agent_triggers` /
  :func:`ensure_agent_views` — idempotent schema pieces, re-applied on every
  ``init_db`` so a newly added ``KNOWN_AGENT`` gets its view and its trigger
  arm without a migration.
* :func:`migrate_006_agent_isolation` — the one-shot data migration: backup,
  backfill, merge partial ids, dedupe, UNIQUE fingerprint index. Atomic
  (``BEGIN IMMEDIATE``), idempotent, race-safe.
* :func:`drop_agent` — delete one agent's mined rows everywhere (dry run by
  default). Never touches the agent's own on-disk session files.

Enforcement model
-----------------
``agent TEXT NOT NULL DEFAULT ''`` plus an ``AFTER INSERT`` trigger per table
that derives the agent from the row's session id (or path) whenever a writer
left it empty. So the invariant *every row has an agent consistent with its
session id* holds for every writer — the Python seam in ``queries.py`` (which
stamps it explicitly), raw SQL in scripts and tests, and hook code from an
older checkout. The trigger's derivation rule is the same one
:func:`sio.core.session_handle.parse_handle` uses: a known-agent prefix wins,
anything else is the legacy Claude id.

The dedupe fingerprint deliberately does NOT include ``agent``: after backfill
the agent is a pure function of ``session_id`` (so it adds nothing), and
leaving it out means the self-healing trigger's UPDATE can never collide with
the UNIQUE index.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from sio.core import session_handle as _sh
from sio.core.session_handle import LEGACY_AGENT

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 6
SCHEMA_DESCRIPTION = "006-agent-isolation"

# table -> column the agent is derived from.
AGENT_TABLES: dict[str, str] = {
    "error_records": "session_id",
    "flow_events": "session_id",
    "positive_records": "session_id",
    "session_metrics": "session_id",
    "processed_sessions": "file_path",
}

# (child table, fk column, parent agent table) — rows that reference an agent
# table's ``id`` and must be remapped on dedupe / deleted on drop-agent.
DEPENDENT_TABLES: tuple[tuple[str, str, str], ...] = (
    ("pattern_errors", "error_id", "error_records"),
)

# experiment_runs is polymorphic: (source_table, event_id).
EXPERIMENT_RUN_SOURCES: tuple[str, ...] = (
    "error_records",
    "flow_events",
    "positive_records",
)

# Per-agent SQL views: table -> view name prefix (errors_pi, flows_pi, ...).
VIEW_PREFIXES: dict[str, str] = {
    "error_records": "errors",
    "flow_events": "flows",
}

# The dedupe identity of an error record. COALESCE so NULL tool_name /
# error_type compare equal (UNIQUE treats NULLs as distinct otherwise).
ERROR_FINGERPRINT_COLS = (
    "session_id",
    "timestamp",
    "COALESCE(error_type, '')",
    "COALESCE(tool_name, '')",
    "error_text",
)
ERROR_FINGERPRINT_INDEX = "ux_error_records_fingerprint"
ERROR_FINGERPRINT_INDEX_SQL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {ERROR_FINGERPRINT_INDEX} "
    f"ON error_records({', '.join(ERROR_FINGERPRINT_COLS)})"
)

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Small schema helpers
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _present_agent_tables(conn: sqlite3.Connection) -> dict[str, str]:
    """AGENT_TABLES restricted to tables that exist AND carry both columns."""
    out: dict[str, str] = {}
    for table, key in AGENT_TABLES.items():
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        if "agent" in cols and key in cols:
            out[table] = key
    return out


def agent_case_sql(column: str) -> str:
    """SQL expression deriving the agent from a session id / path column.

    Mirrors :func:`sio.core.session_handle.parse_handle`: the text before the
    first ``:`` when it names a known agent, else the legacy Claude id.
    """
    prefix = f"substr({column}, 1, instr({column}, ':') - 1)"
    known = ", ".join(f"'{a}'" for a in _sh.KNOWN_AGENTS)
    return f"CASE WHEN {prefix} IN ({known}) THEN {prefix} ELSE '{LEGACY_AGENT}' END"


def _unknown_prefix_sql(column: str) -> str:
    """WHERE fragment: rows whose ``agent:`` prefix is NOT a known agent.

    Only counts values shaped like a handle (a short token before the colon,
    no path separator), so a Claude ``<path>:<hash>`` session id is not
    mis-reported as an unknown agent.
    """
    prefix = f"substr({column}, 1, instr({column}, ':') - 1)"
    known = ", ".join(f"'{a}'" for a in _sh.KNOWN_AGENTS)
    return (
        f"instr({column}, ':') > 1 AND {prefix} NOT IN ({known}) "
        f"AND instr({prefix}, '/') = 0 AND length({prefix}) <= 32"
    )


def validate_agent_name(agent: str) -> str:
    """Return ``agent`` if it is a known agent AND a safe SQL identifier."""
    if agent not in _sh.KNOWN_AGENTS:
        raise ValueError(
            f"unknown agent {agent!r} — known agents: {', '.join(_sh.KNOWN_AGENTS)}"
        )
    if not _IDENT_RE.match(agent):
        raise ValueError(f"agent name {agent!r} is not a valid SQL identifier")
    return agent


# ---------------------------------------------------------------------------
# Idempotent schema pieces (run from init_db on every open)
# ---------------------------------------------------------------------------


def ensure_agent_columns(conn: sqlite3.Connection) -> list[str]:
    """ALTER-add ``agent`` (+ index) to every inventoried table that lacks it.

    Returns the tables that were altered. Safe on fresh DBs (whose DDL already
    declares the column) and on legacy ones.
    """
    altered: list[str] = []
    for table in AGENT_TABLES:
        if not _table_exists(conn, table):
            continue
        if "agent" not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN agent TEXT NOT NULL DEFAULT ''")
            altered.append(table)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_agent ON {table}(agent)")
    return altered


def _trigger_sql(table: str, key: str) -> str:
    return (
        f"CREATE TRIGGER trg_{table}_agent_default AFTER INSERT ON {table} "
        f"WHEN NEW.agent IS NULL OR NEW.agent = '' "
        f"BEGIN UPDATE {table} SET agent = {agent_case_sql('NEW.' + key)} "
        f"WHERE rowid = NEW.rowid; END"
    )


def _view_sql(name: str, table: str, agent: str) -> str:
    return f"CREATE VIEW {name} AS SELECT * FROM {table} WHERE agent = '{agent}'"


def _sync_object(
    conn: sqlite3.Connection, kind: str, name: str, desired_sql: str
) -> bool:
    """Create ``kind`` object ``name`` with ``desired_sql`` unless it already
    exists with exactly that definition. Returns True when (re)created."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type=? AND name=?", (kind, name)
    ).fetchone()
    if row is not None and row[0] == desired_sql:
        return False
    if row is not None:
        conn.execute(f"DROP {kind.upper()} IF EXISTS {name}")
    conn.execute(desired_sql)
    return True


def ensure_agent_triggers(conn: sqlite3.Connection) -> list[str]:
    """(Re)create the self-healing ``agent`` triggers. Returns names touched."""
    touched: list[str] = []
    for table, key in _present_agent_tables(conn).items():
        name = f"trg_{table}_agent_default"
        if _sync_object(conn, "trigger", name, _trigger_sql(table, key)):
            touched.append(name)
    return touched


def ensure_agent_views(conn: sqlite3.Connection) -> list[str]:
    """(Re)create ``errors_<agent>`` / ``flows_<agent>`` for every KNOWN_AGENT.

    Returns the view names touched. A view whose definition already matches is
    left alone, so this is cheap to call on every open.
    """
    touched: list[str] = []
    present = _present_agent_tables(conn)
    for table, prefix in VIEW_PREFIXES.items():
        if table not in present:
            continue
        for agent in _sh.KNOWN_AGENTS:
            validate_agent_name(agent)
            name = f"{prefix}_{agent}"
            if _sync_object(conn, "view", name, _view_sql(name, table, agent)):
                touched.append(name)
    return touched


def ensure_error_fingerprint_index(conn: sqlite3.Connection) -> bool:
    """Create the UNIQUE fingerprint index on error_records if possible.

    On a legacy DB that still holds exact duplicates the CREATE fails with an
    IntegrityError; that is not fatal here — :func:`migrate_006_agent_isolation`
    dedupes first and then creates it. Returns True when the index exists
    after the call.
    """
    if not _table_exists(conn, "error_records"):
        return False
    try:
        conn.execute(ERROR_FINGERPRINT_INDEX_SQL)
    except sqlite3.IntegrityError as exc:
        logger.debug(
            "error_records still has duplicate fingerprints (%s); "
            "run `sio db migrate` to dedupe and add %s",
            exc,
            ERROR_FINGERPRINT_INDEX,
        )
        return False
    return True


def agent_migration_pending(conn: sqlite3.Connection) -> bool:
    """True when the 006 data migration has not been applied to this DB.

    A DB with no ``schema_version`` table at all is one ``init_db`` just
    created (tests, first run) — it has no legacy rows to backfill, so it is
    not reported as pending; ``sio init`` / ``sio db migrate`` stamp it.
    """
    if not _table_exists(conn, "schema_version"):
        return False
    row = conn.execute(
        "SELECT status FROM schema_version WHERE version=?", (SCHEMA_VERSION,)
    ).fetchone()
    return row is None or row[0] != "applied"


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup_db(db_path: str | Path, tag: str) -> Path | None:
    """Consistent copy of ``db_path`` via the sqlite3 backup API.

    Lands in ``<db dir>/backups/<db name>.<utc-ts>.<tag>.bak`` — derived from
    the DB's own location, so a test DB backs up into its tmp dir, never into
    the real ``~/.sio``. Returns the backup path, or None for ``:memory:`` /
    a DB file that does not exist yet.
    """
    if str(db_path) == ":memory:":
        return None
    src_path = Path(db_path)
    if not src_path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst_dir = src_path.parent / "backups"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_path = dst_dir / f"{src_path.name}.{ts}.{tag}.bak"
    src = sqlite3.connect(str(src_path), timeout=30.0)
    try:
        src.execute("PRAGMA busy_timeout=30000")
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dst_path


# ---------------------------------------------------------------------------
# Migration 006
# ---------------------------------------------------------------------------


def _open(db_path: str | Path) -> sqlite3.Connection:
    """Autocommit connection (explicit BEGIN/COMMIT), 30 s busy timeout, WAL.

    Switching a rollback-journal DB to WAL needs an exclusive moment that the
    busy handler does not cover, so it is attempted only when the DB is not
    already WAL (the canonical ``~/.sio/sio.db`` always is) and retried briefly
    when another connection is mid-flight.
    """
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    if str(db_path) != ":memory:":
        deadline = time.monotonic() + 30.0
        while conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
    return conn


def _is_applied(conn: sqlite3.Connection) -> bool:
    return not agent_migration_pending(conn)


def _counts_by_agent(conn: sqlite3.Connection, table: str, expr: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT {expr} AS a, COUNT(*) FROM {table} GROUP BY a ORDER BY a"  # noqa: S608
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def _merge_partial_ids(
    conn: sqlite3.Connection, table: str, agent: str
) -> tuple[int, int, list[str]]:
    """Fold partial non-claude session ids into the one full id they belong to.

    A stored id ``agent:P`` is merged into ``agent:F`` when P is a strict
    substring of exactly one other native id F present in the SAME table for
    that agent. Filesystem is never consulted. Returns
    ``(ids_merged, rows_rewritten, ambiguous_ids)``.
    """
    ids = [
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT session_id FROM {table} WHERE agent = ?",  # noqa: S608
            (agent,),
        ).fetchall()
    ]
    natives = {sid: sid.split(":", 1)[1] if ":" in sid else sid for sid in ids}
    merged = rows = 0
    ambiguous: list[str] = []
    for sid, nat in natives.items():
        if not nat:
            continue
        cands = [
            other
            for other, onat in natives.items()
            if other != sid and len(onat) > len(nat) and nat in onat
        ]
        if len(cands) == 1:
            cur = conn.execute(
                f"UPDATE {table} SET session_id = ? WHERE session_id = ? AND agent = ?",  # noqa: S608
                (cands[0], sid, agent),
            )
            merged += 1
            rows += cur.rowcount
        elif len(cands) > 1:
            ambiguous.append(sid)
    return merged, rows, ambiguous


def _remap_dependents(conn: sqlite3.Connection, keep: int, dup: int) -> None:
    """Point every row referencing error ``dup`` at ``keep`` (drop leftovers
    that would violate a PK/UNIQUE because ``keep`` is already linked)."""
    for child, fk, parent in DEPENDENT_TABLES:
        if parent != "error_records" or not _table_exists(conn, child):
            continue
        conn.execute(
            f"UPDATE OR IGNORE {child} SET {fk} = ? WHERE {fk} = ?", (keep, dup)  # noqa: S608
        )
        conn.execute(f"DELETE FROM {child} WHERE {fk} = ?", (dup,))  # noqa: S608
    if _table_exists(conn, "experiment_runs"):
        conn.execute(
            "UPDATE OR IGNORE experiment_runs SET event_id = ? "
            "WHERE source_table = 'error_records' AND event_id = ?",
            (keep, dup),
        )
        conn.execute(
            "DELETE FROM experiment_runs WHERE source_table = 'error_records' AND event_id = ?",
            (dup,),
        )


def _dedupe_error_records(conn: sqlite3.Connection) -> dict[str, int]:
    """Delete exact duplicates (same fingerprint), keeping the lowest id.

    Referencing rows are remapped to the survivor first. Returns rows removed
    per agent.
    """
    fp = ", ".join(ERROR_FINGERPRINT_COLS)
    groups = conn.execute(
        f"SELECT MIN(id), GROUP_CONCAT(id), agent FROM error_records "  # noqa: S608
        f"GROUP BY {fp} HAVING COUNT(*) > 1"
    ).fetchall()
    removed: dict[str, int] = {}
    for keep, ids_csv, agent in groups:
        dups = [int(x) for x in ids_csv.split(",") if int(x) != keep]
        for dup in dups:
            _remap_dependents(conn, keep, dup)
        placeholders = ", ".join("?" * len(dups))
        conn.execute(
            f"DELETE FROM error_records WHERE id IN ({placeholders})", dups  # noqa: S608
        )
        removed[agent] = removed.get(agent, 0) + len(dups)
    return removed


def migrate_006_agent_isolation(
    db_path: str | Path, *, backup: bool = True
) -> dict:
    """Bring a DB to schema version 6: the ``agent`` column, backfilled and enforced.

    Steps, all inside ONE ``BEGIN IMMEDIATE`` transaction (writers from live
    hooks wait on ``busy_timeout``; a crash rolls everything back):

    1. add ``agent`` + index to every :data:`AGENT_TABLES` table
    2. backfill it from the session id / path (known prefix → that agent,
       anything else → ``claude``; unknown ``xyz:`` prefixes are counted and
       reported, never silently dropped)
    3. merge partial non-claude ids into the full canonical id when unambiguous
    4. dedupe exact error_records duplicates (survivor = lowest id, referencing
       rows remapped)
    5. UNIQUE fingerprint index on error_records
    6. self-healing triggers + per-agent views
    7. stamp ``schema_version`` 6 applied

    Before any change a sqlite backup-API copy is written next to the DB
    (``<dir>/backups/``). Idempotent: an already-migrated DB returns
    ``{"status": "already_applied"}`` with no backup and no writes. Race-safe:
    the applied check is repeated under the write lock, so of two concurrent
    callers the second becomes a no-op.

    Returns a report dict (see :func:`format_migration_report`).
    """
    from sio.core.db.schema import ensure_schema_version  # noqa: PLC0415

    conn = _open(db_path)
    try:
        ensure_schema_version(conn)
        if _is_applied(conn):
            return {"status": "already_applied", "db_path": str(db_path)}

        backup_path = backup_db(db_path, "pre-agent-migration") if backup else None
        report: dict = {
            "status": "applied",
            "db_path": str(db_path),
            "backup": str(backup_path) if backup_path else None,
            "before": {},
            "after": {},
            "unknown_prefix": {},
            "merged_ids": {},
            "merged_rows": {},
            "ambiguous_ids": {},
            "duplicates_removed": {},
        }
        started = datetime.now(timezone.utc)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _is_applied(conn):  # lost a race — the other process did it
                conn.execute("ROLLBACK")
                return {"status": "already_applied", "db_path": str(db_path)}

            conn.execute(
                "INSERT OR IGNORE INTO schema_version "
                "(version, applied_at, status, description) VALUES (?, ?, 'applying', ?)",
                (SCHEMA_VERSION, started.isoformat(), SCHEMA_DESCRIPTION),
            )
            conn.execute(
                "UPDATE schema_version SET status='applying', applied_at=? WHERE version=?",
                (started.isoformat(), SCHEMA_VERSION),
            )

            # 1. columns
            ensure_agent_columns(conn)
            present = _present_agent_tables(conn)

            # 2. backfill
            for table, key in present.items():
                report["before"][table] = _counts_by_agent(conn, table, agent_case_sql(key))
                report["unknown_prefix"][table] = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {_unknown_prefix_sql(key)}"  # noqa: S608
                    ).fetchone()[0]
                )
                conn.execute(
                    f"UPDATE {table} SET agent = {agent_case_sql(key)} "  # noqa: S608
                    "WHERE agent IS NULL OR agent = ''"
                )

            # 3. merge partial ids (non-claude, canonical-id tables only)
            for table in ("error_records", "flow_events"):
                if table not in present:
                    continue
                for agent in _sh.KNOWN_AGENTS:
                    if agent == LEGACY_AGENT:
                        continue
                    merged, rows, ambiguous = _merge_partial_ids(conn, table, agent)
                    if merged:
                        report["merged_ids"].setdefault(table, {})[agent] = merged
                        report["merged_rows"].setdefault(table, {})[agent] = rows
                    if ambiguous:
                        report["ambiguous_ids"].setdefault(table, {})[agent] = ambiguous

            # 4. dedupe + 5. fingerprint index
            if "error_records" in present:
                report["duplicates_removed"] = _dedupe_error_records(conn)
                conn.execute(ERROR_FINGERPRINT_INDEX_SQL)

            # 6. triggers + views
            ensure_agent_triggers(conn)
            ensure_agent_views(conn)

            for table in present:
                report["after"][table] = _counts_by_agent(conn, table, "agent")

            # 7. stamp
            conn.execute(
                "UPDATE schema_version SET status='applied' WHERE version=?",
                (SCHEMA_VERSION,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        report["elapsed_seconds"] = round(
            (datetime.now(timezone.utc) - started).total_seconds(), 3
        )
        return report
    finally:
        conn.close()


def format_migration_report(report: dict) -> str:
    """Human-readable rendering of a :func:`migrate_006_agent_isolation` report."""
    if report.get("status") == "already_applied":
        return f"agent migration (006): already applied on {report.get('db_path')}"
    lines = [f"agent migration (006) applied on {report['db_path']}"]
    if report.get("backup"):
        lines.append(f"  backup: {report['backup']}")
    lines.append(f"  elapsed: {report.get('elapsed_seconds', 0)} s")
    for table in report["after"]:
        before = report["before"].get(table, {})
        after = report["after"][table]
        lines.append(f"  {table}:")
        for agent in sorted(set(before) | set(after)):
            lines.append(
                f"    {agent:12s} before={before.get(agent, 0):7d}  after={after.get(agent, 0):7d}"
            )
        unk = report["unknown_prefix"].get(table, 0)
        if unk:
            lines.append(
                f"    ! {unk} row(s) had an unknown 'xyz:' prefix and were filed "
                "under claude (legacy rule) — review them"
            )
    for table, per_agent in report.get("merged_ids", {}).items():
        for agent, n in per_agent.items():
            rows = report["merged_rows"][table][agent]
            lines.append(
                f"  merged {n} partial {agent} session id(s) in {table} ({rows} rows)"
            )
    for table, per_agent in report.get("ambiguous_ids", {}).items():
        for agent, ids in per_agent.items():
            lines.append(
                f"  ! {len(ids)} ambiguous partial {agent} id(s) left as-is in {table}: "
                + ", ".join(ids)
            )
    dups = report.get("duplicates_removed") or {}
    if dups:
        lines.append(
            "  duplicate error_records removed: "
            + ", ".join(f"{a}={n}" for a, n in sorted(dups.items()))
        )
    else:
        lines.append("  duplicate error_records removed: 0")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# drop-agent
# ---------------------------------------------------------------------------


def _drop_counts(conn: sqlite3.Connection, agent: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    present = _present_agent_tables(conn)
    for child, fk, parent in DEPENDENT_TABLES:
        if parent in present and _table_exists(conn, child):
            counts[child] = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {child} WHERE {fk} IN "  # noqa: S608
                    f"(SELECT id FROM {parent} WHERE agent = ?)",
                    (agent,),
                ).fetchone()[0]
            )
    if _table_exists(conn, "experiment_runs"):
        n = 0
        for src in EXPERIMENT_RUN_SOURCES:
            if src in present:
                n += int(
                    conn.execute(
                        "SELECT COUNT(*) FROM experiment_runs WHERE source_table = ? "
                        f"AND event_id IN (SELECT id FROM {src} WHERE agent = ?)",  # noqa: S608
                        (src, agent),
                    ).fetchone()[0]
                )
        counts["experiment_runs"] = n
    for table in present:
        counts[table] = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE agent = ?", (agent,)  # noqa: S608
            ).fetchone()[0]
        )
    return counts


def drop_agent(
    db_path: str | Path,
    agent: str,
    *,
    execute: bool = False,
    allow_claude: bool = False,
    backup: bool = True,
) -> dict:
    """Delete every mined row belonging to ``agent`` from SIO's database.

    Dry run by default: returns per-table counts that WOULD be deleted and
    writes nothing. With ``execute=True`` a backup is taken, then dependents
    (``pattern_errors``, ``experiment_runs``) and the agent's rows in every
    :data:`AGENT_TABLES` table are deleted in one transaction.

    Only SIO's mined data is touched — the agent's own session files on disk
    (``~/.pi``, ``~/.claude/projects`` ...) are never read or modified.

    ``claude`` is refused unless ``allow_claude=True`` because it is the bulk
    of the data. Unknown agent names raise ``ValueError``.
    """
    validate_agent_name(agent)
    if agent == LEGACY_AGENT and not allow_claude:
        raise ValueError(
            "refusing to drop 'claude' — it is the bulk of the data; "
            "pass --including-claude if you really mean it"
        )
    conn = _open(db_path)
    try:
        counts = _drop_counts(conn, agent)
        report = {
            "agent": agent,
            "db_path": str(db_path),
            "executed": False,
            "counts": counts,
            "backup": None,
        }
        if not execute:
            return report
        backup_path = backup_db(db_path, f"pre-drop-{agent}") if backup else None
        report["backup"] = str(backup_path) if backup_path else None
        present = _present_agent_tables(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for child, fk, parent in DEPENDENT_TABLES:
                if parent in present and _table_exists(conn, child):
                    conn.execute(
                        f"DELETE FROM {child} WHERE {fk} IN "  # noqa: S608
                        f"(SELECT id FROM {parent} WHERE agent = ?)",
                        (agent,),
                    )
            if _table_exists(conn, "experiment_runs"):
                for src in EXPERIMENT_RUN_SOURCES:
                    if src in present:
                        conn.execute(
                            "DELETE FROM experiment_runs WHERE source_table = ? "
                            f"AND event_id IN (SELECT id FROM {src} WHERE agent = ?)",  # noqa: S608
                            (src, agent),
                        )
            for table in present:
                conn.execute(
                    f"DELETE FROM {table} WHERE agent = ?", (agent,)  # noqa: S608
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        report["executed"] = True
        return report
    finally:
        conn.close()


def format_drop_report(report: dict) -> str:
    """Human-readable rendering of a :func:`drop_agent` report."""
    verb = "deleted" if report["executed"] else "DRY RUN — would delete"
    lines = [f"drop-agent {report['agent']} on {report['db_path']}: {verb}"]
    for table, n in report["counts"].items():
        lines.append(f"  {table:20s} {n:8d}")
    lines.append(f"  {'total':20s} {sum(report['counts'].values()):8d}")
    if report.get("backup"):
        lines.append(f"  backup: {report['backup']}")
    if not report["executed"]:
        lines.append("  (nothing written — re-run with --yes to execute)")
    return "\n".join(lines)
