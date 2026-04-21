"""Flow extraction from parsed session messages.

Discovers recurring tool sequences (positive patterns) from JSONL transcripts.
No LLM required — pure regex + sequence matching.

Public API
----------
    extract_flows(parsed_messages) -> list[str]
        Returns a compressed tool sequence for one session.

    compute_flow_ngrams(tool_sequence, n_range=(2, 5)) -> list[tuple]
        Returns n-gram tuples from a tool sequence.

    is_success_signal(user_message: str) -> bool
        Checks if a user message indicates success (short + no negative keywords).
"""

from __future__ import annotations

import re

# Negative keywords that indicate failure even in short messages
_NEGATIVE_KEYWORDS = re.compile(
    r"\b(no|wrong|fix|error|stop|undo|revert|broken|fail|bug|issue|bad)\b",
    re.IGNORECASE,
)

# Positive keywords that boost success confidence
_POSITIVE_KEYWORDS = re.compile(
    r"\b(thanks|perfect|awesome|great|nice|good|done|yes|ok|lgtm|ship it)\b",
    re.IGNORECASE,
)


def _extract_extension(tool_input: str | None) -> str:
    """Extract file extension from tool input JSON string."""
    if not tool_input:
        return ""
    # Quick regex for common path patterns
    m = re.search(r'["\']([^"\']+\.(\w{1,6}))["\']', tool_input)
    if m:
        ext = m.group(2).lower()
        if ext in (
            "py",
            "js",
            "ts",
            "tsx",
            "jsx",
            "sql",
            "md",
            "yaml",
            "yml",
            "json",
            "toml",
            "sh",
            "css",
            "html",
            "txt",
            "csv",
            "parquet",
            "rs",
            "go",
            "java",
            "cpp",
            "ipynb",  # FR-026 / audit L1
        ):
            return f".{ext}"
    return ""


def extract_tool_sequence(parsed_messages: list[dict]) -> list[dict]:
    """Extract ordered tool calls with metadata from parsed messages.

    Returns list of dicts: {tool: str, ext: str, timestamp: str, idx: int}
    """
    sequence = []
    for i, msg in enumerate(parsed_messages):
        tool_name = msg.get("tool_name")
        if tool_name and msg.get("role") == "assistant":
            # Skip tool_result messages (they echo the tool name but are user role)
            ext = _extract_extension(msg.get("tool_input"))
            sequence.append(
                {
                    "tool": tool_name,
                    "ext": ext,
                    "timestamp": msg.get("timestamp", ""),
                    "idx": i,
                }
            )
        elif tool_name and msg.get("role") == "user" and msg.get("error"):
            # Tool results with errors — mark for context but don't add to flow
            pass
    return sequence


def compress_rle(sequence: list[dict]) -> list[str]:
    """Run-length encode consecutive identical tool+extension pairs.

    Read(.py) → Read(.py) → Read(.py) becomes "Read(.py)+"
    Read(.py) → Read(.md) stays as two separate entries.
    """
    if not sequence:
        return []

    compressed = []
    prev_key = None
    count = 0

    for item in sequence:
        key = f"{item['tool']}{item['ext']}"
        if key == prev_key:
            count += 1
        else:
            if prev_key is not None:
                if count > 1:
                    compressed.append(f"{prev_key}+")
                else:
                    compressed.append(prev_key)
            prev_key = key
            count = 1

    # Flush last
    if prev_key is not None:
        if count > 1:
            compressed.append(f"{prev_key}+")
        else:
            compressed.append(prev_key)

    return compressed


def compute_ngrams(compressed: list[str], n_range: tuple[int, int] = (2, 5)) -> list[tuple]:
    """Generate n-grams from a compressed tool sequence.

    Returns list of tuples, each tuple is an n-gram.
    """
    ngrams = []
    for n in range(n_range[0], n_range[1] + 1):  # FR-022 / audit M5: upper bound inclusive
        for i in range(len(compressed) - n + 1):
            ngrams.append(tuple(compressed[i : i + n]))
    return ngrams


