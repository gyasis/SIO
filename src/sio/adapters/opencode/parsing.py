"""OpenCode part parsing shared by the OpenCode and Paperclip adapters.

An OpenCode ``part`` has the same JSON shape whether it sits in the
``part.data`` column of ``opencode.db`` or arrives as the ``part`` member of an
``opencode run --format json`` event line (which is what a Paperclip
``opencode_local`` agent writes into its run log). Both adapters call
:func:`part_events` so the tool-failure rules live in exactly one place.

Failure signals produced here (all land in ``SessionEvent.error`` so the
existing miner files them; see ``sio.mining.error_extractor``):

* a tool part with ``state.status == "error"``;
* a shell command whose metadata ``exit`` is non-zero, or whose output has a
  line starting ``fatal:`` / ``error:``;
* the SAME shell command run :data:`LOOP_THRESHOLD` times in one session
  (filed as ``repeated_attempt``, once, on the Nth call);
* model chat-template tokens leaking into assistant text.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sio.adapters.base import SessionEvent

LOOP_THRESHOLD = 3

SHELL_TOOLS = frozenset({"bash", "shell", "sh", "zsh", "run_command", "execute"})

# Tool output / input are clipped so one huge `cat` cannot bloat the DB.
_OUTPUT_HEAD = 1200
_OUTPUT_TAIL = 600
_INPUT_MAX = 500

_TEMPLATE_NAMES = (
    r"channel|im_start|im_end|endoftext|start|end|message|return|constrain|"
    r"eot_id|start_header_id|end_header_id|begin_of_text"
)
# `<|channel|>`, `<|im_start|>`, `<channel|>`, `<|channel>` -- at least one pipe,
# so ordinary HTML / generics such as `<end>` never match.
TEMPLATE_LEAK_RE = re.compile(
    rf"<\|(?:{_TEMPLATE_NAMES})\|?>|<(?:{_TEMPLATE_NAMES})\|>", re.IGNORECASE
)
_FAILURE_LINE_RE = re.compile(r"^\s*(?:fatal|error):", re.IGNORECASE | re.MULTILINE)


def iso_ms(epoch_ms: float | int | None) -> str:
    """Convert an epoch-MILLISECONDS stamp to ISO-8601 UTC ('' when unusable)."""
    if epoch_ms is None:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch_ms) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def clip(text: str, head: int = _OUTPUT_HEAD, tail: int = _OUTPUT_TAIL) -> str:
    """Keep the start and end of a long string; errors usually sit at the end."""
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n…[{len(text) - head - tail} chars truncated]…\n{text[-tail:]}"


def store_hash(path: str | Path) -> str:
    """Short stable hash of a store location, used to namespace session ids."""
    resolved = str(Path(path).expanduser().resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]


def normalize_command(command: str) -> str:
    return " ".join(command.split())


def input_summary(tool: str, tool_input: Any) -> str:
    """One readable line for a tool call: the command for shells, else key args."""
    if isinstance(tool_input, dict):
        if tool in SHELL_TOOLS or "command" in tool_input:
            cmd = tool_input.get("command")
            if cmd:
                return clip(str(cmd), _INPUT_MAX, 0)
        try:
            return clip(json.dumps(tool_input, ensure_ascii=False, sort_keys=True), _INPUT_MAX, 0)
        except (TypeError, ValueError):
            return clip(str(tool_input), _INPUT_MAX, 0)
    return clip(str(tool_input or ""), _INPUT_MAX, 0)


class SessionSignals:
    """Per-session state for the signals that need memory (command loops)."""

    def __init__(self, threshold: int = LOOP_THRESHOLD) -> None:
        self.threshold = threshold
        self._seen: dict[str, int] = {}

    def command_seen(self, command: str) -> int:
        key = normalize_command(command)
        self._seen[key] = self._seen.get(key, 0) + 1
        return self._seen[key]


def shell_failure(output: str, metadata: dict[str, Any] | None) -> str | None:
    """Return a one-line reason when a shell result looks like a failure."""
    exit_code = (metadata or {}).get("exit")
    bare = output.strip() in ("", "(no output)")
    # exit 1 with no output is grep/diff/test saying "no match" -- not a failure.
    if isinstance(exit_code, int) and exit_code != 0 and not (exit_code == 1 and bare):
        return f"exit {exit_code}"
    if _FAILURE_LINE_RE.search(output):
        return "output reports fatal:/error:"
    return None


def part_events(
    part: dict[str, Any],
    ts: str,
    role: str,
    signals: SessionSignals | None = None,
) -> list[SessionEvent]:
    """Normalise one OpenCode part into events ([] for reasoning / step markers)."""
    ptype = part.get("type")
    if ptype == "text":
        text = str(part.get("text") or "")
        if not text:
            return []
        leak = TEMPLATE_LEAK_RE.search(text) if role == "assistant" else None
        return [
            SessionEvent(
                ts=ts,
                role=role,
                content=text,
                tool=None,
                raw={"time": ts},
                error=(
                    f"Template token leaked into assistant text: {leak.group(0)} "
                    f"(near: {text[max(0, leak.start() - 40):leak.end() + 40]!r})"
                    if leak
                    else None
                ),
            )
        ]
    if ptype != "tool":
        return []

    tool = str(part.get("tool") or "tool")
    state = part.get("state") or {}
    out = [call_event(tool, state.get("input"), ts, signals)]
    status = state.get("status")
    if status in ("completed", "error"):
        is_error = status == "error"
        body = str(
            (state.get("error") or "tool error")
            if is_error
            else (state.get("output") or (state.get("metadata") or {}).get("output") or "")
        )
        out.append(
            result_event(
                tool, out[0].content, body, ts, is_error=is_error, metadata=state.get("metadata")
            )
        )
    return out


def call_event(
    tool: str, tool_input: Any, ts: str, signals: SessionSignals | None = None
) -> SessionEvent:
    """The assistant's tool call; flags a same-command loop on the Nth repeat."""
    call = SessionEvent(
        ts=ts,
        role="assistant",
        content=input_summary(tool, tool_input),
        tool=tool,
        # loop_checked: this adapter detects same-COMMAND loops itself, so the
        # extractor's coarser same-tool-name run detector must not also fire.
        raw={"tool_input": tool_input, "loop_checked": signals is not None},
    )
    if signals is not None and tool.lower() in SHELL_TOOLS and isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "")
        count = signals.command_seen(command) if command else 0
        if count == signals.threshold:
            call.error = (
                f"Same command run {count} times in one session (loop): {clip(command, 200, 0)}"
            )
            call.error_type = "repeated_attempt"
    return call


def result_event(
    tool: str,
    call_summary: str,
    body: str,
    ts: str,
    *,
    is_error: bool = False,
    metadata: dict[str, Any] | None = None,
) -> SessionEvent:
    """The tool's result; ``error`` is set for harness-flagged or shell failures."""
    if is_error:
        error: str | None = clip(body)
    else:
        reason = shell_failure(body, metadata) if tool.lower() in SHELL_TOOLS else None
        error = (
            f"{reason}: {clip(call_summary, 160, 0)} :: {clip(body, 400, 400)}" if reason else None
        )
    return SessionEvent(
        ts=ts,
        role="tool",
        content=clip(body),
        tool=tool,
        raw={"tool_output": clip(body)},
        error=error,
    )
