"""Positive signal extractor — detects user approval, gratitude, and implicit
success signals from parsed conversation messages.

Extracts four signal types from session transcripts:

- **confirmation** — explicit user agreement ("yes exactly", "that's right", "correct")
- **gratitude** — user thanking the assistant ("thanks", "great work", "well done")
- **implicit_approval** — short positive response (<20 words, no negatives) after tool use
- **session_success** — session ends with a positive signal and no pending errors

Exported API
------------
extract_positive_signals(parsed_messages) -> list[dict]
"""

from __future__ import annotations

import re
from typing import Any

# Import the shared positive/negative keyword regexes from flow_extractor
# to avoid duplication.  We extend them with more specific patterns below.
from sio.mining.flow_extractor import _NEGATIVE_KEYWORDS, _POSITIVE_KEYWORDS

# ---------------------------------------------------------------------------
# Compiled patterns — confirmation
# ---------------------------------------------------------------------------
# These go beyond _POSITIVE_KEYWORDS by matching multi-word confirmation phrases.

_CONFIRMATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\byes\s+exactly\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+right\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+correct\b", re.IGNORECASE),
    re.compile(r"\byes\s+that\s+works\b", re.IGNORECASE),
    re.compile(r"\bexactly\s+what\s+i\s+wanted\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+what\s+i\s+(?:want|need)(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\byes,?\s+that['']?s\s+it\b", re.IGNORECASE),
    re.compile(r"\bspot\s+on\b", re.IGNORECASE),
    re.compile(r"\bnailed\s+it\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Compiled patterns — gratitude
# ---------------------------------------------------------------------------
# _POSITIVE_KEYWORDS covers single words like "thanks", "awesome", "great".
# These patterns catch multi-word gratitude phrases.

_GRATITUDE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bthank\s+you\b", re.IGNORECASE),
    re.compile(r"\bthanks\s+(?:a\s+lot|so\s+much|for)\b", re.IGNORECASE),
    re.compile(r"\bgreat\s+(?:work|job)\b", re.IGNORECASE),
    re.compile(r"\bnice\s+(?:work|job|one)\b", re.IGNORECASE),
    re.compile(r"\bwell\s+done\b", re.IGNORECASE),
    re.compile(r"\bawesome\s+(?:work|job)\b", re.IGNORECASE),
    re.compile(r"\bappreciate\s+(?:it|that|the)\b", re.IGNORECASE),
    re.compile(r"\bbeautifully\s+done\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# All positive signal patterns combined (for implicit_approval / session_success)
# ---------------------------------------------------------------------------
# Merges _POSITIVE_KEYWORDS (single-word from flow_extractor) with the
# multi-word patterns defined above.

_ALL_POSITIVE_PATTERNS: list[re.Pattern[str]] = [
    _POSITIVE_KEYWORDS,
    *_CONFIRMATION_PATTERNS,
    *_GRATITUDE_PATTERNS,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_of(msg: dict[str, Any]) -> str:
    """Return a non-None string for a message's displayable content."""
    return msg.get("content") or ""


def _is_confirmation(content: str) -> bool:
    """Return True when *content* matches an explicit confirmation phrase."""
    return any(pat.search(content) for pat in _CONFIRMATION_PATTERNS)


def _is_gratitude(content: str) -> bool:
    """Return True when *content* matches a gratitude phrase.

    Checks both the multi-word gratitude patterns defined here and the
    single-word positive keywords from flow_extractor (e.g. "thanks",
    "awesome") when they appear in isolation-like contexts.
    """
    if any(pat.search(content) for pat in _GRATITUDE_PATTERNS):
        return True
    # Single-word matches from flow_extractor that are gratitude-specific
    gratitude_words = re.compile(r"\b(thanks|awesome|perfect)\b", re.IGNORECASE)
    return bool(gratitude_words.search(content))


def _has_any_positive(content: str) -> bool:
    """Return True when *content* matches any positive signal pattern."""
    return any(pat.search(content) for pat in _ALL_POSITIVE_PATTERNS)


def _has_negative(content: str) -> bool:
    """Return True when *content* contains negative keywords."""
    return bool(_NEGATIVE_KEYWORDS.search(content))


def _word_count(content: str) -> int:
    """Return the number of whitespace-delimited words in *content*."""
    return len(content.split())


def _prev_assistant_content(messages: list[dict[str, Any]], before_idx: int) -> str | None:
    """Return truncated content of the most recent assistant message before *before_idx*."""
    for i in range(before_idx - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            text = _content_of(messages[i])
            if text:
                return text[:200]
    return None


def _prev_tool_name(messages: list[dict[str, Any]], before_idx: int) -> str | None:
    """Return the tool_name from the most recent tool_use record before *before_idx*."""
    for i in range(before_idx - 1, -1, -1):
        tn = messages[i].get("tool_name")
        if tn and messages[i].get("role") == "assistant":
            return tn
    return None


def _has_pending_errors(messages: list[dict[str, Any]], up_to_idx: int) -> bool:
    """Return True if there are unresolved errors in the recent message window.

    Scans backwards from *up_to_idx* looking for error fields.  Stops at the
    first user message that is itself a positive signal (errors before that
    point are considered resolved).
    """
    for i in range(up_to_idx - 1, -1, -1):
        msg = messages[i]
        if msg.get("error"):
            return True
        # If we hit a prior positive user message, treat earlier errors as resolved
        if msg.get("role") in ("human", "user") and _has_any_positive(_content_of(msg)):
            break
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_positive_signals(
    parsed_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify parsed conversation messages into positive signal dicts.

    Parameters
    ----------
    parsed_messages:
        List of message dicts produced by ``jsonl_parser.parse_jsonl``.
        Each dict must carry at minimum: ``role``, ``content``.
        Optional fields: ``tool_name``, ``error``, ``timestamp``.

    Returns
    -------
    list[dict]
        Zero or more signal dicts, each containing:

        - ``signal_type`` — one of ``"confirmation"``, ``"gratitude"``,
          ``"implicit_approval"``, ``"session_success"``
        - ``signal_text`` — the user's actual message text
        - ``context_before`` — what the assistant did before this signal
          (previous assistant message content, truncated to 200 chars)
        - ``tool_name`` — the tool executed before the signal, or ``None``
        - ``timestamp`` — from the user message, or ``None``
    """
    if not parsed_messages:
        return []

    signals: list[dict[str, Any]] = []

    for idx, msg in enumerate(parsed_messages):
        role: str = msg.get("role", "")
        content: str = _content_of(msg)

        # Only human/user messages can emit positive signals
        if role not in ("human", "user"):
            continue

        # Skip tool_result messages (they echo tool names in user role)
        if msg.get("tool_name"):
            continue

        if not content.strip():
            continue

        timestamp: str | None = msg.get("timestamp")
        context_before = _prev_assistant_content(parsed_messages, idx)
        tool_name = _prev_tool_name(parsed_messages, idx)

        base = {
            "context_before": context_before,
            "tool_name": tool_name,
            "timestamp": timestamp,
        }

        # 1. Confirmation — explicit agreement phrases
        if _is_confirmation(content):
            signals.append(
                {
                    "signal_type": "confirmation",
                    "signal_text": content,
                    **base,
                }
            )
            continue  # most specific wins; don't double-count

        # 2. Gratitude — thankful phrasing
        if _is_gratitude(content):
            signals.append(
                {
                    "signal_type": "gratitude",
                    "signal_text": content,
                    **base,
                }
            )
            continue

        # 3. Implicit approval — short positive response after tool execution
        #    Requirements: <20 words, no negative keywords, preceded by tool use
        if (
            tool_name is not None
            and _word_count(content) < 20
            and not _has_negative(content)
            and _has_any_positive(content)
        ):
            signals.append(
                {
                    "signal_type": "implicit_approval",
                    "signal_text": content,
                    **base,
                }
            )

    # 4. Session success — session ends with a positive signal and no pending errors
    #    Check the last user message in the conversation
    last_user_idx: int | None = None
    for i in range(len(parsed_messages) - 1, -1, -1):
        msg = parsed_messages[i]
        if (
            msg.get("role") in ("human", "user")
            and not msg.get("tool_name")
            and _content_of(msg).strip()
        ):
            last_user_idx = i
            break

    if last_user_idx is not None:
        last_content = _content_of(parsed_messages[last_user_idx])
        if _has_any_positive(last_content) and not _has_pending_errors(
            parsed_messages, last_user_idx
        ):
            # Emit session_success as a distinct signal type even if
            # the same message was already classified as gratitude/confirmation.
            # Only deduplicate if session_success was already emitted for this
            # exact message (prevents true duplicates, not cross-type overlap).
            already_session_success = any(
                s["signal_type"] == "session_success" and s["signal_text"] == last_content
                for s in signals
            )
            if not already_session_success:
                signals.append(
                    {
                        "signal_type": "session_success",
                        "signal_text": last_content,
                        "context_before": _prev_assistant_content(parsed_messages, last_user_idx),
                        "tool_name": _prev_tool_name(parsed_messages, last_user_idx),
                        "timestamp": parsed_messages[last_user_idx].get("timestamp"),
                    }
                )

    return signals
