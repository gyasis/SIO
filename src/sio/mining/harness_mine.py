"""harness_mine — bulk-mine every non-Claude harness, for unattended runs.

``run_mine`` scans the Claude and SpecStory stores. The scheduler and
autoresearch call :func:`mine_other_harnesses` after it so every harness
reaches clustering, datasets and suggestions. Each harness runs on its own:
a failure is logged with the agent name and the next harness still runs.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

#: Every harness except Claude, in the order they are mined.
OTHER_HARNESSES = (
    "omp",
    "pi",
    "dsh",
    "kimi",
    "codex",
    "gemini",
    "goose",
    "opencode",
    "paperclip",
    "aider",
    "promptchain",
)


def db_path_of(conn: sqlite3.Connection) -> str | None:
    """File path of ``conn``'s main database; None for an in-memory database."""
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    return path or None


def mine_other_harnesses(db_path: str | None, since: str) -> dict[str, str]:
    """Bulk-mine every harness in :data:`OTHER_HARNESSES` into ``db_path``.

    Returns ``{agent: "ok" | "failed: <reason>"}``; ``{}`` when ``db_path`` is
    None (an in-memory database has no file another connection can open).
    """
    if not db_path:
        logger.info("mine_other_harnesses: in-memory database, skipped")
        return {}
    from sio.cli.main import (  # noqa: PLC0415
        _STORE_BULK_AGENTS,
        _mine_adapter_bulk,
        _mine_agent_bulk,
    )

    status: dict[str, str] = {}
    for agent in OTHER_HARNESSES:
        try:
            if agent in ("opencode", "paperclip", *_STORE_BULK_AGENTS):
                _mine_adapter_bulk(db_path, agent, since)
            else:
                _mine_agent_bulk(db_path, agent, since)
            status[agent] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Harness mine failed for %s: %s: %s", agent, type(exc).__name__, exc)
            status[agent] = f"failed: {type(exc).__name__}: {exc}"
    return status
