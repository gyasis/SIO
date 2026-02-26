"""T024: Contract tests for DSPy Signature field definitions.

These tests inspect the actual Signature classes to verify their input and output
field names match the contract expected by the dspy_generator module. If someone
renames a field in the Signature, these tests catch the mismatch immediately.
"""

from __future__ import annotations


class TestSuggestionGeneratorContract:
    """Verify SuggestionGenerator Signature fields match the expected contract."""

    def test_input_field_names_match_contract(self):
        """SuggestionGenerator must have exactly these input fields."""
        from sio.core.dspy.signatures import SuggestionGenerator

        actual_inputs = set(SuggestionGenerator.input_fields.keys())
        expected_inputs = {"error_examples", "error_type", "pattern_summary", "tool_input_context"}

        assert actual_inputs == expected_inputs, (
            f"SuggestionGenerator input fields mismatch.\n"
            f"  Expected: {expected_inputs}\n"
            f"  Actual:   {actual_inputs}\n"
            f"  Missing:  {expected_inputs - actual_inputs}\n"
            f"  Extra:    {actual_inputs - expected_inputs}"
        )

    def test_output_field_names_match_contract(self):
        """SuggestionGenerator must have exactly these output fields."""
        from sio.core.dspy.signatures import SuggestionGenerator

        actual_outputs = set(SuggestionGenerator.output_fields.keys())
        expected_outputs = {
            "target_surface",
            "rule_title",
            "prevention_instructions",
            "rationale",
        }

        assert actual_outputs == expected_outputs, (
            f"SuggestionGenerator output fields mismatch.\n"
            f"  Expected: {expected_outputs}\n"
            f"  Actual:   {actual_outputs}\n"
            f"  Missing:  {expected_outputs - actual_outputs}\n"
            f"  Extra:    {actual_outputs - expected_outputs}"
        )

    def test_input_fields_are_dspy_input_fields(self):
        """Each input field must be a dspy.InputField instance."""

        from sio.core.dspy.signatures import SuggestionGenerator

        for name, field in SuggestionGenerator.input_fields.items():
            # DSPy stores field metadata; verify they are InputField-typed
            assert hasattr(field, "json_schema_extra") or hasattr(field, "description"), (
                f"Input field {name!r} does not look like a proper dspy field"
            )

    def test_output_fields_are_dspy_output_fields(self):
        """Each output field must be a dspy.OutputField instance."""

        from sio.core.dspy.signatures import SuggestionGenerator

        for name, field in SuggestionGenerator.output_fields.items():
            assert hasattr(field, "json_schema_extra") or hasattr(field, "description"), (
                f"Output field {name!r} does not look like a proper dspy field"
            )


class TestGroundTruthCandidateContract:
    """Verify GroundTruthCandidate has the same base fields plus quality_assessment."""

    def test_input_fields_match_suggestion_generator(self):
        """GroundTruthCandidate must have the same input fields as SuggestionGenerator."""
        from sio.core.dspy.signatures import (
            GroundTruthCandidate,
            SuggestionGenerator,
        )

        sg_inputs = set(SuggestionGenerator.input_fields.keys())
        gt_inputs = set(GroundTruthCandidate.input_fields.keys())

        assert sg_inputs == gt_inputs, (
            f"GroundTruthCandidate inputs should match SuggestionGenerator.\n"
            f"  SuggestionGenerator: {sg_inputs}\n"
            f"  GroundTruthCandidate: {gt_inputs}"
        )

    def test_output_fields_are_superset_of_suggestion_generator(self):
        """GroundTruthCandidate outputs must include all SuggestionGenerator outputs + quality_assessment."""
        from sio.core.dspy.signatures import (
            GroundTruthCandidate,
            SuggestionGenerator,
        )

        sg_outputs = set(SuggestionGenerator.output_fields.keys())
        gt_outputs = set(GroundTruthCandidate.output_fields.keys())

        # GroundTruthCandidate must have everything SuggestionGenerator has
        assert sg_outputs.issubset(gt_outputs), (
            f"GroundTruthCandidate missing SuggestionGenerator output fields: "
            f"{sg_outputs - gt_outputs}"
        )

        # Plus it must have quality_assessment
        assert "quality_assessment" in gt_outputs, (
            "GroundTruthCandidate must have 'quality_assessment' output field"
        )


class TestSuggestionModuleContract:
    """Verify SuggestionModule.forward() signature matches the Signature inputs."""

    def test_forward_accepts_signature_input_names(self):
        """SuggestionModule.forward() parameters must match SuggestionGenerator input fields."""
        import inspect

        from sio.core.dspy.modules import SuggestionModule
        from sio.core.dspy.signatures import SuggestionGenerator

        expected_params = set(SuggestionGenerator.input_fields.keys())

        sig = inspect.signature(SuggestionModule.forward)
        # Exclude 'self' from parameter names
        actual_params = {
            name for name in sig.parameters if name != "self"
        }

        assert expected_params == actual_params, (
            f"SuggestionModule.forward() parameters mismatch with Signature inputs.\n"
            f"  Signature inputs: {expected_params}\n"
            f"  forward() params: {actual_params}\n"
            f"  Missing in forward: {expected_params - actual_params}\n"
            f"  Extra in forward: {actual_params - expected_params}"
        )

    def test_module_wraps_chain_of_thought(self):
        """SuggestionModule.generate must be a ChainOfThought instance."""
        import dspy

        from sio.core.dspy.modules import SuggestionModule

        mod = SuggestionModule()
        assert isinstance(mod.generate, dspy.ChainOfThought), (
            f"SuggestionModule.generate should be ChainOfThought, "
            f"got {type(mod.generate).__name__}"
        )


class TestTargetSurfaceContract:
    """Verify the target_surface field description lists all 7 valid surfaces."""

    EXPECTED_SURFACES = [
        "claude_md_rule",
        "skill_update",
        "hook_config",
        "mcp_config",
        "settings_config",
        "agent_profile",
        "project_config",
    ]

    def test_target_surface_desc_lists_all_surfaces(self):
        """The target_surface output field description must mention all 7 valid surfaces."""
        from sio.core.dspy.signatures import SuggestionGenerator

        target_field = SuggestionGenerator.output_fields["target_surface"]
        # DSPy stores the desc in json_schema_extra or description
        desc = ""
        if hasattr(target_field, "json_schema_extra") and target_field.json_schema_extra:
            desc = str(target_field.json_schema_extra.get("desc", ""))
        if not desc and hasattr(target_field, "description"):
            desc = str(target_field.description or "")

        for surface in self.EXPECTED_SURFACES:
            assert surface in desc, (
                f"target_surface description is missing '{surface}'.\n"
                f"  Full desc: {desc!r}"
            )
