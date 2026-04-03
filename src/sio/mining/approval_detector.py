"""Approval detector — classifies user responses after tool calls as approved
or rejected, producing per-tool and aggregate approval rates.

Exported API
------------
detect_approvals(parsed_messages: list[dict]) -> dict

The returned dict contains:
    total_tool_calls  — number of tool_use entries found
    approved          — count of approved tool calls
    rejected          — count of rejected tool calls
    approval_rate     — float in [0.0, 1.0]
    per_tool          — mapping of tool_name -> {approved, rejected, rate}
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Rejection signals — user explicitly disagrees, corrects, or requests reversal.
# Ordered from most specific to shortest to reduce false positives.
_REJECTION_PATTERNS: list[re.Pattern[str]] = [
    # Explicit negation openers
    re.compile(r"(?:^|\s)no[,.]?(?:\s|$)", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+wrong\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+not\s+(?:right|correct|what)\b", re.IGNORECASE),
    re.compile(r"\bnot\s+what\s+i\s+(?:wanted|asked|meant)\b", re.IGNORECASE),
    # Correction language
    re.compile(r"\bwrong\b", re.IGNORECASE),
    re.compile(r"\bundo\b", re.IGNORECASE),
    re.compile(r"\brevert\b", re.IGNORECASE),
    re.compile(r"\brollback\b", re.IGNORECASE),
    re.compile(r"\broll\s+back\b", re.IGNORECASE),
    # User repeating/rephrasing their request (frustration signal)
    re.compile(r"\bi\s+(?:said|meant|asked)\b", re.IGNORECASE),
    # Direct correction
    re.compile(r"\bnot\s+correct\b", re.IGNORECASE),
    re.compile(r"\bno,?\s+actually\b", re.IGNORECASE),
    re.compile(r"\bfix\s+(?:this|that|it)\b", re.IGNORECASE),
    re.compile(r"\btry\s+again\b", re.IGNORECASE),
]

# Explicit approval signals — user positively acknowledges the tool result.
_APPROVAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bthanks\b", re.IGNORECASE),
    re.compile(r"\bthank\s+you\b", re.IGNORECASE),
    re.compile(r"\bperfect\b", re.IGNORECASE),
    re.compile(r"\bgreat\b", re.IGNORECASE),
    re.compile(r"\bgood\b", re.IGNORECASE),
    re.compile(r"\bnice\b", re.IGNORECASE),
    re.compile(r"\bawesome\b", re.IGNORECASE),
    re.compile(r"\bcorrect\b", re.IGNORECASE),
    re.compile(r"\bexactly\b", re.IGNORECASE),
    re.compile(r"\bworks\b", re.IGNORECASE),
    re.compile(r"\blgtm\b", re.IGNORECASE),
    re.compile(r"\byes\b", re.IGNORECASE),
    re.compile(r"\bok\b", re.IGNORECASE),
    re.compile(r"\bdone\b", re.IGNORECASE),
    re.compile(r"\bship\s+it\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_rejection(content: str) -> bool:
    """Return True when *content* matches any rejection phrase."""
    return any(pat.search(content) for pat in _REJECTION_PATTERNS)


def _is_explicit_approval(content: str) -> bool:
    """Return True when *content* matches any explicit approval phrase."""
    return any(pat.search(content) for pat in _APPROVAL_PATTERNS)


def _classify_response(content: str) -> str:
    """Classify a user response as 'approved' or 'rejected'.

    Priority: rejection signals override approval signals (if both present,
    the user is likely correcting while being polite — treat as rejected).
    If neither signal is detected, default to approved (neutral continuation).
    """
    if not content or not content.strip():
        # Empty or whitespace-only response — treat as neutral approval
        # (user did not object).
        return "approved"

    if _is_rejection(content):
        return "rejected"

    # Explicit approval or neutral continuation — both count as approved.
    return "approved"


def _find_tool_calls(parsed_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a list of dicts for each tool_use entry with its index.

    Each dict: {index, tool_name, msg}
    """
    tool_calls: list[dict[str, Any]] = []
    for idx, msg in enumerate(parsed_messages):
        if msg.get("role") == "assistant" and msg.get("tool_name"):
            tool_calls.append({
                "index": idx,
                "tool_name": msg["tool_name"],
                "msg": msg,
            })
    return tool_calls


def _find_next_user_response(
    parsed_messages: list[dict[str, Any]],
    after_idx: int,
) -> str | None:
    """Find the next non-tool-result user message after *after_idx*.

    Skips tool_result messages (user role with tool_name set) and looks
    for the first genuine human response.  Returns None if no user
    message follows before the end of the conversation.
    """
    for i in range(after_idx + 1, len(parsed_messages)):
        msg = parsed_messages[i]
        role = msg.get("role", "")
        if role in ("human", "user") and not msg.get("tool_name"):
            return msg.get("content") or ""
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_approvals(parsed_messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify user responses after tool calls as approved or rejected.

    Parameters
    ----------
    parsed_messages:
        List of message dicts produced by a SpecStory or JSONL parser.
        Each dict must carry at minimum: ``role``, ``content``, ``tool_name``.

    Returns
    -------
    dict with keys:
        total_tool_calls  — int, number of tool_use entries found
        approved          — int, count classified as approved
        rejected          — int, count classified as rejected
        approval_rate     — float in [0.0, 1.0]
        per_tool          — dict mapping tool_name -> {approved, rejected, rate}
    """
    if not parsed_messages:
        return {
            "total_tool_calls": 0,
            "approved": 0,
            "rejected": 0,
            "approval_rate": 0.0,
            "per_tool": {},
        }

    tool_calls = _find_tool_calls(parsed_messages)

    if not tool_calls:
        return {
            "total_tool_calls": 0,
            "approved": 0,
            "rejected": 0,
            "approval_rate": 0.0,
            "per_tool": {},
        }

    approved_total = 0
    rejected_total = 0
    per_tool: dict[str, dict[str, int]] = {}

    for tc in tool_calls:
        tool_name = tc["tool_name"]
        response_content = _find_next_user_response(
            parsed_messages, tc["index"],
        )

        # If no user response follows (end of conversation), treat as
        # approved — user did not object.
        if response_content is None:
            classification = "approved"
        else:
            classification = _classify_response(response_content)

        # Update totals
        if classification == "approved":
            approved_total += 1
        else:
            rejected_total += 1

        # Update per-tool counters
        if tool_name not in per_tool:
            per_tool[tool_name] = {"approved": 0, "rejected": 0}
        per_tool[tool_name][classification] += 1

    total = approved_total + rejected_total
    approval_rate = approved_total / total if total > 0 else 0.0

    # Compute per-tool rates
    per_tool_with_rates: dict[str, dict[str, Any]] = {}
    for name, counts in per_tool.items():
        tool_total = counts["approved"] + counts["rejected"]
        per_tool_with_rates[name] = {
            "approved": counts["approved"],
            "rejected": counts["rejected"],
            "rate": counts["approved"] / tool_total if tool_total > 0 else 0.0,
        }

    return {
        "total_tool_calls": total,
        "approved": approved_total,
        "rejected": rejected_total,
        "approval_rate": approval_rate,
        "per_tool": per_tool_with_rates,
    }
