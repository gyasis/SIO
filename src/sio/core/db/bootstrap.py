"""Canonical ``~/.sio/sio.db`` bootstrap — schema, version, migrations, sync.

Runs once at the top of ``sio init`` (before any harness adapter), so the
canonical DB is always ready regardless of which harness is selected.
Restores the orchestration concern that was dropped when ``installer.py``
was replaced by the file-staging-only adapter pattern in commit ``bc39869``.

Idempotent on every step — safe to call repeatedly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_canonical_db_ready(db_path: str | Path | None = None) -> Path:
    """Bring the canonical ``~/.sio/sio.db`` to current schema.

    Performs, in order:
      1. ``init_db()`` — creates base tables (CREATE TABLE IF NOT EXISTS)
         + applies the in-place ALTER migration block in ``schema.py``
         (this is the path that picks up the cycle_id columns from PR #1)
      2. ``ensure_schema_version()`` — seeds the ``schema_version`` table
         with the version=1 baseline row if absent. Without this,
         ``sio status`` reports ``schema_version: n/a (n/a)``.
      3. ``migrate_004()`` — applies the 004 schema delta if not yet
         marked applied. Imported lazily because ``scripts/`` may not
         be on the path in all install layouts.
      4. ``migrate_split_brain.main()`` — one-time mirror of per-platform
         ``behavior_invocations`` rows into the canonical DB. Idempotent
         across runs.

    Args:
        db_path: Override the canonical DB path. Defaults to
            ``$SIO_DB_PATH`` if set, else ``~/.sio/sio.db``.

    Returns:
        The resolved canonical DB path that was bootstrapped.

    Notes:
        Migration failures are logged at debug level and swallowed.
        A migration script that doesn't exist (because the install
        layout doesn't ship ``scripts/``) is also non-fatal — the
        base ``init_db()`` call already creates a usable schema.
    """
    if db_path is None:
        db_path = os.environ.get(
            "SIO_DB_PATH",
            os.path.expanduser("~/.sio/sio.db"),
        )
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Base schema (CREATE IF NOT EXISTS) + in-place ALTER migrations
    from sio.core.db.schema import ensure_schema_version, init_db  # noqa: PLC0415

    conn = init_db(str(db_path))

    # 2. schema_version baseline
    try:
        ensure_schema_version(conn)
    except Exception as exc:
        logger.warning("ensure_schema_version failed on %s: %s", db_path, exc)

    conn.close()

    # 3. 004 migration (idempotent — checks schema_version internally)
    try:
        from scripts.migrate_004 import migrate as migrate_004  # noqa: PLC0415

        migrate_004(str(db_path))
    except Exception as exc:
        logger.debug("migrate_004 skipped on %s: %s", db_path, exc)

    # 4. Split-brain backfill (one-time, idempotent across runs)
    try:
        from scripts.migrate_split_brain import main as split_brain  # noqa: PLC0415

        split_brain()
    except Exception as exc:
        logger.debug("migrate_split_brain skipped: %s", exc)

    return db_path
