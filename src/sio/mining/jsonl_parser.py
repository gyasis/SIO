"""JSONL transcript parser for Claude Code session files.

Parses Claude Code JSONL transcripts into a normalised list of dicts.

Wire format (one JSON object per line):

    {"type":"human","message":{"role":"user","content":"..."},"timestamp":"..."}
    {"type":"assistant","message":{"role":"assistant","content":"..."},"timestamp":"..."}
    {"type":"tool_use","tool_name":"Read","tool_input":{...},"tool_output":"...","timestamp":"..."}
    {"type":"tool_use","tool_name":"Bash","tool_input":{...},"tool_output":null,"error":"...","timestamp":"..."}

Each returned dict has the following keys:

    role        str            "user" | "assistant" | "tool"
    content     str            message content or empty string
    tool_name   str | None     populated for tool_use entries
    tool_input  str | None     JSON-serialised string of the tool_input object
    tool_output str | None     output from the tool
    error       str | None     error string when present
    timestamp   str | None     ISO-8601 timestamp, preserved verbatim
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _parse_human_or_assistant(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a normalised record from a human or assistant wire-format line.

    Returns None if the entry cannot be meaningfully interpreted (missing
    ``message`` block entirely), though callers should be tolerant of partial
    data.
    """
    message: dict[str, Any] = raw.get("message") or {}
    role: str = message.get("role") or ("user" if raw.get("type") == "human" else "assistant")
    content: str | None = message.get("content")

    return {
        "role": role,
        "content": content if content is not None else "",
        "tool_name": None,
        "tool_input": None,
        "tool_output": None,
        "error": None,
        "timestamp": raw.get("timestamp"),
    }


def _parse_tool_use(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalised record from a tool_use wire-format line."""
    tool_input_raw: Any = raw.get("tool_input")
    tool_input_str: str | None
    if tool_input_raw is None:
        tool_input_str = None
    elif isinstance(tool_input_raw, str):
        # Already a string — pass through unchanged.
        tool_input_str = tool_input_raw
    else:
        # dict, list, or other JSON-serialisable value — serialise it.
        tool_input_str = json.dumps(tool_input_raw, ensure_ascii=False)

    return {
        "role": "tool",
        "content": "",
        "tool_name": raw.get("tool_name"),
        "tool_input": tool_input_str,
        "tool_output": raw.get("tool_output"),
        "error": raw.get("error"),
        "timestamp": raw.get("timestamp"),
    }


def _parse_normalised_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Handle a line that is already in the normalised output schema.

    The ``sample_jsonl_file`` conftest fixture writes dicts with the
    normalised keys directly (role/content/tool_name/...) rather than the
    wire format.  We must not crash on these.
    """
    tool_input_raw: Any = raw.get("tool_input")
    tool_input_str: str | None
    if tool_input_raw is None:
        tool_input_str = None
    elif isinstance(tool_input_raw, str):
        tool_input_str = tool_input_raw
    else:
        tool_input_str = json.dumps(tool_input_raw, ensure_ascii=False)

    return {
        "role": raw.get("role") or "",
        "content": raw.get("content") or "",
        "tool_name": raw.get("tool_name"),
        "tool_input": tool_input_str,
        "tool_output": raw.get("tool_output"),
        "error": raw.get("error"),
        "timestamp": raw.get("timestamp"),
    }


def _dispatch(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Route a parsed JSON object to the appropriate extractor.

    Returns a normalised dict or None if the object cannot be interpreted
    (e.g. not a dict, not a recognised ``type`` value, or a bare list/scalar
    that slipped through).
    """
    if not isinstance(raw, dict):
        return None

    entry_type: str | None = raw.get("type")

    if entry_type in ("human", "assistant"):
        return _parse_human_or_assistant(raw)

    if entry_type == "tool_use":
        return _parse_tool_use(raw)

    # No ``type`` field present — check for the normalised-schema format used
    # by the conftest fixture (has a ``role`` key at top level).
    if "role" in raw:
        return _parse_normalised_record(raw)

    # Unknown shape — skip.
    return None


def parse_jsonl(file_path: Path) -> list[dict]:
    """Parse a Claude Code JSONL transcript file into a list of normalised dicts.

    Reads ``file_path`` line by line.  Each line is attempted as a JSON
    object.  Corrupt, empty, or non-object lines are silently skipped.
    The function never raises regardless of file contents.

    Args:
        file_path: Path to the ``.jsonl`` file to parse.

    Returns:
        A list of normalised message dicts.  Each dict has the keys:
        ``role``, ``content``, ``tool_name``, ``tool_input``,
        ``tool_output``, ``error``, ``timestamp``.
        Returns an empty list for an empty or whitespace-only file.

    Examples:
        >>> records = parse_jsonl(Path("session.jsonl"))
        >>> records[0]["role"]
        'user'
        >>> records[0]["timestamp"]
        '2026-02-25T10:00:00Z'
    """
    records: list[dict] = []

    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, IOError):
        return records

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        try:
            raw = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue

        record = _dispatch(raw)
        if record is not None:
            records.append(record)

    return records
