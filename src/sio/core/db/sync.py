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

# ---------------------------------------------------------------------------
# Path resolution (honours env overrides for testing)
# ---------------------------------------------------------------------------

def _sio_db_path() -> Path:
    env = os.environ.get("SIO_DB_PATH")
    return Path(env) if env else Path.home() / ".sio" / "sio.db"


def _platform_db_path(platform: str) -> Path:
    env = os.environ.get("SIO_PLATFORM_DB_PATH")
    if env:
        return Path(env)
    return Path.home() / ".sio" / platform / "behavior_invocations.db"


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


def sync_behavior_invocations(since_timestamp: str | None = None) -> dict[str, int]:
    """Mirror per-platform behavior_invocations into sio.db. Idempotent.

    Uses INSERT OR IGNORE against the composite unique index
    (platform, session_id, timestamp, tool_name) so repeated calls copy 0 rows.

    Args:
        since_timestamp: ISO-8601 UTC string. When supplied, only rows with
            timestamp >= since_timestamp are copied. None means full sync.

    Returns:
        Mapping of {platform: rows_copied}.
    """
    sio_path, platform_dbs = _rebuild_paths()
    results: dict[str, int] = {}

    with open_db(sio_path) as sio_conn:
        for platform, platform_db in platform_dbs.items():
            if not platform_db.exists():
                results[platform] = 0
                continue

            alias = f"p_{platform.replace('-', '_')}"
            sio_conn.execute(
                f"ATTACH DATABASE '{platform_db}' AS {alias}"
            )
            try:
                where = (
                    f" WHERE timestamp >= '{since_timestamp}'"
                    if since_timestamp
                    else ""
                )
                cursor = sio_conn.execute(
                    f"""
                    INSERT OR IGNORE INTO behavior_invocations
                        (platform, session_id, timestamp, tool_name, tool_input,
                         user_message, activated, correct_action, correct_outcome,
                         user_satisfied, conversation_pointer)
                    SELECT
                        '{platform}', session_id, timestamp, tool_name, tool_input,
                        user_message, activated, correct_action, correct_outcome,
                        user_satisfied, conversation_pointer
                    FROM {alias}.behavior_invocations
                    {where}
                    """
                )
                results[platform] = cursor.rowcount
            finally:
                sio_conn.execute(f"DETACH DATABASE {alias}")

    return results


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
            }
            continue

        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute(
                "SELECT COUNT(*) FROM behavior_invocations"
            ).fetchone()
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
        }

    return result
