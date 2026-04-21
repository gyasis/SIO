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

    Audit Round 2 N-R2D.1 (Hunter #2, DSPy): examples now match the
    canonical ``PatternToRule`` signature (sio.core.dspy.signatures.
    PatternToRule) per ``contracts/dspy-module-api.md`` §3:

      inputs  : pattern_description (str), example_errors (list[str]),
                project_context (str)
      outputs : rule_title, rule_body, rule_rationale

    DB fields are mapped thus:
      error_type + pattern_summary     → pattern_description (prefixed)
      error_examples_json              → example_errors (parsed, cap 5)
      (no DB field)                    → project_context (empty default)
      prevention_instructions           → rule_body
      rationale                         → rule_rationale
      target_surface                    → dropped (not an output in new sig)

    Args:
        conn: SQLite connection with SIO schema.

    Returns:
        List of ``dspy.Example`` with ``.with_inputs()`` set to the 3
        canonical input fields.
    """
    import json

    import dspy

    from sio.core.db.queries import get_training_corpus

    rows = get_training_corpus(conn)
    examples = []

    for row in rows:
        # Extract error messages from the stored JSON blob
        example_errors: list[str] = []
        try:
            parsed = json.loads(row["error_examples_json"] or "[]")
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict):
                items = parsed.get("examples", [])
            else:
                items = []
            for ex_row in items[:5]:
                if isinstance(ex_row, dict):
                    msg = ex_row.get("error_text") or ex_row.get("error_message") or \
                        ex_row.get("context_message")
                    if msg:
                        example_errors.append(str(msg)[:500])
                elif isinstance(ex_row, str):
                    example_errors.append(ex_row[:500])
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        if not example_errors:
            # Fallback: use pattern_summary as a single example so the
            # optimizer still has signal to work with
            example_errors = [row["pattern_summary"] or "No example available"]

        pattern_description = (
            f"[{row['error_type']}] {row['pattern_summary']}"
            if row.get("error_type") and row.get("pattern_summary")
            else (row.get("pattern_summary") or row.get("error_type") or "")
        )

        ex = dspy.Example(
            pattern_description=pattern_description,
            example_errors=example_errors,
            project_context="",  # not stored in DB; optimizer tolerates empty
            rule_title=row["rule_title"],
            rule_body=row["prevention_instructions"],
            rule_rationale=row["rationale"],
        ).with_inputs("pattern_description", "example_errors", "project_context")
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

    row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
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
    error_examples_json = json.dumps(
        [
            {
                "source": "suggestion",
                "suggestion_id": suggestion_id,
                "description": description,
            }
        ]
    )

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
