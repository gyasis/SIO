"""JSONL transcript parser for Claude Code session files.

Parses Claude Code JSONL transcripts into a normalised list of dicts.

Real Claude Code wire format (one JSON object per line)::

    {"type":"user","message":{"role":"user","content":"..."},...}
    {"type":"assistant","message":{"role":"assistant","content":[
        {"type":"text","text":"..."},
        {"type":"tool_use","id":"...","name":"Read","input":{...}}
    ]},...}
    {"type":"user","message":{"role":"user","content":[
        {"type":"tool_result","tool_use_id":"...","content":"...","is_error":false}
    ]},...}

Also supports the legacy test-fixture format::

    {"type":"human","message":{"role":"user","content":"..."}}
    {"type":"tool_use","tool_name":"Read","tool_input":{...},"error":"..."}
    {"role":"user","content":"...","tool_name":null,...}

Each returned dict has the following keys:

    role        str            "user" | "assistant"
    content     str            message content or empty string
    tool_name   str | None     populated for tool_use entries
    tool_input  str | None     JSON-serialised string of the tool_input object
    tool_output str | None     output from the tool
    error       str | None     error string when present
    timestamp   str | None     ISO-8601 timestamp, preserved verbatim

    input_tokens                int | None   from message.usage.input_tokens
    output_tokens               int | None   from message.usage.output_tokens
    cache_creation_input_tokens int | None   from message.usage.cache_creation_input_tokens
    cache_read_input_tokens     int | None   from message.usage.cache_read_input_tokens
    cost_usd                    float | None from message.costUsd
    stop_reason                 str | None   from message.stopReason
    is_sidechain                bool | None  from message.isSidechain
    model                       str | None   from message.model
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Internal: tool_result tracking
# ---------------------------------------------------------------------------

# We need to correlate tool_result blocks (which carry errors) with the
# tool_use blocks that produced them.  This dict maps tool_use_id -> record
# so that when we encounter a tool_result we can back-fill the error field.
_ToolUseMap = dict[str, dict[str, Any]]


# Keys added by T008 — token usage, cost, model metadata.
# Every record gets these keys; they are None unless the source line provides them.
_META_FIELD_DEFAULTS: dict[str, Any] = {
    "input_tokens": None,
    "output_tokens": None,
    "cache_creation_input_tokens": None,
    "cache_read_input_tokens": None,
    "cost_usd": None,
    "stop_reason": None,
    "is_sidechain": None,
    "model": None,
}


def _get_camel_or_snake(d: dict[str, Any], snake: str, camel: str) -> Any:
    """Return d[snake] or d[camel], preferring snake_case, else None."""
    val = d.get(snake)
    if val is not None:
        return val
    return d.get(camel)


def _extract_message_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract token-usage / cost / model metadata from a raw wire-format line.

    Handles both camelCase (real wire) and snake_case variants.
    Returns a dict with exactly the keys in ``_META_FIELD_DEFAULTS``.
    """
    meta: dict[str, Any] = dict(_META_FIELD_DEFAULTS)

    # usage lives either on the top-level raw or nested under raw["message"]
    usage: dict[str, Any] = {}
    if isinstance(raw.get("usage"), dict):
        usage = raw["usage"]
    else:
        message = raw.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            usage = message["usage"]

    if usage:
        meta["input_tokens"] = _get_camel_or_snake(
            usage, "input_tokens", "inputTokens"
        )
        meta["output_tokens"] = _get_camel_or_snake(
            usage, "output_tokens", "outputTokens"
        )
        meta["cache_creation_input_tokens"] = _get_camel_or_snake(
            usage, "cache_creation_input_tokens", "cacheCreationInputTokens"
        )
        meta["cache_read_input_tokens"] = _get_camel_or_snake(
            usage, "cache_read_input_tokens", "cacheReadInputTokens"
        )

    meta["cost_usd"] = _get_camel_or_snake(raw, "cost_usd", "costUsd")
    meta["stop_reason"] = _get_camel_or_snake(raw, "stop_reason", "stopReason")
    meta["is_sidechain"] = _get_camel_or_snake(raw, "is_sidechain", "isSidechain")
    meta["model"] = raw.get("model")

    # Fallback: real Claude Code JSONL nests these inside raw['message']
    message = raw.get("message") or {}
    if isinstance(message, dict):
        if meta["cost_usd"] is None:
            meta["cost_usd"] = _get_camel_or_snake(message, "cost_usd", "costUsd")
        if meta["stop_reason"] is None:
            meta["stop_reason"] = _get_camel_or_snake(
                message, "stop_reason", "stopReason"
            )
        if meta["model"] is None:
            meta["model"] = message.get("model")

    return meta


