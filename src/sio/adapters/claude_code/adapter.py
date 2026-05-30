"""Claude Code session adapter — reads ~/.claude JSONL transcripts.

The first concrete :class:`SessionAdapter` implementation. Parses a Claude
session's JSONL file into normalised :class:`SessionEvent` objects (the same
shape every future per-agent adapter produces).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from sio.adapters.base import SessionEvent, SessionManifest


class ClaudeAdapter:
    """EXTRACT events from a Claude Code JSONL session transcript."""

    agent = "claude"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield every message in the session as a normalised event."""
        with open(manifest.path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message") or {}
                role = entry.get("type") or msg.get("role") or "unknown"
                blocks = msg.get("content")
                tool = None
                if isinstance(blocks, list):
                    content = " ".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in blocks
                    )
                    for b in blocks:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tool = b.get("name")
                            break
                else:
                    content = str(blocks or entry.get("text", ""))
                yield SessionEvent(
                    ts=entry.get("timestamp", ""),
                    role=role,
                    content=content,
                    tool=tool,
                    raw=entry,
                )

    def get_live_stream(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        raise NotImplementedError("live streaming is Phase B")
