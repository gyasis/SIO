"""Kimi Code CLI session adapter — reads ~/.kimi-code wire.jsonl transcripts.

Kimi Code CLI (``kimi``) writes each agent's turn-by-turn wire log at
``~/.kimi-code/sessions/<workspace>/session_<uuid>/agents/<agent>/wire.jsonl``.
This adapter EXTRACTs the searchable record types (``context.append_message``
and ``turn.prompt`` — see ``sio.search.cli.search_kimi`` for the same logic
applied at search time) into normalised :class:`SessionEvent` objects, and
tails a live session the same way :class:`ClaudeAdapter` does (poll file size).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sio.adapters.base import SessionEvent, SessionManifest


def _kimi_ts(obj: dict[str, Any]) -> str:
    """Convert kimi's epoch-milliseconds ``time`` field to ISO-8601 UTC."""
    ms = obj.get("time")
    if ms is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _event_from_line(obj: dict[str, Any]) -> SessionEvent | None:
    """Normalise one parsed wire.jsonl record into a SessionEvent, or None.

    Only ``context.append_message`` and ``turn.prompt`` carry human-readable
    content; every other record type (loop events, llm.request, usage.record,
    permission/tool/plan_mode bookkeeping, turn.cancel/steer) is noise.
    """
    rtype = obj.get("type")
    if rtype == "context.append_message":
        message = obj.get("message") or {}
        role = message.get("role") or "unknown"
        content = message.get("content")
        if isinstance(content, list):
            text = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        else:
            text = str(content or "")
    elif rtype == "turn.prompt":
        blocks = obj.get("input")
        if isinstance(blocks, list):
            text = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in blocks
            )
        else:
            text = str(blocks or "")
        role = (obj.get("origin") or {}).get("kind") or "user"
    else:
        return None
    return SessionEvent(ts=_kimi_ts(obj), role=role, content=text, tool=None, raw=obj)


class KimiAdapter:
    """EXTRACT events from a Kimi Code CLI wire.jsonl session transcript."""

    agent = "kimi"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield every searchable message in the session's main wire.jsonl."""
        with open(manifest.path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev = _event_from_line(obj)
                if ev is not None:
                    yield ev

    def get_live_stream(
        self,
        manifest: SessionManifest,
        *,
        poll_interval: float = 1.0,
        from_start: bool = False,
    ) -> Iterator[SessionEvent]:
        """Tail the session's wire.jsonl, yielding new events as they're written.

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
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ev = _event_from_line(obj)
                        if ev is not None:
                            yield ev
                    offset = fh.tell()
            time.sleep(poll_interval)
