"""sio.suggestions.home_file — ranked markdown output for pending suggestions.

Public API
----------
    write_suggestions(suggestions: list[dict], path: str) -> None

Writes a prioritised markdown document grouping suggestions by confidence:

    High Priority    confidence > 0.7
    Medium Priority  0.4 <= confidence <= 0.7
    Low Priority     confidence < 0.4

Each suggestion entry includes:
- Description
- Confidence value
- Proposed change in a fenced code block
- Target file
- ``sio approve <id>`` and ``sio reject <id>`` commands (approve listed first)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any  # noqa: F401

# ---------------------------------------------------------------------------
# Priority bucketing
# ---------------------------------------------------------------------------

_HIGH_THRESHOLD = 0.7
_MEDIUM_THRESHOLD = 0.4


def _priority_bucket(confidence: float) -> str:
    """Return "high", "medium", or "low" based on confidence."""
    if confidence > _HIGH_THRESHOLD:
        return "high"
    if confidence >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render_suggestion(suggestion: dict[str, Any]) -> str:
    """Render a single suggestion as a markdown block."""
    sid: int = suggestion.get("id", 0)
    description: str = suggestion.get("description", "")
    confidence: float = float(suggestion.get("confidence", 0.0))
    proposed_change: str = suggestion.get("proposed_change", "")
    target_file: str = suggestion.get("target_file", "")

    lines: list[str] = [
        f"### Suggestion #{sid}",
        "",
        f"**Description:** {description}",
        "",
        f"**Confidence:** {confidence:.2f}",
        "",
        f"**Target file:** `{target_file}`",
        "",
        "**Proposed change:**",
        "",
        "```",
        proposed_change,
        "```",
        "",
        "**Actions:**",
        "",
        f"`sio approve {sid}`",
        f"`sio reject {sid}`",
        "",
    ]
    return "\n".join(lines)


def _render_section(title: str, suggestions: list[dict[str, Any]]) -> str:
    """Render a priority section heading plus all suggestion entries."""
    lines: list[str] = [f"## {title}", ""]
    for suggestion in suggestions:
        lines.append(_render_suggestion(suggestion))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_suggestions(suggestions: list[dict[str, Any]], path: str) -> None:
    """Write a prioritised markdown file of improvement suggestions.

    Parameters
    ----------
    suggestions:
        List of suggestion dicts.  Each must carry at minimum:
        ``id``, ``description``, ``confidence``, ``proposed_change``,
        ``target_file``.
    path:
        Destination file path (created or overwritten).
    """
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# SIO Improvement Suggestions",
        "",
        "Review each suggestion and approve or reject it using the commands provided.",
        "",
    ]

    if not suggestions:
        lines.append("*No suggestions available at this time.*")
        lines.append("")
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return

    # Bucket suggestions by priority — preserve order within each bucket.
    high: list[dict[str, Any]] = []
    medium: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []

    for suggestion in suggestions:
        confidence = float(suggestion.get("confidence", 0.0))
        bucket = _priority_bucket(confidence)
        if bucket == "high":
            high.append(suggestion)
        elif bucket == "medium":
            medium.append(suggestion)
        else:
            low.append(suggestion)

    # Render sections in High → Medium → Low order regardless of which buckets
    # are populated, so the ordering invariant always holds in the output.
    if high:
        lines.append(_render_section("High Priority", high))
    if medium:
        lines.append(_render_section("Medium Priority", medium))
    if low:
        lines.append(_render_section("Low Priority", low))

    output_path.write_text("\n".join(lines), encoding="utf-8")
