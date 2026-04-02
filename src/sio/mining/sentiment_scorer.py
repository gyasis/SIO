"""Sentiment scoring for user messages — keyword-frequency-based scoring
and frustration escalation detection.

Exported API
------------
score_sentiment(text: str) -> float
    Returns a score from -1.0 (very negative) to +1.0 (very positive).

detect_frustration_escalation(scores: list[float], texts: list[str] | None = None) -> bool
    Returns True when frustration escalation is detected — either via
    3+ consecutive negative scores or via strong escalation phrases.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled patterns — keyword sets for sentiment scoring
# ---------------------------------------------------------------------------

_POSITIVE_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bthanks\b", re.IGNORECASE),
    re.compile(r"\bperfect\b", re.IGNORECASE),
    re.compile(r"\bgreat\b", re.IGNORECASE),
    re.compile(r"\bgood\b", re.IGNORECASE),
    re.compile(r"\bnice\b", re.IGNORECASE),
    re.compile(r"\bawesome\b", re.IGNORECASE),
    re.compile(r"\bcorrect\b", re.IGNORECASE),
    re.compile(r"\bexactly\b", re.IGNORECASE),
    re.compile(r"\bworks\b", re.IGNORECASE),
    re.compile(r"\byes\b", re.IGNORECASE),
]

_NEGATIVE_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bwrong\b", re.IGNORECASE),
    re.compile(r"\bno\b", re.IGNORECASE),
    re.compile(r"\bfix\b", re.IGNORECASE),
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bbroken\b", re.IGNORECASE),
    re.compile(r"\bfail\b", re.IGNORECASE),
    re.compile(r"\bundo\b", re.IGNORECASE),
    re.compile(r"\brevert\b", re.IGNORECASE),
    re.compile(r"\bstop\b", re.IGNORECASE),
    re.compile(r"\bfrustrated\b", re.IGNORECASE),
    re.compile(r"\bannoying\b", re.IGNORECASE),
    re.compile(r"\bwaste\b", re.IGNORECASE),
]

# Strong escalation phrases — signal acute user frustration.
# These are multi-word phrases that are unambiguous frustration markers.
_ESCALATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfrustrated\b", re.IGNORECASE),
    re.compile(r"\bannoying\b", re.IGNORECASE),
    re.compile(r"\bwaste\s+of\s+time\b", re.IGNORECASE),
    re.compile(r"\bjust\s+do\s+\w+\b", re.IGNORECASE),
    re.compile(r"\bstop\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_matches(text: str, patterns: list[re.Pattern[str]]) -> int:
    """Count the total number of keyword matches in *text*.

    Each pattern can match multiple times via ``findall``; all matches
    across all patterns are summed.
    """
    total = 0
    for pat in patterns:
        total += len(pat.findall(text))
    return total


def _has_escalation_phrase(text: str) -> bool:
    """Return True if *text* contains any strong escalation phrase."""
    return any(pat.search(text) for pat in _ESCALATION_PATTERNS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_sentiment(text: str) -> float:
    """Score the sentiment of *text* using keyword frequency ratios.

    Parameters
    ----------
    text:
        A user message string.

    Returns
    -------
    float
        A score in [-1.0, +1.0] where:
        - +1.0 = entirely positive keywords
        - -1.0 = entirely negative keywords
        -  0.0 = neutral (equal counts or no keywords)

    The formula is::

        score = (positive_count - negative_count) / max(positive_count + negative_count, 1)

    The result is clamped to [-1.0, 1.0].
    """
    if not text or not text.strip():
        return 0.0

    positive_count = _count_matches(text, _POSITIVE_KEYWORDS)
    negative_count = _count_matches(text, _NEGATIVE_KEYWORDS)

    denominator = max(positive_count + negative_count, 1)
    raw_score = (positive_count - negative_count) / denominator

    # Clamp to [-1.0, 1.0] (should already be in range, but defensive)
    return max(-1.0, min(1.0, raw_score))


def detect_frustration_escalation(
    scores: list[float],
    texts: list[str] | None = None,
) -> bool:
    """Detect frustration escalation from a sequence of sentiment scores.

    Parameters
    ----------
    scores:
        List of sentiment scores (each in [-1.0, 1.0]), one per message
        in chronological order.
    texts:
        Optional list of raw message texts, parallel to *scores*.  When
        provided, the texts are scanned for strong escalation phrases
        in addition to the consecutive-negative-score check.

    Returns
    -------
    bool
        True when frustration escalation is detected:
        - 3+ consecutive scores are negative (< 0), OR
        - any text in *texts* contains a strong escalation phrase
          ("frustrated", "annoying", "waste of time", "just do X", "stop")
    """
    # Check 1: 3+ consecutive negative scores
    consecutive_negative = 0
    for s in scores:
        if s < 0:
            consecutive_negative += 1
            if consecutive_negative >= 3:
                return True
        else:
            consecutive_negative = 0

    # Check 2: strong escalation phrases in raw texts
    if texts:
        for text in texts:
            if text and _has_escalation_phrase(text):
                return True

    return False
