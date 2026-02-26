"""T007: TDD tests for DSPy Signature definitions.

Module under test: src/sio/core/dspy/signatures.py

These tests are intentionally RED until the Signature classes are defined.
"""

from __future__ import annotations


class TestSuggestionGeneratorSignature:
    """SuggestionGenerator must define the expected input and output fields."""

    def test_suggestion_generator_has_input_fields(self):
        from sio.core.dspy.signatures import SuggestionGenerator

        # DSPy Signatures expose fields via .input_fields (dict of InputField)
        input_names = set(SuggestionGenerator.input_fields.keys())
        expected = {"error_examples", "error_type", "pattern_summary"}
        assert expected.issubset(input_names), (
            f"Missing input fields: {expected - input_names}"
        )

    def test_suggestion_generator_has_output_fields(self):
        from sio.core.dspy.signatures import SuggestionGenerator

        output_names = set(SuggestionGenerator.output_fields.keys())
        expected = {"target_surface", "rule_title", "prevention_instructions", "rationale"}
        assert expected.issubset(output_names), (
            f"Missing output fields: {expected - output_names}"
        )


class TestGroundTruthCandidateSignature:
    """GroundTruthCandidate must define quality_assessment output field."""

    def test_ground_truth_candidate_has_quality_assessment(self):
        from sio.core.dspy.signatures import GroundTruthCandidate

        output_names = set(GroundTruthCandidate.output_fields.keys())
        assert "quality_assessment" in output_names, (
            f"Missing 'quality_assessment' in output fields: {output_names}"
        )


class TestSignatureInheritance:
    """Both signatures must be proper dspy.Signature subclasses."""

    def test_signatures_are_dspy_signatures(self):
        import dspy

        from sio.core.dspy.signatures import (
            GroundTruthCandidate,
            SuggestionGenerator,
        )

        assert issubclass(SuggestionGenerator, dspy.Signature), (
            "SuggestionGenerator must be a dspy.Signature subclass"
        )
        assert issubclass(GroundTruthCandidate, dspy.Signature), (
            "GroundTruthCandidate must be a dspy.Signature subclass"
        )