def indexed_ngrams(
    compressed: list[str], n_range: tuple[int, int] = (2, 5)
) -> list[tuple[tuple[str, ...], int]]:
    """Generate n-grams with their starting index in the compressed sequence.

    Returns list of (ngram_tuple, start_index) pairs, where start_index
    is the position of the first element of the ngram in *compressed*.
    """
    results: list[tuple[tuple[str, ...], int]] = []
    for n in range(n_range[0], n_range[1] + 1):  # FR-022 / audit M5: upper bound inclusive
        for i in range(len(compressed) - n + 1):
            results.append((tuple(compressed[i : i + n]), i))
    return results


def compressed_to_tool_indices(sequence: list[dict]) -> list[list[int]]:
    """Map each position in the RLE-compressed sequence back to tool_sequence indices.

    Given the raw tool sequence (list of dicts with 'tool' and 'ext'),
    returns a list where element ``i`` is the list of tool_sequence indices
    that were collapsed into compressed position ``i``.

    This mirrors the logic of :func:`compress_rle` exactly.
    """
    if not sequence:
        return []

    mapping: list[list[int]] = []
    prev_key = None
    current_indices: list[int] = []

    for idx, item in enumerate(sequence):
        key = f"{item['tool']}{item['ext']}"
        if key == prev_key:
            current_indices.append(idx)
        else:
            if prev_key is not None:
                mapping.append(current_indices)
            prev_key = key
            current_indices = [idx]

    # Flush last group
    if current_indices:
        mapping.append(current_indices)

    return mapping


def is_success_signal(content: str) -> bool:
    """Check if a user message indicates success.

    FR-021 / audit L3: Require an EXPLICIT positive marker to declare success.
    Absence of negative keywords is NOT sufficient — default to was_successful=0
    to avoid false-positive success attribution.

    Success = contains positive keywords (regardless of length).
    Messages that are merely short with no negatives do NOT count as success.
    """
    if not content or not content.strip():
        return False

    # Only explicit positive keywords count as success signals
    if _POSITIVE_KEYWORDS.search(content):
        return True

    return False


def find_success_markers(parsed_messages: list[dict]) -> set[int]:
    """Find indices of messages that are followed by success signals.

    Returns set of message indices where the NEXT user message is a success signal.
    """
    success_indices = set()
    for i, msg in enumerate(parsed_messages):
        if msg.get("role") == "user" and not msg.get("tool_name"):
            content = msg.get("content", "")
            if is_success_signal(content):
                # Mark all preceding tool calls (up to previous user message) as successful
                for j in range(i - 1, -1, -1):
                    msg_j = parsed_messages[j]
                    if msg_j.get("role") == "user" and not msg_j.get("tool_name"):
                        break
                    success_indices.add(j)
    return success_indices


def extract_flows_from_session(
    parsed_messages: list[dict],
) -> dict:
    """Extract flow data from a single session.

    Returns:
        {
            "tool_sequence": list[dict],  # raw tool calls
            "compressed": list[str],       # RLE compressed
            "ngrams": list[tuple],         # n-gram tuples
            "success_indices": set[int],   # message indices near success signals
            "duration_seconds": float,     # session duration
        }
    """
    tool_seq = extract_tool_sequence(parsed_messages)
    compressed = compress_rle(tool_seq)
    ngrams = compute_ngrams(compressed)
    success_indices = find_success_markers(parsed_messages)

    # Calculate duration from timestamps
    duration = 0.0
    timestamps = [t["timestamp"] for t in tool_seq if t["timestamp"]]
    if len(timestamps) >= 2:
        try:
            from datetime import datetime

            first = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
            last = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
            duration = (last - first).total_seconds()
        except (ValueError, TypeError):
            pass

    return {
        "tool_sequence": tool_seq,
        "compressed": compressed,
        "ngrams": ngrams,
        "success_indices": success_indices,
        "duration_seconds": duration,
    }
