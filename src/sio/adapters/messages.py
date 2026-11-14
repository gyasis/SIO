"""messages — adapter events as the message dicts SIO's analysers read.

The Claude parser emits ONE assistant message per tool call carrying
``tool_input``, ``tool_output`` and ``error``. Adapters emit the call and its
result as two events. :func:`events_to_paired_messages` joins them so the
distiller, the positive-signal extractor, the flow extractor and the session
metrics read every harness in the Claude shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Iterable


def events_to_paired_messages(events: Iterable) -> list[dict]:
    """Adapter events -> message dicts with each tool call joined to its result.

    Adapters emit a tool call (``assistant`` event with ``tool`` set, arguments
    as content) and its result (``tool`` event) separately. The Claude parser
    emits ONE assistant message per call carrying ``tool_input``,
    ``tool_output`` and ``error``; the distiller reads that shape. Results are
    matched to the oldest open call of the same tool name, else the oldest open
    call. Adapters that already carry the whole call in ``raw`` (OpenCode,
    Paperclip) produce one message directly.
    """
    out: list[dict] = []
    pending: dict[str, list[int]] = {}

    def _base(ev, role: str) -> dict:
        return {
            "role": role,
            "content": ev.content or "",
            "tool_name": None,
            "tool_input": None,
            "tool_output": None,
            "error": None,
            "timestamp": ev.ts,
        }

    for ev in events:
        raw = ev.raw if isinstance(ev.raw, dict) else {}
        if ev.tool and ("tool_input" in raw or "tool_output" in raw):
            msg = _base(ev, "assistant")
            msg.update(
                tool_name=ev.tool,
                tool_input=raw.get("tool_input"),
                tool_output=raw.get("tool_output"),
                error=getattr(ev, "error", None) or None,
            )
            out.append(msg)
        elif ev.role == "assistant" and ev.tool:
            msg = _base(ev, "assistant")
            msg.update(content="", tool_name=ev.tool, tool_input=ev.content or "")
            out.append(msg)
            pending.setdefault(ev.tool, []).append(len(out) - 1)
        elif ev.role == "tool":
            queue = pending.get(ev.tool) if ev.tool else None
            if not queue:
                open_calls = [q for q in pending.values() if q]
                queue = min(open_calls, key=lambda q: q[0]) if open_calls else None
            if queue:
                call = out[queue.pop(0)]
                call["tool_output"] = ev.content or ""
                call["error"] = getattr(ev, "error", None) or None
            else:
                out.append(_base(ev, "tool"))
        else:
            out.append(_base(ev, ev.role))
    return out


def pair_message_dicts(messages: Iterable[dict]) -> list[dict]:
    """Pair unpaired message dicts (one per event, as the mine paths build them).

    Each dict carries ``role``, ``content``, ``tool_name``, ``timestamp`` and
    optionally ``tool_input`` / ``tool_output`` / ``error``. A dict with a
    non-empty ``tool_input`` or ``tool_output`` is a whole call already.
    """
    events = []
    for m in messages:
        raw = {k: m[k] for k in ("tool_input", "tool_output") if m.get(k) is not None}
        events.append(
            SimpleNamespace(
                ts=m.get("timestamp") or "",
                role=m.get("role") or "",
                content=m.get("content") or "",
                tool=m.get("tool_name"),
                raw=raw,
                error=m.get("error"),
            )
        )
    return events_to_paired_messages(events)
