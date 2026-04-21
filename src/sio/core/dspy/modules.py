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


class SuggestionModule(_Module):
    """Generates improvement suggestions using ChainOfThought reasoning."""

    def __init__(self):
        if _dspy is None:
            raise ImportError("dspy is required for SuggestionModule — pip install dspy")
        super().__init__()
        from sio.core.dspy.signatures import SuggestionGenerator

        self.generate = _dspy.ChainOfThought(SuggestionGenerator)

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
