"""dsh (DeepSeek Harness) session adapter — reads ~/.dsh/sessions/**/session.v4.jsonl.zstd.

Layout: ``~/.dsh/sessions/<encoded-cwd>/<session-id>/session.v4.jsonl.zstd``,
one zstd-compressed JSONL file per session. The session id is the directory
name (``session-<uuid>`` or a bare uuid); the file stem is always ``session.v4``.

Record shape: ``{"type": "<group>/<event>", "seq", "time" (epoch ms), "data"}``,
except the first line, a ``{"type": "session", "id", "cwd", "createdAt"}``
header. Mapped records:

* ``user/message``      -> ``user`` event (``data.content`` text blocks)
* ``assistant/message`` -> ``assistant`` event for text blocks, ``system``
  event for ``reasoning`` blocks. Its ``tool-call`` blocks are skipped: every
  call is also written as a ``tool/call`` record carrying the ``callId`` its
  result refers to, and that record is the one used.
* ``tool/call``         -> ``assistant`` event, ``tool`` = name, content = JSON args
* ``tool/result``       -> ``tool`` event; ``message.isError`` sets ``error``
* ``command/run``       -> ``user`` event (``/name args`` the human typed)
* ``compaction/summary``-> ``system`` event

Everything else (``step/*``, ``turn/*``, ``request/*``, ``job/*``, policy and
title records) is bookkeeping and yields nothing.
"""

from __future__ import annotations

import datetime
import io
import json
from typing import Any, Iterable, Iterator

from sio.adapters.base import SessionEvent, SessionManifest


def _ts(ms: Any) -> str:
    """Epoch milliseconds -> ISO-8601 UTC; empty string when absent."""
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat()


def _text(content: Any, kinds: tuple[str, ...] = ("text",)) -> str:
    """Join the text of content blocks whose type is in ``kinds``."""
    if isinstance(content, str):
        return content
    parts = [
        str(b.get("text", ""))
        for b in content or []
        if isinstance(b, dict) and b.get("type") in kinds
    ]
    return "\n".join(p for p in parts if p)


def events_from_entry(
    obj: dict[str, Any], call_names: dict[str, str] | None = None
) -> Iterator[SessionEvent]:
    """Normalise one dsh record into zero or more events.

    ``call_names`` maps ``callId`` -> tool name across calls, so a
    ``tool/result`` (which carries only the call id) gets its tool name.
    """
    call_names = {} if call_names is None else call_names
    rtype = obj.get("type")
    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
    ts = _ts(obj.get("time"))

    if rtype == "session":
        cwd = obj.get("cwd") or ""
        yield SessionEvent(
            ts=_ts(obj.get("createdAt")),
            role="system",
            content=f"session started in {cwd}" if cwd else "session started",
            tool=None,
            raw=obj,
        )
    elif rtype == "user/message":
        yield SessionEvent(
            ts=ts, role="user", content=_text(data.get("content")), tool=None, raw=obj
        )
    elif rtype == "assistant/message":
        content = (data.get("message") or {}).get("content")
        reasoning = _text(content, ("reasoning",))
        if reasoning:
            yield SessionEvent(ts=ts, role="system", content=reasoning, tool=None, raw=obj)
        text = _text(content)
        if text:
            yield SessionEvent(ts=ts, role="assistant", content=text, tool=None, raw=obj)
    elif rtype == "tool/call":
        args = data.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False)
        name = data.get("name") or "tool"
        if data.get("callId"):
            call_names[data["callId"]] = name
        yield SessionEvent(ts=ts, role="assistant", content=args, tool=name, raw=obj)
    elif rtype == "tool/result":
        message = data.get("message") or {}
        content = _text(message.get("content"))
        error = (content or "isError (empty tool result)") if message.get("isError") else None
        yield SessionEvent(
            ts=ts,
            role="tool",
            content=content,
            tool=call_names.get(message.get("toolCallId") or ""),
            raw=obj,
            error=error,
        )
    elif rtype == "command/run":
        command = f"/{data.get('name', '')}{data.get('args') or ''}".strip()
        yield SessionEvent(ts=ts, role="user", content=command, tool=None, raw=obj)
    elif rtype == "compaction/summary":
        yield SessionEvent(
            ts=ts, role="system", content=_text(data.get("summary")), tool=None, raw=obj
        )


def events_from_lines(lines: Iterable[str]) -> Iterator[SessionEvent]:
    """Normalise decompressed JSONL lines; blank or malformed lines are skipped."""
    call_names: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield from events_from_entry(obj, call_names)


def open_session_lines(path: str) -> io.TextIOBase:
    """Open a dsh session file as text lines, decompressing zstd when needed."""
    if not path.endswith(".zstd"):
        return open(path, encoding="utf-8", errors="replace")
    try:
        import zstandard
    except ImportError as exc:
        raise RuntimeError(
            "Reading dsh sessions needs the 'zstandard' package: pip install zstandard"
        ) from exc
    raw = open(path, "rb")
    reader = zstandard.ZstdDecompressor().stream_reader(raw, closefd=True)
    return io.TextIOWrapper(reader, encoding="utf-8", errors="replace")


class DshAdapter:
    """EXTRACT events from a dsh session transcript."""

    agent = "dsh"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield every recognised record in the session as normalised events."""
        with open_session_lines(manifest.path) as fh:
            yield from events_from_lines(fh)

    def get_live_stream(self, manifest: SessionManifest, **_: Any) -> Iterator[SessionEvent]:
        """Not supported: the transcript is a zstd stream that cannot be tailed by size."""
        raise NotImplementedError(
            "dsh sessions are zstd-compressed and cannot be streamed live; "
            "use 'sio live show' or 'sio distill' instead."
        )
