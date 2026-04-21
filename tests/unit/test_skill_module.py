"""Tests for DSPy SkillGeneratorModule and SkillGeneratorSignature.

Module under test: src/sio/core/dspy/skill_module.py
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dspy


class TestSkillGeneratorSignature:
    """SkillGeneratorSignature must have the correct input/output fields."""

    def test_signature_has_input_fields(self):
        from sio.core.dspy.signatures import SkillGeneratorSignature

        input_fields = SkillGeneratorSignature.input_fields
        expected = {
            "pattern_description",
            "error_examples",
            "positive_examples",
            "flow_sequence",
        }
        assert expected == set(input_fields.keys()), (
            f"Input fields mismatch. Expected: {expected}, Got: {set(input_fields.keys())}"
        )

    def test_signature_has_output_fields(self):
        from sio.core.dspy.signatures import SkillGeneratorSignature

        output_fields = SkillGeneratorSignature.output_fields
        expected = {
            "trigger_conditions",
            "ordered_steps",
            "guardrails",
        }
        assert expected == set(output_fields.keys()), (
            f"Output fields mismatch. Expected: {expected}, Got: {set(output_fields.keys())}"
        )

    def test_signature_is_dspy_signature(self):
        from sio.core.dspy.signatures import SkillGeneratorSignature

        assert issubclass(SkillGeneratorSignature, dspy.Signature)


class TestSkillGeneratorModule:
    """SkillGeneratorModule must be a valid dspy.Module with generate and forward."""

    def test_module_instantiates_without_error(self):
        from sio.core.dspy.skill_module import SkillGeneratorModule

        mod = SkillGeneratorModule()
        assert mod is not None

    def test_module_is_dspy_module(self):
        from sio.core.dspy.skill_module import SkillGeneratorModule

        mod = SkillGeneratorModule()
        assert isinstance(mod, dspy.Module), (
            "SkillGeneratorModule must be an instance of dspy.Module"
        )

    def test_module_has_generate(self):
        from sio.core.dspy.skill_module import SkillGeneratorModule

        mod = SkillGeneratorModule()
        assert hasattr(mod, "generate"), "SkillGeneratorModule must have a 'generate' attribute"

    def test_module_forward_signature(self):
        from sio.core.dspy.skill_module import SkillGeneratorModule

        mod = SkillGeneratorModule()
        sig = inspect.signature(mod.forward)
        param_names = set(sig.parameters.keys())
        expected = {
            "pattern_description",
            "error_examples",
            "positive_examples",
            "flow_sequence",
        }
        assert expected.issubset(param_names), (
            f"forward() missing parameters: {expected - param_names}. Has: {param_names}"
        )

    def test_module_has_generate_skill_method(self):
        from sio.core.dspy.skill_module import SkillGeneratorModule

        mod = SkillGeneratorModule()
        assert hasattr(mod, "generate_skill")
        assert callable(mod.generate_skill)

    def test_generate_skill_with_mock_predict(self):
        """Mock dspy.Predict to test generate_skill without LLM calls."""
        from sio.core.dspy.skill_module import SkillGeneratorModule

        mod = SkillGeneratorModule()

        mock_result = SimpleNamespace(
            trigger_conditions="When editing Python files with imports",
            ordered_steps=("1. Read the file\n2. Check imports with Grep\n3. Edit the file"),
            guardrails=("- NEVER skip import verification\n- ALWAYS run ruff check after editing"),
        )
        mod.generate = MagicMock(return_value=mock_result)

        skill_md = mod.generate_skill(
            pattern="Import ordering failures",
            errors=[{"error": "F401 unused import"}],
            positives=[{"success": "imports sorted correctly"}],
            flow="Read,Grep,Edit,Bash",
        )

        assert "# Skill: Import ordering failures" in skill_md
        assert "## Trigger Conditions" in skill_md
        assert "When editing Python files with imports" in skill_md
        assert "## Steps" in skill_md
        assert "Read the file" in skill_md
        assert "## Guardrails" in skill_md
        assert "NEVER skip import verification" in skill_md
        assert "ALWAYS run ruff check" in skill_md

    def test_generate_skill_accepts_json_string_errors(self):
        """generate_skill should accept errors as a JSON string."""
        from sio.core.dspy.skill_module import SkillGeneratorModule

        mod = SkillGeneratorModule()

        mock_result = SimpleNamespace(
            trigger_conditions="When running tests",
            ordered_steps="1. Run pytest",
            guardrails="- ALWAYS check exit code",
        )
        mod.generate = MagicMock(return_value=mock_result)

        skill_md = mod.generate_skill(
            pattern="Test failures",
            errors='[{"error": "AssertionError"}]',
            flow="Bash",
        )

        assert "# Skill: Test failures" in skill_md


class TestTemplateFallback:
    """When no LLM is available, template-based output should be returned."""

    def test_template_skill_output(self):
        from sio.core.dspy.skill_module import _template_skill

        result = _template_skill(
            pattern="Edit without reading",
            errors="[]",
            positives="[]",
            flow="Read,Grep,Edit",
        )

        assert "# Skill: Edit without reading" in result
        assert "## Trigger Conditions" in result
        assert "## Steps" in result
        assert "1. Run `Read`" in result
        assert "2. Run `Grep`" in result
        assert "3. Run `Edit`" in result
        assert "## Guardrails" in result
        assert "NEVER skip" in result
        assert "ALWAYS verify" in result

    def test_generate_skill_safe_fallback(self):
        """generate_skill_safe falls back to template when LLM unavailable."""
        from sio.core.dspy.skill_module import generate_skill_safe

        # Patch forward to raise an exception simulating no LLM
        with patch(
            "sio.core.dspy.skill_module.SkillGeneratorModule.forward",
            side_effect=RuntimeError("No LLM configured"),
        ):
            result = generate_skill_safe(
                pattern="Missing LLM test",
                errors=[{"error": "test"}],
                flow="Read,Edit",
            )

        assert "# Skill: Missing LLM test" in result
        assert "## Steps" in result
        assert "1. Run `Read`" in result
        assert "2. Run `Edit`" in result

    def test_generate_skill_safe_with_no_dspy(self):
        """generate_skill_safe falls back when dspy import fails."""
        from sio.core.dspy.skill_module import generate_skill_safe

        with patch(
            "sio.core.dspy.skill_module._load_optimized_or_default",
            side_effect=ImportError("no dspy"),
        ):
            result = generate_skill_safe(
                pattern="No DSPy",
                errors="[]",
                flow="Bash",
            )

        assert "# Skill: No DSPy" in result
        assert "1. Run `Bash`" in result


class TestLoadOptimizedOrDefault:
    """_load_optimized_or_default should load from store or return default."""

    def test_returns_default_without_conn(self):
        from sio.core.dspy.skill_module import (
            SkillGeneratorModule,
            _load_optimized_or_default,
        )

        mod = _load_optimized_or_default(conn=None)
        assert isinstance(mod, SkillGeneratorModule)

    def test_returns_default_when_no_active_module(self):
        from sio.core.dspy.skill_module import (
            SkillGeneratorModule,
            _load_optimized_or_default,
        )

        mock_conn = MagicMock()
        with patch(
            "sio.core.dspy.module_store.get_active_module",
            return_value=None,
        ):
            mod = _load_optimized_or_default(conn=mock_conn)

        assert isinstance(mod, SkillGeneratorModule)
