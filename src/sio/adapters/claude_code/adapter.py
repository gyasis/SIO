"""Claude Code session adapter — reads ~/.claude JSONL transcripts.

The first concrete :class:`SessionAdapter` implementation. Parses a Claude
session's JSONL file into normalised :class:`SessionEvent` objects (the same
shape every future per-agent adapter produces), and tails it live (Phase B).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

from sio.adapters.base import SessionEvent, SessionManifest


def _event_from_entry(entry: dict[str, Any]) -> SessionEvent:
    """Normalise one parsed JSONL entry into a SessionEvent."""
    msg = entry.get("message") or {}
    role = entry.get("type") or msg.get("role") or "unknown"
    blocks = msg.get("content")
    tool = None
    if isinstance(blocks, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in blocks
        )
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tool = b.get("name")
                break
    else:
        content = str(blocks or entry.get("text", ""))
    return SessionEvent(
        ts=entry.get("timestamp", ""),
        role=role,
        content=content,
        tool=tool,
        raw=entry,
    )


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
                yield _event_from_entry(entry)

    def get_live_stream(
        self,
        manifest: SessionManifest,
        *,
        poll_interval: float = 1.0,
        from_start: bool = False,
    ) -> Iterator[SessionEvent]:
        """Tail the session file, yielding new events as they are written.

        Polls file size (lowest-common-denominator across harnesses) and reads
        newly appended JSONL lines. Starts at end of file unless ``from_start``.
        Runs until the caller stops iterating (e.g. KeyboardInterrupt).
        """
        path = manifest.path
        offset = 0 if from_start else os.path.getsize(path)
        while True:
            try:
                size = os.path.getsize(path)
            except OSError:
                time.sleep(poll_interval)
                continue
            if size < offset:  # file truncated / rotated
                offset = 0
            if size > offset:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        yield _event_from_entry(entry)
                    offset = fh.tell()
            time.sleep(poll_interval)
