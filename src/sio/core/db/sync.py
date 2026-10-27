"""Per-platform -> sio.db sync (Constitution Principle V reconciliation).

Mirrors behavior_invocations from per-platform DBs into the canonical sio.db.
Write targets remain per-platform; readers see a unified, discriminated view.

Environment overrides (used in tests):
  SIO_DB_PATH          — override canonical DB path
  SIO_PLATFORM_DB_PATH — override per-platform DB path (claude-code only)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from sio.core.constants import DEFAULT_PLATFORM
from sio.core.db.connect import open_db
from sio.core.paths import db_path as _default_db_path
from sio.core.paths import sio_home as _default_sio_home

# ---------------------------------------------------------------------------
# Path resolution (honours env overrides for testing)
# ---------------------------------------------------------------------------


def _sio_db_path() -> Path:
    env = os.environ.get("SIO_DB_PATH")
    return Path(env) if env else _default_db_path()


def _platform_db_path(platform: str) -> Path:
    env = os.environ.get("SIO_PLATFORM_DB_PATH")
    if env:
        return Path(env)
    return _default_sio_home() / platform / "behavior_invocations.db"


# Public constants (tests + scripts import these directly)
SIO_DB = _sio_db_path()
PLATFORM_DBS = {
    DEFAULT_PLATFORM: _platform_db_path(DEFAULT_PLATFORM),
}


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _rebuild_paths() -> tuple[Path, dict[str, Path]]:
    """Re-evaluate path constants; called after env-var mutation in tests."""
    sio = _sio_db_path()
    plat = {DEFAULT_PLATFORM: _platform_db_path(DEFAULT_PLATFORM)}
    return sio, plat


# Every data column of behavior_invocations except `platform`, which the sync sets to the
# destination discriminator rather than trusting the source. Kept as one tuple so the INSERT
# and the SELECT can never drift apart — the original defect was exactly that drift.
_SYNC_COLUMNS: tuple[str, ...] = (
    "session_id", "timestamp", "user_message", "behavior_type", "actual_action",
    "expected_action", "activated", "correct_action", "correct_outcome", "user_satisfied",
    "user_note", "passive_signal", "history_file", "line_start", "line_end",
    "token_count", "latency_ms", "labeled_by", "labeled_at", "tool_name",
    "tool_input", "conversation_pointer",
)


class SyncIntegrityError(RuntimeError):
    """A sync finished without accounting for every source row.

    Raised instead of returning a short count, because the defect this replaces reported
    success after discarding everything: `INSERT OR IGNORE` suppresses NOT NULL and CHECK
    failures as well as UNIQUE conflicts, so 55,348 rows became `0 copied` with no error.
    """


def sync_behavior_invocations(
    since_timestamp: str | None = None,
    detailed: bool = False,
) -> dict:
    """Mirror per-platform behavior_invocations into sio.db. Idempotent.

    Copies EVERY data column. `platform` is set to the destination discriminator; the other
    22 come from the source row.

    Duplicates are skipped by `ON CONFLICT (platform, session_id, timestamp, tool_name)
    DO NOTHING`, which targets the identity index ONLY. A NOT NULL or CHECK violation is no
    longer swallowed — it raises, as it must: `INSERT OR IGNORE` previously hid the missing
    `behavior_type` column and silently dropped every row while reporting success.

    After the copy, every source row in range is confirmed present in the canonical table.
    A shortfall raises `SyncIntegrityError` rather than returning a number the caller would
    read as success.

    Args:
        since_timestamp: ISO-8601 UTC string. Only rows with timestamp >= this are copied.
            None means full sync.
        detailed: when True, each platform maps to
            {"source": int, "copied": int, "already_present": int}.
            When False (default, preserved for existing callers) each platform maps to the
            number of rows copied.

    Returns:
        Mapping of {platform: rows_copied} or, with detailed=True, the three counts.

    Raises:
        SyncIntegrityError: a source row in range is missing from the canonical table.
    """
    sio_path, platform_dbs = _rebuild_paths()
    results: dict[str, dict[str, int]] = {}

    cols = ", ".join(_SYNC_COLUMNS)
    src_cols = ", ".join(f"s.{c}" for c in _SYNC_COLUMNS)

    with open_db(sio_path) as sio_conn:
        for platform, platform_db in platform_dbs.items():
            if not platform_db.exists():
                results[platform] = {"source": 0, "copied": 0, "already_present": 0}
                continue

            alias = f"p_{platform.replace('-', '_')}"
            sio_conn.execute("ATTACH DATABASE ? AS " + alias, (str(platform_db),))
            try:
                # SQLite needs a WHERE on the SELECT of an INSERT..SELECT..ON CONFLICT, or
                # the parser cannot tell the upsert's ON from a join's ON (SQLite upsert docs).
                where, params = " WHERE 1=1", []
                if since_timestamp:
                    where = " WHERE 1=1 AND s.timestamp >= ?"
                    params = [since_timestamp]

                source = sio_conn.execute(
                    f"SELECT COUNT(*) FROM {alias}.behavior_invocations s{where}", params
                ).fetchone()[0]
                before = sio_conn.execute(
                    "SELECT COUNT(*) FROM main.behavior_invocations WHERE platform = ?",
                    (platform,),
                ).fetchone()[0]

                sio_conn.execute(
                    f"""
                    INSERT INTO main.behavior_invocations (platform, {cols})
                    SELECT ?, {src_cols}
                    FROM {alias}.behavior_invocations s
                    {where}
                    ON CONFLICT (platform, session_id, timestamp, tool_name) DO NOTHING
                    """,
                    [platform, *params],
                )
                after = sio_conn.execute(
                    "SELECT COUNT(*) FROM main.behavior_invocations WHERE platform = ?",
                    (platform,),
                ).fetchone()[0]
                copied = after - before

                # Integrity gate: every source row in range must now be present. Counting
                # the copy alone cannot tell a skipped duplicate from a dropped row.
                accounted = sio_conn.execute(
                    f"""
                    SELECT COUNT(*) FROM {alias}.behavior_invocations s
                    WHERE EXISTS (
                        SELECT 1 FROM main.behavior_invocations d
                        WHERE d.platform = ? AND d.session_id = s.session_id
                          AND d.timestamp = s.timestamp
                          AND (d.tool_name IS s.tool_name)
                    ){" AND s.timestamp >= ?" if since_timestamp else ""}
                    """,
                    [platform, *params],
                ).fetchone()[0]
                if accounted < source:
                    raise SyncIntegrityError(
                        f"{platform}: {source} source rows in range, only {accounted} are "
                        f"present after sync ({source - accounted} unaccounted for, "
                        f"{copied} copied this run)"
                    )

                results[platform] = {
                    "source": source,
                    "copied": copied,
                    "already_present": accounted - copied,
                }
            finally:
                sio_conn.execute(f"DETACH DATABASE {alias}")

    if detailed:
        return results
    return {p: v["copied"] for p, v in results.items()}


# ---------------------------------------------------------------------------
# Drift computation (supports sio status SC-009)
# ---------------------------------------------------------------------------


def compute_sync_drift() -> dict[str, dict]:
    """Compute sync drift between per-platform DBs and the canonical sio.db.

    Returns:
        {
          platform: {
            canonical_count: int,
            per_platform_count: int,
            drift_pct: float,   # (per_platform - canonical) / per_platform; 0 if ppc == 0
          }
        }
    """
    sio_path, platform_dbs = _rebuild_paths()
    result: dict[str, dict] = {}

    # Read canonical counts per platform
    canonical_counts: dict[str, int] = {}
    try:
        with open_db(sio_path, read_only=True) as conn:
            for platform in platform_dbs:
                row = conn.execute(
                    "SELECT COUNT(*) FROM behavior_invocations WHERE platform = ?",
                    (platform,),
                ).fetchone()
                canonical_counts[platform] = row[0] if row else 0
    except Exception:
        for platform in platform_dbs:
            canonical_counts.setdefault(platform, 0)

    # Read per-platform counts
    for platform, db_path in platform_dbs.items():
        canonical = canonical_counts.get(platform, 0)

        if not db_path.exists():
            result[platform] = {
                "canonical_count": canonical,
                "per_platform_count": 0,
                "drift_pct": 0.0,
                "row_gap": 0,
            }
            continue

        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute("SELECT COUNT(*) FROM behavior_invocations").fetchone()
            conn.close()
            per_platform = row[0] if row else 0
        except Exception:
            per_platform = 0

        if per_platform == 0:
            drift_pct = 0.0
        else:
            drift_pct = (per_platform - canonical) / per_platform

        result[platform] = {
            "canonical_count": canonical,
            "per_platform_count": per_platform,
            "drift_pct": drift_pct,
            # The absolute gap. A percentage hides real loss at scale: 5% of 35,000 rows is
            # ~1,770 tool calls, which `sio status` reported as "in sync".
            "row_gap": max(0, per_platform - canonical),
        }

    return result
