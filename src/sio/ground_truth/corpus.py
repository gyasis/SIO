"""sio.ground_truth.corpus -- Load approved ground truth as DSPy training data.

Public API
----------
    load_training_corpus(conn) -> list[dspy.Example]
    promote_to_ground_truth(conn, suggestion_id) -> int
"""

from __future__ import annotations

import sqlite3


def load_training_corpus(conn: sqlite3.Connection) -> list:
    """Load positive-labeled ground truth rows as ``dspy.Example`` objects.

    Each Example has input fields (``error_examples``, ``error_type``,
    ``pattern_summary``) and output fields (``target_surface``, ``rule_title``,
    ``prevention_instructions``, ``rationale``).

    Args:
        conn: SQLite connection with SIO schema.

    Returns:
        List of ``dspy.Example`` with ``.with_inputs()`` set correctly.
    """
    import dspy

    from sio.core.db.queries import get_training_corpus

    rows = get_training_corpus(conn)
    examples = []

    for row in rows:
        ex = dspy.Example(
            error_examples=row["error_examples_json"],
            error_type=row["error_type"],
            pattern_summary=row["pattern_summary"],
            target_surface=row["target_surface"],
            rule_title=row["rule_title"],
            prevention_instructions=row["prevention_instructions"],
            rationale=row["rationale"],
        ).with_inputs("error_examples", "error_type", "pattern_summary")
        examples.append(ex)

    return examples


def promote_to_ground_truth(conn: sqlite3.Connection, suggestion_id: int) -> int:
    """Promote an approved suggestion to a positive ground truth entry.

    Reads the suggestion from the ``suggestions`` table and creates a
    corresponding ``ground_truth`` row with ``label='positive'`` and
    ``source='approved'``.

    Args:
        conn: SQLite connection with SIO schema.
        suggestion_id: The suggestion row ID.

    Returns:
        The new ground_truth row ID.

    Raises:
        ValueError: If the suggestion does not exist.
    """
    import json

    from sio.core.db.queries import insert_ground_truth

    row = conn.execute(
        "SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Suggestion {suggestion_id} not found")

    suggestion = dict(row)

    # Extract fields — suggestions may have target_surface and reasoning_trace
    # from DSPy-generated suggestions, or defaults for template-generated ones
    pattern_id = str(suggestion.get("pattern_id", "unknown"))
    target_surface = suggestion.get("target_surface") or "claude_md_rule"
    description = suggestion.get("description", "")
    proposed_change = suggestion.get("proposed_change", "")

    # Build error_examples_json from the suggestion context
    # For promoted suggestions, we store the description as the example
    error_examples_json = json.dumps([{
        "source": "suggestion",
        "suggestion_id": suggestion_id,
        "description": description,
    }])

    # Extract rule_title from description — try em-dash first, then double-dash
    if " \u2014 " in description:
        rule_title = description.split(" \u2014 ")[0]
    elif " -- " in description:
        rule_title = description.split(" -- ")[0]
    else:
        rule_title = description[:100]
    # Strip [DSPy] prefix if present
    if rule_title.startswith("[DSPy] "):
        rule_title = rule_title[7:]

    gt_id = insert_ground_truth(
        conn,
        pattern_id=pattern_id,
        error_examples_json=error_examples_json,
        error_type="suggestion_promoted",
        pattern_summary=description,
        target_surface=target_surface,
        rule_title=rule_title,
        prevention_instructions=proposed_change,
        rationale=f"Promoted from approved suggestion #{suggestion_id}.",
        source="approved",
        confidence=suggestion.get("confidence"),
        file_path=suggestion.get("target_file"),
        strict=False,  # pattern_id from suggestions; may not exist in patterns table
    )

    # Mark as positive since it was already approved
    from sio.core.db.queries import update_ground_truth_label

    update_ground_truth_label(conn, gt_id, label="positive", source="approved")

    return gt_id