def _serialise_input(tool_input_raw: Any) -> str | None:
    """Serialise a tool_input value to a string."""
    if tool_input_raw is None:
        return None
    if isinstance(tool_input_raw, str):
        return tool_input_raw
    return json.dumps(tool_input_raw, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Real Claude Code format handlers
# ---------------------------------------------------------------------------


def _extract_content_text(content: Any) -> str:
    """Extract displayable text from a message's content field.

    Content can be:
    - A plain string
    - A list of content blocks (text, tool_use, tool_result, etc.)
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _parse_real_user(raw: dict[str, Any], tool_use_map: _ToolUseMap) -> list[dict[str, Any]]:
    """Parse a real Claude Code ``type: "user"`` line.

    User messages can contain:
    - Plain text (string content)
    - tool_result blocks (array content) that carry success/error results
    """
    message: dict[str, Any] = raw.get("message") or {}
    content = message.get("content", "")
    timestamp = raw.get("timestamp")
    records: list[dict[str, Any]] = []

    if isinstance(content, list):
        # Check for tool_result blocks first
        has_tool_results = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                has_tool_results = True
                tool_use_id = block.get("tool_use_id", "")
                result_content = block.get("content", "")
                is_error = block.get("is_error", False)

                # Look up the original tool_use to get tool_name
                original = tool_use_map.get(tool_use_id, {})
                tool_name = original.get("tool_name")
                tool_input = original.get("tool_input")

                error_str: str | None = None
                if is_error:
                    error_str = (
                        result_content
                        if isinstance(result_content, str)
                        else str(result_content)
                    )

                records.append({
                    "role": "assistant",  # treat as assistant context for error extraction
                    "content": "",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_output": result_content if not is_error else None,
                    "error": error_str,
                    "timestamp": timestamp,
                    **_META_FIELD_DEFAULTS,
                })

        # Also emit the text portions as a user message
        text = _extract_content_text(content)
        if text.strip() or not has_tool_results:
            records.append({
                "role": "user",
                "content": text,
                "tool_name": None,
                "tool_input": None,
                "tool_output": None,
                "error": None,
                "timestamp": timestamp,
                **_META_FIELD_DEFAULTS,
            })
    else:
        # Plain string content
        records.append({
            "role": "user",
            "content": _extract_content_text(content),
            "tool_name": None,
            "tool_input": None,
            "tool_output": None,
            "error": None,
            "timestamp": timestamp,
            **_META_FIELD_DEFAULTS,
        })

    return records


def _parse_real_assistant(raw: dict[str, Any], tool_use_map: _ToolUseMap) -> list[dict[str, Any]]:
    """Parse a real Claude Code ``type: "assistant"`` line.

    Assistant messages can contain:
    - text blocks (the assistant's prose)
    - tool_use blocks (tool invocations with name + input)
    """
    message: dict[str, Any] = raw.get("message") or {}
    content = message.get("content", "")
    timestamp = raw.get("timestamp")
    meta = _extract_message_metadata(raw)
    records: list[dict[str, Any]] = []

    # Emit one record for the assistant's text
    text = _extract_content_text(content)
    records.append({
        "role": "assistant",
        "content": text,
        "tool_name": None,
        "tool_input": None,
        "tool_output": None,
        "error": None,
        "timestamp": timestamp,
        **meta,
    })

    # Extract tool_use blocks
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_name = block.get("name")
                tool_input = _serialise_input(block.get("input"))
                tool_use_id = block.get("id", "")

                # Register in the map so tool_results can find it
                tool_use_map[tool_use_id] = {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                }

                records.append({
                    "role": "assistant",
                    "content": "",
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_output": None,
                    "error": None,
                    "timestamp": timestamp,
                    **meta,
                })

    return records


# ---------------------------------------------------------------------------
# Legacy format handlers (test fixtures)
# ---------------------------------------------------------------------------


def _parse_legacy_human_or_assistant(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a normalised record from a legacy human/assistant line."""
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
        **_META_FIELD_DEFAULTS,
    }


def _parse_legacy_tool_use(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalised record from a legacy tool_use line."""
    return {
        "role": "assistant",
        "content": "",
        "tool_name": raw.get("tool_name"),
        "tool_input": _serialise_input(raw.get("tool_input")),
        "tool_output": raw.get("tool_output"),
        "error": raw.get("error"),
        "timestamp": raw.get("timestamp"),
        **_META_FIELD_DEFAULTS,
    }


def _parse_normalised_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Handle a line already in normalised schema (test fixtures)."""
    return {
        "role": raw.get("role") or "",
        "content": raw.get("content") or "",
        "tool_name": raw.get("tool_name"),
        "tool_input": _serialise_input(raw.get("tool_input")),
        "tool_output": raw.get("tool_output"),
        "error": raw.get("error"),
        "timestamp": raw.get("timestamp"),
        **_META_FIELD_DEFAULTS,
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch(
    raw: dict[str, Any],
    tool_use_map: _ToolUseMap,
) -> list[dict[str, Any]]:
    """Route a parsed JSON object to the appropriate extractor.

    Returns a list of normalised dicts (may be empty for skipped entries).
    """
    if not isinstance(raw, dict):
        return []

    entry_type: str | None = raw.get("type")

    # --- Real Claude Code format ---
    if entry_type == "user" and "message" in raw:
        return _parse_real_user(raw, tool_use_map)

    if entry_type == "assistant" and "message" in raw:
        return _parse_real_assistant(raw, tool_use_map)

    # --- Legacy test-fixture format ---
    if entry_type == "human":
        rec = _parse_legacy_human_or_assistant(raw)
        return [rec] if rec else []

    if entry_type == "tool_use" and "tool_name" in raw:
        return [_parse_legacy_tool_use(raw)]

    # No ``type`` field — check for normalised-schema format (has ``role``).
    if "role" in raw:
        return [_parse_normalised_record(raw)]

    # Unknown shape (progress, file-history-snapshot, etc.) — skip.
    return []


def parse_jsonl(file_path: Path) -> list[dict]:
    """Parse a Claude Code JSONL transcript file into a list of normalised dicts.

    Handles both the real Claude Code wire format and the legacy test-fixture
    format.  Corrupt, empty, or non-object lines are silently skipped.
    The function never raises regardless of file contents.

    Args:
        file_path: Path to the ``.jsonl`` file to parse.

    Returns:
        A list of normalised message dicts.  Each dict has the keys:
        ``role``, ``content``, ``tool_name``, ``tool_input``,
        ``tool_output``, ``error``, ``timestamp``,
        ``input_tokens``, ``output_tokens``,
        ``cache_creation_input_tokens``, ``cache_read_input_tokens``,
        ``cost_usd``, ``stop_reason``, ``is_sidechain``, ``model``.
        Returns an empty list for an empty or whitespace-only file.

    Examples:
        >>> records = parse_jsonl(Path("session.jsonl"))
        >>> records[0]["role"]
        'user'
        >>> records[0]["timestamp"]
        '2026-02-25T10:00:00Z'
    """
    records: list[dict] = []
    tool_use_map: _ToolUseMap = {}

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

        new_records = _dispatch(raw, tool_use_map)
        records.extend(new_records)

    return records
