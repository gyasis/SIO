"""DSPy Module wrappers — ChainOfThought reasoning over Signatures.

dspy is imported lazily so the rest of SIO can load without it installed.
"""

from __future__ import annotations

try:
    import dspy as _dspy

    _Module = _dspy.Module
except ImportError:  # pragma: no cover
    _dspy = None  # type: ignore[assignment]
    _Module = object  # type: ignore[assignment,misc]


# Audit Round 2 C-R2.6 (Hunter #2, DSPy): `SuggestionModule` (4-input old
# SuggestionGenerator signature) has been DELETED as part of the
# consolidation to the canonical `sio.suggestions.dspy_generator.
# SuggestionGenerator` (3-input PatternToRule signature per PRD
# contracts/dspy-module-api.md §3).
#
# The legacy class is preserved here only as an ImportError-raising shim
# that points callers to the replacement. This is NOT a no-op stub — it
# makes the incompatibility loud, which is what the user's "tests validate
# reality" principle requires. Any code that still imports SuggestionModule
# will get a clear signal that it must migrate, not a silent wrong-class
# substitution.


class SuggestionModule:  # pragma: no cover - migration shim
    """Deprecated — removed in Round 2 C-R2.6 consolidation.

    Use ``sio.suggestions.dspy_generator.SuggestionGenerator`` instead.
    The new class uses the PatternToRule signature (pattern_description,
    example_errors, project_context → rule_title, rule_body, rule_rationale)
    per PRD contracts/dspy-module-api.md §3.
    """

    def __init__(self, *args, **kwargs):  # noqa: ARG002
        raise ImportError(
            "SuggestionModule was removed in Round 2 C-R2.6 consolidation. "
            "Use sio.suggestions.dspy_generator.SuggestionGenerator instead. "
            "New signature: forward(pattern_description, example_errors, project_context) "
            "→ rule_title, rule_body, rule_rationale."
        )


class GroundTruthModule(_Module):
    """Generates ground truth candidates using ChainOfThought reasoning."""

    def __init__(self):
        if _dspy is None:
            raise ImportError("dspy is required for GroundTruthModule — pip install dspy")
        super().__init__()
        from sio.core.dspy.signatures import GroundTruthCandidate

        self.generate = _dspy.ChainOfThought(GroundTruthCandidate)

    def forward(
        self,
        error_examples: str,
        error_type: str,
        pattern_summary: str,
        tool_input_context: str = "{}",
    ):
        return self.generate(
            error_examples=error_examples,
            error_type=error_type,
            pattern_summary=pattern_summary,
            tool_input_context=tool_input_context,
        )
