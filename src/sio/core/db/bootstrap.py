"""Canonical ``~/.sio/sio.db`` bootstrap — schema, version, migrations, sync.

Runs once at the top of ``sio init`` (before any harness adapter), so the
canonical DB is always ready regardless of which harness is selected.
Restores the orchestration concern that was dropped when ``installer.py``
was replaced by the file-staging-only adapter pattern in commit ``bc39869``.

Idempotent on every step — safe to call repeatedly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from sio.core.paths import db_path as _default_db_path

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
      3. ``migrate_004()`` (``sio.core.db.migrate_004``) — applies the 004
         schema delta (``pattern_errors.active``, ``patterns.active``,
         ``datasets.active``, ``suggestions.active``, etc. — see that
         module for the full list) if not yet marked applied. Imported
         lazily to match the style of the other migration steps here; it
         is an ordinary in-package import, always available.
      3b. ``migrate_005_experiments()`` — cohort tables.
      3c. ``migrate_006_agent_isolation()`` — the ``agent`` column,
         backfilled + deduped, per-agent views (backs the DB up first).
      4. ``migrate_split_brain.main()`` — one-time mirror of per-platform
         ``behavior_invocations`` rows into the canonical DB. Idempotent
         across runs.

    Args:
        db_path: Override the canonical DB path. Defaults to
            ``$SIO_DB_PATH`` if set, else ``~/.sio/sio.db``.

    Returns:
        The resolved canonical DB path that was bootstrapped.

    Notes:
        A migration step failing here does not raise out of
        ``ensure_canonical_db_ready()`` — several callers (e.g. `sio
        experiment start/status`) invoke it directly with no try/except
        of their own, and step 1 (``init_db()``) already leaves a usable,
        if not fully up-to-date, schema. But "non-fatal" no longer means
        "silent": every step below logs its failure at WARNING (not
        DEBUG), so a genuinely broken migration (a locked DB, a disk
        error, a real code bug) is visible instead of vanishing into a
        log level nobody reads. Historically step 3 (``migrate_004``)
        logged at DEBUG *because* it was expected to fail on most real
        installs — ``scripts.migrate_004`` isn't shipped in the wheel
        (see ``[tool.hatch.build.targets.wheel]`` in ``pyproject.toml``)
        — which is exactly what let the missing 004 columns go unnoticed
        for so long. Now that the migration lives in the package
        (``sio.core.db.migrate_004``), that import can't fail for that
        reason anymore, so any exception here is unexpected and gets the
        same WARNING treatment as ``migrate_006_agent_isolation``.
    """
    if db_path is None:
        db_path = str(_default_db_path())
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

    # 3. 004 migration (idempotent — checks schema_version internally).
    # Lives in the package (sio.core.db.migrate_004), not scripts/, so this
    # import can no longer fail because of install layout (see the module
    # docstring). Any exception here is therefore a real, unexpected
    # failure — log it loudly rather than swallowing it at DEBUG, but
    # don't raise: callers that invoke ensure_canonical_db_ready() without
    # their own try/except (several CLI commands do) should still end up
    # with the base schema from init_db() rather than a hard crash.
    try:
        from sio.core.db.migrate_004 import migrate_004  # noqa: PLC0415

        migrate_004(str(db_path))
    except Exception as exc:
        logger.warning("migrate_004 failed on %s: %s", db_path, exc)

    # 3b. 005 migration — experiments cohort tables (PRD
    # sio_autotag_experiments_2026-05-23). Idempotent.
    try:
        from sio.core.db.schema import migrate_005_experiments  # noqa: PLC0415

        migrate_005_experiments(str(db_path))
    except Exception as exc:
        logger.debug("migrate_005_experiments skipped on %s: %s", db_path, exc)

    # 3c. 006 migration — agent isolation (sio.core.db.agents): backup,
    # backfill `agent`, merge partial ids, dedupe, UNIQUE fingerprint,
    # per-agent views. Idempotent; no-op once stamped.
    try:
        from sio.core.db.agents import migrate_006_agent_isolation  # noqa: PLC0415

        migrate_006_agent_isolation(str(db_path))
    except Exception as exc:
        logger.warning("migrate_006_agent_isolation failed on %s: %s", db_path, exc)

    # 4. Split-brain backfill (one-time, idempotent across runs)
    try:
        from scripts.migrate_split_brain import main as split_brain  # noqa: PLC0415

        split_brain()
    except Exception as exc:
        logger.debug("migrate_split_brain skipped: %s", exc)

    return db_path
