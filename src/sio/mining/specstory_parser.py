"""SpecStory markdown parser for SIO mining pipeline.

Parses SpecStory-format conversation files into structured block dicts.

Supported formats
-----------------
**Inline annotation format** (produced by test helpers):

    **Human:** <content>

    ---

    **Assistant:** <content>
    [Tool call: <ToolName> with input <json>]
    [Tool output: <output>]
    [Tool error: <error_text>]

    ---

**Markdown code block format** (produced by the conftest fixture):

    **Human:** <content>

    **Assistant:** <content>

    **Tool call: <ToolName>**
    ```json
    <json>
    ```

    **<ToolName> output:**
    ```
    <output>
    ```

Public API
----------
- ``parse_specstory(file_path)`` — returns ``list[dict]``
- ``extract_timestamp_from_filename(file_path)`` — returns ``str | None``
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Inline annotation pattern — matches a single bracket token (DOTALL for multi-line content).
# Captures: (1) token type, (2) token body.
_RE_BRACKET_TOKEN = re.compile(
    r"\[(Tool call|Tool output|Tool error):\s*(.*?)\]",
    re.DOTALL,
)

# Markdown code block format patterns
_RE_MD_TOOL_CALL_HEADER = re.compile(
    r"^\*\*Tool call:\s*(?P<name>[^\*]+?)\*\*\s*$",
    re.IGNORECASE,
)
_RE_MD_TOOL_OUTPUT_HEADER = re.compile(
    r"^\*\*(?P<name>[^\*]+?)\s+output[^:]*:\*\*\s*$",
    re.IGNORECASE,
)
_RE_MD_TOOL_ERROR_HEADER = re.compile(
    r"^\*\*(?P<name>[^\*]+?)\s+output\s*\(error\)[^:]*:\*\*\s*$",
    re.IGNORECASE,
)

# Role detection
_RE_ROLE_PREFIX = re.compile(r"^\*\*(Human|Assistant):\*\*\s*", re.IGNORECASE)

# Filename timestamp
_RE_FILENAME_TS = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<hour>\d{2})-(?P<min>\d{2})-(?P<sec>\d{2})Z",
)

# Separator line
_RE_SEPARATOR = re.compile(r"^---\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def extract_timestamp_from_filename(file_path: Path) -> str | None:
    """Parse the ISO 8601 timestamp embedded in a SpecStory filename.

    Expected filename format::

        "2026-02-25_10-00-00Z-session-name.md"

    Returns the timestamp as ``"2026-02-25T10:00:00Z"`` or ``None`` when the
    filename does not match the convention.

    Parameters
    ----------
    file_path:
        ``pathlib.Path`` pointing at the SpecStory file.

    Returns
    -------
    str | None
        ISO 8601 timestamp string or ``None``.

    Examples
    --------
    >>> from pathlib import Path
    >>> extract_timestamp_from_filename(Path("2026-02-25_10-00-00Z-session.md"))
    '2026-02-25T10:00:00Z'
    >>> extract_timestamp_from_filename(Path("not-a-specstory.md")) is None
    True
    """
    stem = file_path.name
    m = _RE_FILENAME_TS.match(stem)
    if not m:
        return None
    date = m.group("date")
    hour = m.group("hour")
    minute = m.group("min")
    second = m.group("sec")
    return f"{date}T{hour}:{minute}:{second}Z"


def parse_specstory(file_path: Path) -> list[dict[str, Any]]:
    """Parse a SpecStory markdown file into a list of block dicts.

    Each dict has the following shape::

        {
            "role":       str,          # "human" or "assistant"
            "content":    str,          # raw text of the block
            "tool_calls": list[dict],   # may be empty; each entry has:
                                        #   tool_name  str
                                        #   tool_input str | None
                                        #   tool_output str | None
                                        #   error      str | None
        }

    Two line formats are supported:

    *Inline annotation*
        ``[Tool call: Read with input {"file_path": "/tmp/x.py"}]``

    *Markdown code-block*
        ``**Tool call: Read**`` followed by a JSON fenced block and an output
        block.

    Malformed blocks are skipped (never raise).  An empty or whitespace-only
    file returns ``[]``.

    Parameters
    ----------
    file_path:
        ``pathlib.Path`` pointing at the SpecStory file.

    Returns
    -------
    list[dict]
        Ordered list of parsed blocks.

    Examples
    --------
    >>> from pathlib import Path
    >>> blocks = parse_specstory(Path("session.md"))
    >>> blocks[0]["role"]
    'human'
    """
    try:
        raw = file_path.read_text(encoding="utf-8")
    except (OSError, IOError):
        return []

    if not raw.strip():
        return []

    # Decide which parse strategy to use based on presence of inline markers.
    has_inline = "[Tool call:" in raw
    has_separators = bool(_RE_SEPARATOR.search(raw))

    if has_separators or has_inline:
        return _parse_separator_style(raw)
    else:
        return _parse_markdown_style(raw)


# ---------------------------------------------------------------------------
# Strategy A: separator-delimited blocks (inline annotation format)
# ---------------------------------------------------------------------------


def _parse_separator_style(raw: str) -> list[dict[str, Any]]:
    """Parse files that use ``---`` separators between conversation turns."""
    results: list[dict[str, Any]] = []

    # Split into candidate segments on separator lines.
    segments = _RE_SEPARATOR.split(raw)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        block = _parse_single_block(segment)
        if block is not None:
            results.append(block)

    return results


def _parse_single_block(text: str) -> dict[str, Any] | None:
    """Parse one segment into a block dict.

    Returns ``None`` when no role prefix is found.
    """
    lines = text.splitlines()
    if not lines:
        return None

    # Find the first line that carries a role prefix.
    role: str | None = None
    first_content_line: int = 0
    for idx, line in enumerate(lines):
        m = _RE_ROLE_PREFIX.match(line)
        if m:
            role = m.group(1).lower()
            # The rest of the first role line is part of content.
            lines[idx] = _RE_ROLE_PREFIX.sub("", line, count=1)
            first_content_line = idx
            break

    if role is None:
        return None

    content_lines = lines[first_content_line:]
    content_text = "\n".join(content_lines)
    tool_calls = _extract_inline_tool_calls(content_text)
    content = content_text.strip()

    return {
        "role": role,
        "content": content,
        "tool_calls": tool_calls,
    }


def _extract_inline_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract bracket-annotated tool calls from a block's raw text.

    Tokens take the form::

        [Tool call: <Name> with input <json>]
        [Tool output: <text>]
        [Tool error: <text>]

    Content inside the brackets may span multiple lines (DOTALL matching).
    The function walks the token stream and groups each ``Tool call`` with
    the ``Tool output`` / ``Tool error`` that immediately follows it.
    """
    tool_calls: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for m in _RE_BRACKET_TOKEN.finditer(text):
        token_type = m.group(1)
        token_body = m.group(2)

        if token_type == "Tool call":
            # Flush any in-progress call before starting a new one.
            if current is not None:
                tool_calls.append(current)

            # Parse "Read with input {...}" structure.
            call_pattern = re.match(
                r"(?P<name>\S+)\s+with\s+input\s+(?P<input>.+)",
                token_body,
                re.DOTALL,
            )
            if call_pattern is None:
                # Malformed call line — skip this token.
                current = None
                continue

            tool_name = call_pattern.group("name").strip()
            raw_input = call_pattern.group("input").strip()

            tool_input: str | None
            try:
                json.loads(raw_input)  # validate JSON
                tool_input = raw_input
            except (json.JSONDecodeError, ValueError):
                tool_input = None  # discard malformed JSON

            current = {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": None,
                "error": None,
            }

        elif token_type == "Tool output" and current is not None:
            current["tool_output"] = token_body

        elif token_type == "Tool error" and current is not None:
            current["error"] = token_body

    if current is not None:
        tool_calls.append(current)

    return tool_calls


