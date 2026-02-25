"""Auto-labeler — infers binary labels from tool output and error signals."""

from __future__ import annotations


def auto_label(
    tool_name: str,
    tool_input: str,
    tool_output: str | None,
    error: str | None,
) -> dict:
    """Infer binary labels from tool execution results.

    Returns:
        Dict with keys: activated (int), correct_action (int), correct_outcome (int).
    """
    # activated: did the tool run? None means it didn't fire at all.
    # Empty string means it ran but produced no useful output.
    activated = 1 if tool_output is not None else 0

    # correct_action: no error means the right tool was used
    correct_action = 1 if not error else 0

    # correct_outcome: no error AND non-empty output
    correct_outcome = 1 if not error and tool_output not in (None, "") else 0

    return {
        "activated": activated,
        "correct_action": correct_action,
        "correct_outcome": correct_outcome,
    }
