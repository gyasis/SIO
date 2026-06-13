"""Stage 2 (v2.1) — forward-window extraction.

The actual FIX for an error is the *successful* command/edit that followed it — which
SIO never logs as an error row. To recover it we re-parse the anchor's session JSONL and
read the turns AFTER the error (success-anchored back-propagation, the debate's design).

extract_forward_window(source_file, anchor_ts, tool_name, n) -> str
  Locates the anchor message (by timestamp, tool-name tiebreak) in the transcript and
  returns a compact text of the next ``n`` turns, flagging which tool calls SUCCEEDED
  (error is None) — those are the candidate fixes. Never raises; returns "" on any miss.
"""
from __future__ import annotations

import json
from pathlib import Path

from sio.mining.jsonl_parser import parse_jsonl


def _text(x: object) -> str:
    """Coerce a message field to a flat string (some tool_output/content are lists/dicts)."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    try:
        return json.dumps(x, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(x)


def _locate(msgs: list[dict], anchor_ts: str, tool_name: str | None) -> int | None:
    # exact timestamp + tool match first
    for i, m in enumerate(msgs):
        if m.get("timestamp") == anchor_ts and (
            tool_name is None or m.get("tool_name") == tool_name
        ):
            return i
    # exact timestamp, any tool
    for i, m in enumerate(msgs):
        if m.get("timestamp") == anchor_ts:
            return i
    # first message at/after the anchor time
    for i, m in enumerate(msgs):
        if (m.get("timestamp") or "") >= (anchor_ts or ""):
            return i
    return None


def extract_forward_window(
    source_file: str | None,
    anchor_ts: str | None,
    tool_name: str | None = None,
    n: int = 12,
    max_chars: int = 2200,
) -> str:
    """Compact text of the n turns AFTER the anchor error (successful tools flagged)."""
    if not source_file:
        return ""
    p = Path(source_file)
    if not p.exists():
        return ""
    try:
        msgs = parse_jsonl(p)
    except Exception:  # noqa: BLE001 — parser is meant to never raise, but be safe
        return ""
    idx = _locate(msgs, anchor_ts or "", tool_name)
    if idx is None:
        return ""

    parts: list[str] = []
    for m in msgs[idx + 1: idx + 1 + n]:
        role = m.get("role", "?")
        if m.get("tool_name"):
            ok = "OK" if not m.get("error") else "ERR"
            tin = _text(m.get("tool_input"))[:160].replace("\n", " ")
            out = (_text(m.get("tool_output")) or _text(m.get("error")))[:240].replace("\n", " ")
            parts.append(f"[{role}/{m['tool_name']}/{ok}] {tin} → {out}")
        else:
            c = _text(m.get("content")).strip()[:280].replace("\n", " ")
            if c:
                parts.append(f"[{role}] {c}")
    return "\n".join(parts)[:max_chars]