# ---------------------------------------------------------------------------
# Strategy B: Markdown code block format (conftest fixture format)
# ---------------------------------------------------------------------------


def _parse_markdown_style(raw: str) -> list[dict[str, Any]]:
    """Parse files that embed tool calls as Markdown fenced code blocks.

    This handles the conftest ``sample_specstory_file`` fixture format where
    ``**Tool call: <Tool>**`` headers and backtick code blocks are used.

    The parser splits the raw content on role-header markers and accumulates
    tool calls for each assistant segment.
    """
    results: list[dict[str, Any]] = []

    # Split on role markers while keeping the markers in the parts.
    # Pattern: line starting with **Human:** or **Assistant:**
    role_split_pattern = re.compile(
        r"(?m)^(\*\*(Human|Assistant):\*\*)",
        re.IGNORECASE,
    )

    # Find all role marker positions.
    markers: list[tuple[int, str]] = []
    for m in role_split_pattern.finditer(raw):
        role_str = m.group(2).lower()
        markers.append((m.start(), role_str))

    if not markers:
        return []

    segments: list[tuple[str, str]] = []
    for i, (start, role) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(raw)
        segment_text = raw[start:end]
        segments.append((role, segment_text))

    for role, segment_text in segments:
        # Strip the role prefix from the beginning of the segment.
        content_text = _RE_ROLE_PREFIX.sub("", segment_text, count=1).strip()

        # Extract markdown-style tool calls.
        tool_calls = _extract_md_tool_calls(segment_text)

        results.append(
            {
                "role": role,
                "content": content_text,
                "tool_calls": tool_calls,
            }
        )

    return results


