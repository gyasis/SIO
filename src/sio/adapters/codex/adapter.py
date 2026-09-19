"""Codex CLI session adapter — reads ~/.codex/sessions rollout-*.jsonl transcripts.

Codex (the OpenAI Codex CLI) writes one line per structured record to
``~/.codex/sessions/YYYY/MM/DD/rollout-<iso-timestamp>-<uuid>.jsonl``. Each
top-level line is ``{timestamp, ordinal, type, payload}``. ``type`` values
seen in practice: ``session_meta`` (once, always line 1), ``turn_context``,
``world_state``, ``token_usage_record``, ``event_msg`` (harness bookkeeping —
its ``item_completed`` payloads duplicate the richer ``response_item``
records below, so they add nothing extra), and ``response_item`` (the actual
turn content: chat ``message``s, ``custom_tool_call``/``custom_tool_call_output``
pairs, and ``reasoning`` blocks).

This adapter EXTRACTs ``response_item`` content plus ``session_meta`` into
normalised :class:`SessionEvent` objects (the same shape
:mod:`sio.adapters.claude_code` and :mod:`sio.adapters.kimi` produce);
everything else is treated as noise and dropped, the same convention
:mod:`sio.adapters.kimi` uses for its ``llm.request``/``usage.record``/
``context.append_loop_event`` records. Tails a live session the same way
Claude/Kimi do (poll file size).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

from sio.adapters.base import SessionEvent, SessionManifest


def _text_from_blocks(blocks: Any) -> str:
    """Join the ``text`` of a list of content blocks into one string.

    Codex content blocks carry ``text`` (``input_text``/``output_text`` for
    chat messages, plain ``text`` for tool output and reasoning summaries);
    tolerate a bare string or ``None`` too.
    """
    if isinstance(blocks, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in blocks
        )
    return str(blocks or "")


def _event_from_line(obj: dict[str, Any], call_names: dict[str, str]) -> SessionEvent | None:
    """Normalise one parsed rollout-*.jsonl record into a SessionEvent, or None.

    ``call_names`` maps a ``custom_tool_call``'s ``call_id`` -> its tool
    ``name``, built up as calls are seen (they precede their matching
    ``*_output`` in the file) so the output event carries the SAME tool name
    -- otherwise ``--grep`` on a tool name would only ever match the call half
    of the pair, never its result.
    """
    ts = obj.get("timestamp", "")
    rtype = obj.get("type")

    if rtype == "session_meta":
        payload = obj.get("payload") or {}
        cwd = payload.get("cwd") or "?"
        originator = payload.get("originator") or payload.get("cli_version") or "?"
        return SessionEvent(
            ts=ts, role="system", content=f"session started in {cwd} ({originator})",
            tool=None, raw=obj,
        )

    if rtype != "response_item":
        # turn_context / world_state / token_usage_record / event_msg: pure
        # harness bookkeeping (event_msg's item_completed duplicates the
        # response_item records below) -- noise, same as Kimi's
        # llm.request/usage.record/context.append_loop_event.
        return None

    payload = obj.get("payload") or {}
    ptype = payload.get("type")

    if ptype == "message":
        role = payload.get("role") or "unknown"
        # The "developer" role is Codex's injected system-prompt/skills text,
        # not a human turn -- file it as system so it never masquerades as
        # user input under --only user.
        if role == "developer":
            role = "system"
        return SessionEvent(
            ts=ts, role=role, content=_text_from_blocks(payload.get("content")),
            tool=None, raw=obj,
        )

    if ptype == "custom_tool_call":
        name = payload.get("name") or "tool"
        call_id = payload.get("call_id")
        if call_id:
            call_names[call_id] = name
        return SessionEvent(
            ts=ts, role="assistant", content=str(payload.get("input") or ""),
            tool=name, raw=obj,
        )

    if ptype == "custom_tool_call_output":
        name = call_names.get(payload.get("call_id"), "tool")
        return SessionEvent(
            ts=ts, role="tool", content=_text_from_blocks(payload.get("output")),
            tool=name, raw=obj,
        )

    if ptype == "reasoning":
        # encrypted_content is opaque; only the (usually-empty) summary is
        # human-readable.
        return SessionEvent(
            ts=ts, role="system", content=_text_from_blocks(payload.get("summary")),
            tool=None, raw=obj,
        )

    # function_call / local_shell_call / any future response_item shape:
    # unrecognised -- drop rather than guess at its meaning.
    return None


class CodexAdapter:
    """EXTRACT events from a Codex CLI rollout-*.jsonl session transcript."""

    agent = "codex"

    def get_events(self, manifest: SessionManifest) -> Iterator[SessionEvent]:
        """Yield every recognised record in the session as a normalised event."""
        call_names: dict[str, str] = {}
        with open(manifest.path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev = _event_from_line(obj, call_names)
                if ev is not None:
                    yield ev

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
        A ``custom_tool_call`` seen before the tail started falls outside
        ``call_names``, so its matching output falls back to the generic
        "tool" name -- the same best-effort tradeoff Claude/Kimi accept for a
        cold-started live tail.
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
                        ev = _event_from_line(obj, call_names)
                        if ev is not None:
                            yield ev
                    offset = fh.tell()
            time.sleep(poll_interval)
