"""T008: TDD tests for DSPy Module wrappers.

Module under test: src/sio/core/dspy/modules.py

These tests are intentionally RED until the Module classes are defined.
"""

from __future__ import annotations

import inspect


# Audit Round 2 C-R2.6 (Hunter #2, DSPy) consolidation: `TestSuggestionModule`
# class (4 tests) has been REMOVED because the `SuggestionModule` class it
# covered was deleted in the same commit. The canonical replacement is
# `sio.suggestions.dspy_generator.SuggestionGenerator` (3-input PatternToRule
# signature). Coverage for the new class lives at
# `tests/unit/dspy/test_suggestion_generator_module.py` — those tests verify
# forward() returns the expected Prediction, dspy.Assert-replacement triggers
# on malformed output, and instrumentation is attached. No assertions from
# the removed tests were silently dropped; each was replaced by a test
# against the new canonical surface.


class TestGroundTruthModule:
    """GroundTruthModule must be a valid dspy.Module with generate."""

    def test_ground_truth_module_is_dspy_module(self):
        import dspy

        from sio.core.dspy.modules import GroundTruthModule

        mod = GroundTruthModule()
        assert isinstance(mod, dspy.Module), "GroundTruthModule must be an instance of dspy.Module"

    def test_ground_truth_module_has_generate(self):
        from sio.core.dspy.modules import GroundTruthModule

        mod = GroundTruthModule()
        assert hasattr(mod, "generate"), "GroundTruthModule must have a 'generate' attribute"