def _extract_md_tool_calls(segment: str) -> list[dict[str, Any]]:
    """Extract tool calls from the Markdown code block format.

    Looks for patterns like::

        **Tool call: <Name>**
        ```json
        <input json>
        ```

        **<Name> output:**
        ```
        <output>
        ```

        **<Name> output (error):**
        ```
        <error text>
        ```
    """
    tool_calls: list[dict[str, Any]] = []
    lines = segment.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Check for tool call header: **Tool call: <Name>**
        m_header = _RE_MD_TOOL_CALL_HEADER.match(line)
        if m_header:
            tool_name = m_header.group("name").strip()
            i += 1

            # Consume the JSON code block (optional).
            tool_input: str | None = None
            tool_output: str | None = None
            error: str | None = None

            # Look for ```json or ``` block.
            if i < len(lines) and lines[i].strip().startswith("```"):
                i += 1  # skip opening fence
                json_lines: list[str] = []
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    json_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    i += 1  # skip closing fence
                raw_json = "\n".join(json_lines).strip()
                try:
                    json.loads(raw_json)
                    tool_input = raw_json
                except (json.JSONDecodeError, ValueError):
                    tool_input = None

            # Skip blank lines.
            while i < len(lines) and not lines[i].strip():
                i += 1

            # Look for output or error block.
            if i < len(lines):
                out_line = lines[i].strip()
                m_err_hdr = _RE_MD_TOOL_ERROR_HEADER.match(out_line)
                m_out_hdr = _RE_MD_TOOL_OUTPUT_HEADER.match(out_line)

                if m_err_hdr:
                    i += 1
                    error = _consume_code_block(lines, i)
                    # Advance past the block.
                    i = _skip_code_block(lines, i)
                elif m_out_hdr:
                    i += 1
                    tool_output = _consume_code_block(lines, i)
                    i = _skip_code_block(lines, i)

            tool_calls.append(
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_output": tool_output,
                    "error": error,
                }
            )
            continue

        i += 1

    return tool_calls


def _consume_code_block(lines: list[str], start: int) -> str | None:
    """Return the content inside a fenced code block beginning at ``start``.

    ``start`` must point to the opening fence line (e.g. ````` ``` ````).
    Returns ``None`` if no fence is found at ``start``.
    """
    if start >= len(lines):
        return None
    if not lines[start].strip().startswith("```"):
        return None
    i = start + 1
    content_lines: list[str] = []
    while i < len(lines) and not lines[i].strip().startswith("```"):
        content_lines.append(lines[i])
        i += 1
    return "\n".join(content_lines)


def _skip_code_block(lines: list[str], start: int) -> int:
    """Advance the index past a fenced code block starting at ``start``.

    Returns the index of the line after the closing fence.
    """
    if start >= len(lines) or not lines[start].strip().startswith("```"):
        return start
    i = start + 1
    while i < len(lines) and not lines[i].strip().startswith("```"):
        i += 1
    return i + 1  # skip closing fence
