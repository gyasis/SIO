"""T008: TDD tests for DSPy Module wrappers.

Module under test: src/sio/core/dspy/modules.py

These tests are intentionally RED until the Module classes are defined.
"""

from __future__ import annotations

import inspect


class TestSuggestionModule:
    """SuggestionModule must be a valid dspy.Module with generate and forward."""

    def test_suggestion_module_is_dspy_module(self):
        import dspy

        from sio.core.dspy.modules import SuggestionModule

        mod = SuggestionModule()
        assert isinstance(mod, dspy.Module), (
            "SuggestionModule must be an instance of dspy.Module"
        )

    def test_suggestion_module_has_generate(self):
        from sio.core.dspy.modules import SuggestionModule

        mod = SuggestionModule()
        assert hasattr(mod, "generate"), (
            "SuggestionModule must have a 'generate' attribute"
        )

    def test_suggestion_module_forward_signature(self):
        from sio.core.dspy.modules import SuggestionModule

        mod = SuggestionModule()
        sig = inspect.signature(mod.forward)
        param_names = set(sig.parameters.keys())
        expected = {"error_examples", "error_type", "pattern_summary"}
        assert expected.issubset(param_names), (
            f"forward() missing parameters: {expected - param_names}. "
            f"Has: {param_names}"
        )


class TestGroundTruthModule:
    """GroundTruthModule must be a valid dspy.Module with generate."""

    def test_ground_truth_module_is_dspy_module(self):
        import dspy

        from sio.core.dspy.modules import GroundTruthModule

        mod = GroundTruthModule()
        assert isinstance(mod, dspy.Module), (
            "GroundTruthModule must be an instance of dspy.Module"
        )

    def test_ground_truth_module_has_generate(self):
        from sio.core.dspy.modules import GroundTruthModule

        mod = GroundTruthModule()
        assert hasattr(mod, "generate"), (
            "GroundTruthModule must have a 'generate' attribute"
        )
