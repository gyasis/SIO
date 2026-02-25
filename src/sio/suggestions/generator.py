"""sio.suggestions.generator — improvement proposal generation.

Public API
----------
    generate_suggestions(patterns, datasets, db_conn) -> list[dict]

For each pattern that has a matching dataset entry one suggestion dict is
produced.  Patterns without a corresponding dataset entry are silently skipped.

Suggestion dict schema
----------------------
    pattern_id      (int)   integer row-id of the patterns row
    dataset_id      (int)   integer row-id of the datasets row
    description     (str)   human-readable summary
    confidence      (float) 0.0–1.0 quality signal
    proposed_change (str)   rule text to add to the target config file
    target_file     (str)   destination config file path
    change_type     (str)   one of "claude_md_rule", "skill_md_update", "hook_config"
    status          (str)   always "pending" for freshly generated suggestions
"""

from __future__ import annotations

import sqlite3
from typing import Any

from sio.suggestions.confidence import score_confidence

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_CHANGE_TYPE = "claude_md_rule"
_DEFAULT_TARGET_FILE = "CLAUDE.md"

# Map change_type -> canonical target file
_TARGET_FILE_MAP: dict[str, str] = {
    "claude_md_rule": "CLAUDE.md",
    "skill_md_update": "SKILL.md",
    "hook_config": ".claude/hooks",
}


def _infer_change_type(pattern: dict) -> str:
    """Infer the change type from pattern metadata.

    Rules
    -----
    - Patterns whose tool_name contains "hook" → "hook_config"
    - Patterns whose tool_name contains "skill" → "skill_md_update"
    - Everything else (the vast majority) → "claude_md_rule"
    """
    tool_name: str = (pattern.get("tool_name") or "").lower()
    if "hook" in tool_name:
        return "hook_config"
    if "skill" in tool_name:
        return "skill_md_update"
    return _DEFAULT_CHANGE_TYPE


def _build_proposed_change(pattern: dict) -> str:
    """Generate a proposed CLAUDE.md rule from pattern context.

    The rule is intentionally specific enough to reference the pattern's
    tool_name so it reads as contextually relevant (not a generic placeholder).
    """
    tool_name: str = pattern.get("tool_name") or "the tool"
    description: str = pattern.get("description") or f"errors with {tool_name}"

    lines = [
        f"## Rule: {tool_name} Error Prevention",
        "",
        f"When using {tool_name}, always verify preconditions before the call.",
        f"Pattern observed: {description}",
        "",
        f"- Check that required inputs exist and are valid before calling {tool_name}.",
        f"- Handle errors from {tool_name} gracefully and retry with corrected inputs.",
        "- Log failures with enough context to diagnose the root cause.",
    ]
    return "\n".join(lines)


def _build_description(pattern: dict, dataset: dict) -> str:
    """Build a human-readable description for the suggestion."""
    tool_name: str = pattern.get("tool_name") or "unknown tool"
    error_count: int = int(pattern.get("error_count") or 0)
    positive_count: int = int(dataset.get("positive_count") or 0)
    negative_count: int = int(dataset.get("negative_count") or 0)
    return (
        f"Improve reliability of {tool_name}: "
        f"{error_count} error(s) detected across sessions "
        f"({positive_count} positive / {negative_count} negative examples in dataset)."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_suggestions(
    patterns: list[dict[str, Any]],
    datasets: dict[str, dict[str, Any]],
    db_conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Generate improvement suggestion dicts from ranked patterns and datasets.

    Parameters
    ----------
    patterns:
        List of pattern dicts produced by the clusterer/ranker.  Each dict
        must have at minimum: ``id`` (int row-id), ``pattern_id`` (str),
        ``description``, ``tool_name``, ``error_count``, ``rank_score``.
    datasets:
        Mapping of pattern_id (str) to dataset metadata dicts.  Each metadata
        dict must carry: ``id`` (int row-id), ``pattern_id``, ``positive_count``,
        ``negative_count``, ``file_path``.
    db_conn:
        Open SQLite connection (used for any future DB operations; not mutated
        by this function in the current implementation).

    Returns
    -------
    list[dict]
        One suggestion dict per pattern that has a matching dataset entry.
        Patterns without a dataset entry are silently skipped.
    """
    suggestions: list[dict[str, Any]] = []

    for pattern in patterns:
        pattern_str_id: str = pattern.get("pattern_id", "")
        dataset = datasets.get(pattern_str_id)
        if dataset is None:
            continue

        change_type = _infer_change_type(pattern)
        target_file = _TARGET_FILE_MAP.get(change_type, _DEFAULT_TARGET_FILE)
        confidence = score_confidence(pattern, dataset)
        proposed_change = _build_proposed_change(pattern)
        description = _build_description(pattern, dataset)

        suggestion: dict[str, Any] = {
            "pattern_id": int(pattern["id"]),
            "dataset_id": int(dataset["id"]),
            "description": description,
            "confidence": float(confidence),
            "proposed_change": proposed_change,
            "target_file": target_file,
            "change_type": change_type,
            "status": "pending",
        }
        suggestions.append(suggestion)

    return suggestions
