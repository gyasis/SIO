"""Route a session handle to its agent adapter and locate its manifest.

The LOCATE half of the pipeline: turn a handle (``agent:native_id``) into a
:class:`SessionManifest`, and return the right :class:`SessionAdapter` for an
agent. Claude is implemented; other agents raise NotImplementedError until
their adapters land (PRD sio_absorb_session_search, Phase A).
"""

from __future__ import annotations

from pathlib import Path

from sio.adapters.base import SessionManifest
from sio.core.session_handle import parse_handle

_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def adapter_for(agent: str):
    """Return the :class:`SessionAdapter` for ``agent``.

    Claude uses a direct file adapter; every other agent that has a
    ``sio.search.cli`` parser uses the search-backed adapter. Unknown agents
    raise NotImplementedError.
    """
    if agent == "claude":
        from sio.adapters.claude_code.adapter import ClaudeAdapter

        return ClaudeAdapter()

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

    Claude resolves to a concrete file on disk (returns ``None`` if not found).
    Other known agents return a store-backed manifest; the search-backed
    adapter locates the session by native id within the agent's store.
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
