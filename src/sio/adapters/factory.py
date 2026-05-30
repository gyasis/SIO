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

    Raises NotImplementedError for agents whose adapter is not built yet.
    """
    if agent == "claude":
        from sio.adapters.claude_code.adapter import ClaudeAdapter

        return ClaudeAdapter()
    raise NotImplementedError(
        f"No adapter for agent '{agent}' yet — only 'claude' is implemented "
        "(PRD sio_absorb_session_search, Phase A)."
    )


def manifest_from_handle(handle: str) -> SessionManifest | None:
    """Resolve a session handle to a :class:`SessionManifest`.

    Locates the session's file on disk. Claude only for now. Returns ``None``
    if no matching session file is found.
    """
    agent, native = parse_handle(handle)
    if agent != "claude":
        raise NotImplementedError(
            f"manifest resolution for agent '{agent}' is not implemented yet "
            "(PRD sio_absorb_session_search, Phase A)."
        )
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
