"""pi coding-agent session adapter — reads ~/.pi/agent/sessions/**/*.jsonl.

pi (``@earendil-works/pi-coding-agent``) writes one session per file at
``~/.pi/agent/sessions/<cwd-encoded-dir>/<ISO-timestamp>_<uuid>.jsonl``, one
JSON object per line. The shapes below come from pi's own
``core/session-manager.d.ts`` (session entries) and ``core/messages.d.ts`` /
``pi-ai/types.d.ts`` (message roles), not from guessing at a sample file:

* line 1 is the header ``{type:"session", version, id, timestamp, cwd,
  parentSession?}`` -- ``id`` is the uuid that also ends the file name;
* every later line is ``{type, id, parentId, timestamp, ...}`` where ``type``
  is one of ``message`` (the transcript), ``model_change``,
  ``thinking_level_change``, ``compaction``, ``branch_summary``,
  ``session_info``, ``label``, ``custom`` or ``custom_message``;
* a ``message`` entry wraps ``message.role`` = ``user`` (content is a string
  or text/image blocks), ``assistant`` (text / thinking / toolCall blocks,
  a toolCall carrying ``name`` + ``arguments``), ``toolResult`` (``toolName``,
  ``toolCallId``, text blocks, ``isError``), plus pi's own ``bashExecution``
  (a ``!cmd`` the human ran), ``custom``, ``branchSummary`` and
  ``compactionSummary`` roles.

This adapter EXTRACTs those into the same normalised :class:`SessionEvent`
stream :mod:`sio.adapters.claude_code`, :mod:`sio.adapters.kimi` and
:mod:`sio.adapters.codex` produce. A ``toolResult`` with ``isError`` sets
``SessionEvent.error`` so the mining layer files it as a ``tool_failure``
-- that is the friction signal SIO exists to collect. Tails a live session
the same way Claude/Kimi/Codex do (poll file size).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Iterator
from typing import Any

from sio.adapters.base import SessionEvent, SessionManifest


def _text_from_content(content: Any) -> str:
    """Join the ``text`` of pi content blocks into one string.

    pi content is either a bare string (user messages, custom messages) or a
    list of ``{"type": "text", "text": ...}`` / ``{"type": "image", ...}``
    blocks; image blocks carry no text and contribute nothing.
    """
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content or "")


def _arguments_text(arguments: Any) -> str:
    """Render a toolCall's ``arguments`` (a JSON object) as a grep-able string."""
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arguments)


def _assistant_events(
    obj: dict[str, Any], message: dict[str, Any], ts: str, call_names: dict[str, str]
) -> Iterator[SessionEvent]:
    """Expand one assistant message into events, one per content block group.

    An assistant turn interleaves thinking, prose and tool calls in a single
    ``content`` list, so it becomes several events emitted in block order:
    consecutive text blocks merge into ONE ``assistant`` event, each
    ``thinking`` block is a ``system`` event (harness-side reasoning, the same
    filing Codex's ``reasoning`` gets so it never masquerades as the agent's
    answer), and each ``toolCall`` is an ``assistant`` event whose ``tool`` is
    the tool name and whose content is the JSON arguments.
    """
    text_parts: list[str] = []

    def _flush() -> Iterator[SessionEvent]:
        if text_parts:
            yield SessionEvent(
                ts=ts, role="assistant", content=" ".join(text_parts), tool=None, raw=obj
            )
            text_parts.clear()

    for block in message.get("content") or []:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "thinking":
            yield from _flush()
            yield SessionEvent(
                ts=ts, role="system", content=block.get("thinking", ""), tool=None, raw=obj
            )
        elif btype == "toolCall":
            yield from _flush()
            name = block.get("name") or "tool"
            call_id = block.get("id")
            if call_id:
                call_names[call_id] = name
            yield SessionEvent(
                ts=ts,
                role="assistant",
                content=_arguments_text(block.get("arguments")),
                tool=name,
                raw=obj,
            )
    yield from _flush()


