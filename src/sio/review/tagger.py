"""sio.review.tagger — AI-assisted and human tagging for suggestions.

Public API
----------
    ai_tag(pattern, dataset) -> str
    human_tag(db, suggestion_id, category, note=None) -> bool
    ai_tag_suggestion(db, suggestion_id, pattern, dataset) -> bool
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# AI tagging — generates explanation from pattern + dataset examples
# ---------------------------------------------------------------------------

def ai_tag(pattern: dict, dataset: dict) -> str:
    """Generate an AI explanation string from pattern metadata and dataset examples.

    This is a local heuristic (no LLM call). It summarises the pattern's
    error context and any available examples into a human-readable paragraph.
    """
    tool_name = pattern.get("tool_name") or "unknown tool"
    error_count = int(pattern.get("error_count") or 0)
    description = pattern.get("description") or f"errors with {tool_name}"
    examples = dataset.get("examples") or []

    positive = int(dataset.get("positive_count") or 0)
    negative = int(dataset.get("negative_count") or 0)

    lines = [
        f"Pattern: {description}",
        f"Tool: {tool_name} | Errors: {error_count}",
        f"Dataset: {positive} positive, {negative} negative examples",
    ]

    if examples:
        lines.append(f"Sample ({len(examples)} examples available):")
        for ex in examples[:3]:
            msg = ex.get("message", "")[:80]
            lines.append(f"  - [{ex.get('type', '?')}] {msg}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Human tagging — records user categorization on a suggestion
# ---------------------------------------------------------------------------

def human_tag(
    db: sqlite3.Connection,
    suggestion_id: int,
    category: str,
    note: str | None = None,
) -> bool:
    """Record a human categorization tag on a suggestion.

    Stores the category in ``ai_explanation`` (overwriting any AI tag) and
    optionally sets ``user_note``.  Returns True if the row existed.
    """
    now = datetime.now(timezone.utc).isoformat()
    tag_text = f"[human-tag] {category}"
    if note is not None:
        cur = db.execute(
            "UPDATE suggestions SET ai_explanation = ?, user_note = ?, "
            "reviewed_at = ? WHERE id = ?",
            (tag_text, note, now, suggestion_id),
        )
    else:
        cur = db.execute(
            "UPDATE suggestions SET ai_explanation = ?, reviewed_at = ? "
            "WHERE id = ?",
            (tag_text, now, suggestion_id),
        )
    db.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# AI tag + store — applies ai_tag and persists on the suggestion row
# ---------------------------------------------------------------------------

def ai_tag_suggestion(
    db: sqlite3.Connection,
    suggestion_id: int,
    pattern: dict,
    dataset: dict,
) -> bool:
    """Generate an AI explanation and store it on the suggestion row.

    Does NOT change the suggestion's status.  Returns True if the row existed.
    """
    explanation = ai_tag(pattern, dataset)
    cur = db.execute(
        "UPDATE suggestions SET ai_explanation = ? WHERE id = ?",
        (explanation, suggestion_id),
    )
    db.commit()
    return cur.rowcount > 0
