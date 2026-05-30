"""Generic SessionAdapter backed by the absorbed session-search parsers.

Reuses the proven per-agent parsers in ``sio.search.cli`` (the ``PARSERS``
dispatch) to EXTRACT one session's events for any harness, without
re-implementing each format. This is what makes codex / goose / opencode /
gemini / aider extractable through the same Protocol as Claude.

Efficiency note: this scans the agent's whole store and filters to the target
session id. That is fine for local stores; a per-session locate (like the
Claude adapter's direct file open) is a future optimisation for the
file-per-session agents.
"""

from __future__ import annotations

from collections.abc import Iterator

from sio.adapters.base import SessionEvent, SessionManifest


class SearchBackedAdapter:
    """Adapter for any agent that has a ``sio.search.cli`` parser."""

    def __init__(self, agent: str) -> None:
        self.agent = agent

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield the target session's events via the agent's search parser."""
        from sio.search.cli import PARSERS

        parser = PARSERS.get(self.agent)
        if parser is None:
            raise NotImplementedError(
                f"no session-search parser for agent '{self.agent}'"
            )
        # Empty pattern matches every non-empty event; filter to this session.
        for rec in parser("", False, None):
            if rec.session_id != manifest.native_id:
                continue
            meta = rec.metadata or {}
            yield SessionEvent(
                ts=rec.ts,
                role=rec.role,
                content=rec.content,
                tool=meta.get("tool"),
                raw=meta,
            )

    def get_live_stream(
        self, manifest: SessionManifest, **_kwargs: object
    ) -> Iterator[SessionEvent]:
        raise NotImplementedError(
            f"live watch not supported for agent '{self.agent}' yet (Phase B "
            "currently supports claude)."
        )
