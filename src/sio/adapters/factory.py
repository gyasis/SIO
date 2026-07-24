"""Route a session handle to its agent adapter and locate its manifest.

The LOCATE half of the pipeline: turn a handle (``agent:native_id``) into a
:class:`SessionManifest`, and return the right :class:`SessionAdapter` for an
agent. Claude is implemented; other agents raise NotImplementedError until
their adapters land (PRD sio_absorb_session_search, Phase A).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sio.adapters.base import SessionManifest
from sio.core.session_handle import parse_handle

_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_KIMI_SESSIONS = Path.home() / ".kimi-code" / "sessions"
_GOOSE_DB = Path.home() / ".local" / "share" / "goose" / "sessions" / "sessions.db"
_OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def adapter_for(agent: str):
    """Return the :class:`SessionAdapter` for ``agent``.

    Claude, Kimi, Goose and OpenCode use direct adapters (file- or SQLite-
    backed); every other agent that has a ``sio.search.cli`` parser uses the
    generic search-backed adapter. Unknown agents raise NotImplementedError.
    """
    if agent == "claude":
        from sio.adapters.claude_code.adapter import ClaudeAdapter

        return ClaudeAdapter()

    if agent == "kimi":
        from sio.adapters.kimi.adapter import KimiAdapter

        return KimiAdapter()

    if agent == "goose":
        from sio.adapters.goose.adapter import GooseAdapter

        return GooseAdapter()

    if agent == "opencode":
        from sio.adapters.opencode.adapter import OpenCodeAdapter

        return OpenCodeAdapter()

    from sio.search.cli import PARSERS

    if agent in PARSERS:
        from sio.adapters.search_backed import SearchBackedAdapter

        return SearchBackedAdapter(agent)
    raise NotImplementedError(
        f"No adapter for agent '{agent}' — known agents: "
        f"{', '.join(sorted(PARSERS))}."
    )


def manifest_from_handle(handle: str) -> SessionManifest | None:
    """Resolve a session handle to a :class:`SessionManifest`.

    Claude and Kimi resolve to a concrete file on disk; Goose and OpenCode
    resolve to their SQLite store (confirming the session id actually exists
    in it) — all four return ``None`` if not found. Other known agents return
    a generic store-backed manifest; the search-backed adapter locates the
    session by native id within the agent's store.
    """
    agent, native = parse_handle(handle)
    if agent == "claude":
        matches = sorted(_CLAUDE_PROJECTS.rglob(f"{native}.jsonl"))
        if not matches:
            return None
        return SessionManifest(
            agent="claude",
            native_id=native,
            kind="file",
            path=str(matches[0]),
            encoding="jsonl",
        )

    if agent == "kimi":
        # native may be a full "session_<uuid>" dir name or a bare uuid — try
        # the exact dir name first, then fall back to a wildcard uuid match.
        matches = sorted(_KIMI_SESSIONS.glob(f"*/{native}/agents/main/wire.jsonl"))
        if not matches:
            matches = sorted(_KIMI_SESSIONS.glob(f"*/*{native}*/agents/main/wire.jsonl"))
        if not matches:
            return None
        main_wire = matches[0]
        session_dir_name = main_wire.parents[2].name
        return SessionManifest(
            agent="kimi",
            native_id=session_dir_name,
            kind="file",
            path=str(main_wire),
            encoding="jsonl",
        )

    if agent == "goose":
        if not _GOOSE_DB.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{_GOOSE_DB}?mode=ro&immutable=0", uri=True)
        except (sqlite3.Error, OSError):
            return None
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (native,)
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        return SessionManifest(
            agent="goose",
            native_id=native,
            kind="db",
            path=str(_GOOSE_DB),
            encoding="sqlite",
        )

    if agent == "opencode":
        if not _OPENCODE_DB.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{_OPENCODE_DB}?mode=ro&immutable=0", uri=True)
        except (sqlite3.Error, OSError):
            return None
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM session WHERE id = ?", (native,)
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        return SessionManifest(
            agent="opencode",
            native_id=native,
            kind="db",
            path=str(_OPENCODE_DB),
            encoding="sqlite",
        )

    from sio.search.cli import PARSERS

    if agent in PARSERS:
        return SessionManifest(
            agent=agent,
            native_id=native,
            kind="store",
            path="",
            encoding="varies",
        )
    raise NotImplementedError(
        f"manifest resolution for agent '{agent}' is not implemented "
        f"(known agents: {', '.join(sorted(PARSERS))})."
    )
