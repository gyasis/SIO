"""DSPy Module wrappers — ChainOfThought reasoning over Signatures."""

import dspy

from sio.core.dspy.signatures import GroundTruthCandidate, SuggestionGenerator


class SuggestionModule(dspy.Module):
    """Generates improvement suggestions using ChainOfThought reasoning."""

    def __init__(self):
        super().__init__()
        self.generate = dspy.ChainOfThought(SuggestionGenerator)

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


class GroundTruthModule(dspy.Module):
    """Generates ground truth candidates using ChainOfThought reasoning."""

    def __init__(self):
        super().__init__()
        self.generate = dspy.ChainOfThought(GroundTruthCandidate)

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
