"""Cross-agent session adapter contract — the EXTRACT layer.

A :class:`SessionAdapter` turns ONE session (located via a
:class:`SessionManifest`) into a stream of normalised :class:`SessionEvent`
objects that SIO's analysis layer consumes, regardless of which coding-agent
harness produced it.

Layering (PRD sio_absorb_session_search):
- LOCATE   — find the session / build a manifest (factory + absorbed search)
- EXTRACT  — this contract: manifest -> events (per-agent adapter)
- ANALYZE  — existing SIO, agent-agnostic once events are normalised
- LIVE     — get_live_stream (Phase B)

Phase A establishes the contract + the Claude adapter; other agents are
explicit NotImplementedError stubs filled in later.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class SessionManifest:
    """Where a single session lives and how to read it."""

    agent: str  # claude | codex | goose | opencode | gemini | aider
    native_id: str  # agent-native session id
    kind: str  # "file" | "db"
    path: str  # filesystem path (file) or db path (db)
    encoding: str = "jsonl"  # jsonl | md | sqlite | ...

    @property
    def handle(self) -> str:
        """Canonical ``agent:native_id`` handle for this session."""
        return f"{self.agent}:{self.native_id}"


@dataclass
class SessionEvent:
    """One normalised event from a session transcript."""

    ts: str
    role: str  # user | assistant | tool | system | ...
    content: str
    tool: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SessionAdapter(Protocol):
    """Contract every per-agent adapter implements."""

    agent: str

    def get_events(self, manifest: SessionManifest) -> Iterable[SessionEvent]:
        """Yield every event in the session (the EXTRACT step)."""
        ...

    def get_live_stream(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Tail a running session in real time (Phase B). Optional."""
        ...