def _message_events(
    obj: dict[str, Any], ts: str, call_names: dict[str, str]
) -> Iterator[SessionEvent]:
    """Normalise one ``type:"message"`` entry into zero or more events."""
    message = obj.get("message") or {}
    role = message.get("role") or "unknown"

    if role == "assistant":
        yield from _assistant_events(obj, message, ts, call_names)
        return

    if role == "toolResult":
        name = message.get("toolName") or call_names.get(message.get("toolCallId"), "tool")
        content = _text_from_content(message.get("content"))
        # isError is the friction signal: surface it on SessionEvent.error so
        # the mining layer files a tool_failure, exactly as a Claude
        # tool_result with is_error does on the native path.
        error = content if message.get("isError") else None
        yield SessionEvent(ts=ts, role="tool", content=content, tool=name, raw=obj, error=error)
        return

    if role == "bashExecution":
        # A `!cmd` the HUMAN ran inside pi: the command is a human turn (kept
        # with its `!` prefix, as typed) and its output is a tool result; a
        # non-zero exit code is a failure the same way an isError toolResult is.
        command = str(message.get("command") or "")
        output = str(message.get("output") or "")
        yield SessionEvent(ts=ts, role="user", content=f"!{command}", tool=None, raw=obj)
        exit_code = message.get("exitCode")
        failed = (exit_code not in (None, 0)) or bool(message.get("cancelled"))
        yield SessionEvent(
            ts=ts,
            role="tool",
            content=output,
            tool="bash",
            raw=obj,
            error=(output or f"exit code {exit_code}") if failed else None,
        )
        return

    if role in ("branchSummary", "compactionSummary"):
        yield SessionEvent(
            ts=ts, role="system", content=str(message.get("summary") or ""), tool=None, raw=obj
        )
        return

    if role == "custom":
        # Extension-injected text: not a human turn, so file it as system
        # (the same reasoning Codex applies to its "developer" role).
        yield SessionEvent(
            ts=ts, role="system", content=_text_from_content(message.get("content")),
            tool=None, raw=obj,
        )
        return

    # user, plus any role a future pi adds: keep the role, join the text.
    yield SessionEvent(
        ts=ts, role=role, content=_text_from_content(message.get("content")), tool=None, raw=obj
    )


def events_from_entry(
    obj: dict[str, Any], call_names: dict[str, str]
) -> Iterator[SessionEvent]:
    """Normalise one parsed session-file line into zero or more events.

    ``call_names`` maps a toolCall ``id`` -> tool ``name`` as calls are seen,
    the fallback for a ``toolResult`` that (unlike real pi output) omits
    ``toolName`` -- the same convention :mod:`sio.adapters.codex` uses.
    """
    ts = obj.get("timestamp", "")
    etype = obj.get("type")

    if etype == "message":
        yield from _message_events(obj, ts, call_names)
    elif etype == "session":
        cwd = obj.get("cwd") or "?"
        version = obj.get("version")
        suffix = f" (pi session v{version})" if version is not None else " (pi)"
        yield SessionEvent(
            ts=ts, role="system", content=f"session started in {cwd}{suffix}", tool=None, raw=obj
        )
    elif etype == "model_change":
        provider = obj.get("provider") or "?"
        model = obj.get("modelId") or "?"
        yield SessionEvent(
            ts=ts, role="system", content=f"model changed to {provider}/{model}",
            tool=None, raw=obj,
        )
    elif etype == "thinking_level_change":
        yield SessionEvent(
            ts=ts, role="system",
            content=f"thinking level set to {obj.get('thinkingLevel') or '?'}",
            tool=None, raw=obj,
        )
    elif etype in ("compaction", "branch_summary"):
        yield SessionEvent(
            ts=ts, role="system", content=str(obj.get("summary") or ""), tool=None, raw=obj
        )
    elif etype == "session_info":
        name = obj.get("name")
        yield SessionEvent(
            ts=ts, role="system", content=f"session named {name}" if name else "",
            tool=None, raw=obj,
        )
    elif etype == "custom_message":
        # Extension-injected context (pi converts it to a user message for the
        # LLM); not a human turn, so system -- same as the "custom" role.
        yield SessionEvent(
            ts=ts, role="system", content=_text_from_content(obj.get("content")),
            tool=None, raw=obj,
        )
    # "custom" (opaque extension state) / "label" (bookmarks) / anything
    # unrecognised: bookkeeping with no transcript content -- drop rather
    # than guess, the same convention Kimi/Codex use for their noise records.


def events_from_lines(lines: Iterable[str]) -> Iterator[SessionEvent]:
    """Normalise raw JSONL lines into events (shared by search and EXTRACT).

    Blank and malformed lines are skipped, so a session that is being written
    right now (a torn final line) still yields everything before it.
    """
    call_names: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        yield from events_from_entry(obj, call_names)


class PiAdapter:
    """EXTRACT events from a pi coding-agent session transcript."""

    agent = "pi"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield every recognised entry in the session as normalised events."""
        with open(manifest.path, encoding="utf-8", errors="replace") as fh:
            yield from events_from_lines(fh)

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
        call_names: dict[str, str] = {}
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
                        if isinstance(obj, dict):
                            yield from events_from_entry(obj, call_names)
                    offset = fh.tell()
            time.sleep(poll_interval)
