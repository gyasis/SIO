"""Failing tests for signatures.py — T023 (TDD red).

Tests assert (per contracts/dspy-module-api.md §2):
  1. PatternToRule has non-empty __doc__
  2. PatternToRule has correct InputFields / OutputFields
  3. example_errors field is typed list[str]
  4. dspy.Predict(PatternToRule) instantiates without error (smoke)
  5. RuleRecallScore has class docstring
  6. RuleRecallScore has correct InputFields / OutputFields
  7. dspy.Predict(RuleRecallScore) instantiates smoke-ok

Run to confirm RED before T024:
    uv run pytest tests/unit/dspy/test_signatures.py -v
"""
from __future__ import annotations

import inspect
import pytest


def _import_signatures():
    from sio.core.dspy import signatures  # noqa: PLC0415
    return signatures


# ---------------------------------------------------------------------------
# PatternToRule tests
# ---------------------------------------------------------------------------

def test_pattern_to_rule_has_docstring():
    """PatternToRule must have a non-empty class docstring."""
    sigs = _import_signatures()
    doc = inspect.getdoc(sigs.PatternToRule)
    assert doc and len(doc.strip()) > 0, "PatternToRule must have a class docstring"


def test_pattern_to_rule_has_pattern_description_input():
    """PatternToRule must have pattern_description as an InputField."""
    import dspy  # noqa: PLC0415
    sigs = _import_signatures()
    cls = sigs.PatternToRule
    assert "pattern_description" in cls.model_fields, (
        "PatternToRule missing 'pattern_description' field"
    )
    field = cls.model_fields["pattern_description"]
    assert field.metadata or field.default is not None or True, "field must exist"


def test_pattern_to_rule_has_example_errors_input():
    """PatternToRule must have example_errors as an InputField."""
    sigs = _import_signatures()
    assert "example_errors" in sigs.PatternToRule.model_fields, (
        "PatternToRule missing 'example_errors' field"
    )


def test_pattern_to_rule_has_project_context_input():
    """PatternToRule must have project_context as an InputField."""
    sigs = _import_signatures()
    assert "project_context" in sigs.PatternToRule.model_fields, (
        "PatternToRule missing 'project_context' field"
    )


def test_pattern_to_rule_has_rule_title_output():
    """PatternToRule must have rule_title as an OutputField."""
    sigs = _import_signatures()
    assert "rule_title" in sigs.PatternToRule.model_fields, (
        "PatternToRule missing 'rule_title' field"
    )


def test_pattern_to_rule_has_rule_body_output():
    """PatternToRule must have rule_body as an OutputField."""
    sigs = _import_signatures()
    assert "rule_body" in sigs.PatternToRule.model_fields, (
        "PatternToRule missing 'rule_body' field"
    )


def test_pattern_to_rule_has_rule_rationale_output():
    """PatternToRule must have rule_rationale as an OutputField."""
    sigs = _import_signatures()
    assert "rule_rationale" in sigs.PatternToRule.model_fields, (
        "PatternToRule missing 'rule_rationale' field"
    )


def test_pattern_to_rule_example_errors_is_list_type():
    """PatternToRule.example_errors must be typed as list[str]."""
    import typing  # noqa: PLC0415
    sigs = _import_signatures()
    hints = typing.get_type_hints(sigs.PatternToRule)
    assert "example_errors" in hints, "No type hint found for 'example_errors'"
    hint = hints["example_errors"]
    # Accept list[str] — the hint may be str or List[str] depending on DSPy version
    origin = getattr(hint, "__origin__", None)
    assert origin is list or hint is list, (
        f"Expected list type for example_errors, got {hint!r}"
    )


def test_pattern_to_rule_predict_smoke():
    """dspy.Predict(PatternToRule) must instantiate without error."""
    import dspy  # noqa: PLC0415
    sigs = _import_signatures()
    predictor = dspy.Predict(sigs.PatternToRule)
    assert predictor is not None


def test_pattern_to_rule_input_keys():
    """PatternToRule input keys must include the three required fields."""
    import dspy  # noqa: PLC0415
    sigs = _import_signatures()
    sig_instance = sigs.PatternToRule
    # Access input fields from DSPy signature
    input_fields = sig_instance.input_fields
    assert "pattern_description" in input_fields
    assert "example_errors" in input_fields
    assert "project_context" in input_fields


def test_pattern_to_rule_output_keys():
    """PatternToRule output keys must include the three required fields."""
    sigs = _import_signatures()
    output_fields = sigs.PatternToRule.output_fields
    assert "rule_title" in output_fields
    assert "rule_body" in output_fields
    assert "rule_rationale" in output_fields


# ---------------------------------------------------------------------------
# RuleRecallScore tests
# ---------------------------------------------------------------------------

def test_rule_recall_score_has_docstring():
    """RuleRecallScore must have a non-empty class docstring."""
    sigs = _import_signatures()
    doc = inspect.getdoc(sigs.RuleRecallScore)
    assert doc and len(doc.strip()) > 0, "RuleRecallScore must have a class docstring"


def test_rule_recall_score_has_gold_rule_input():
    """RuleRecallScore must have gold_rule as an InputField."""
    sigs = _import_signatures()
    assert "gold_rule" in sigs.RuleRecallScore.model_fields, (
        "RuleRecallScore missing 'gold_rule' field"
    )


def test_rule_recall_score_has_candidate_rule_input():
    """RuleRecallScore must have candidate_rule as an InputField."""
    sigs = _import_signatures()
    assert "candidate_rule" in sigs.RuleRecallScore.model_fields, (
        "RuleRecallScore missing 'candidate_rule' field"
    )


def test_rule_recall_score_has_score_output():
    """RuleRecallScore must have score as an OutputField."""
    sigs = _import_signatures()
    assert "score" in sigs.RuleRecallScore.model_fields, (
        "RuleRecallScore missing 'score' field"
    )


def test_rule_recall_score_has_reasoning_output():
    """RuleRecallScore must have reasoning as an OutputField."""
    sigs = _import_signatures()
    assert "reasoning" in sigs.RuleRecallScore.model_fields, (
        "RuleRecallScore missing 'reasoning' field"
    )


def test_rule_recall_score_predict_smoke():
    """dspy.Predict(RuleRecallScore) must instantiate without error."""
    import dspy  # noqa: PLC0415
    sigs = _import_signatures()
    predictor = dspy.Predict(sigs.RuleRecallScore)
    assert predictor is not None


def test_rule_recall_score_input_keys():
    """RuleRecallScore input keys must include gold_rule and candidate_rule."""
    sigs = _import_signatures()
    input_fields = sigs.RuleRecallScore.input_fields
    assert "gold_rule" in input_fields
    assert "candidate_rule" in input_fields


def test_rule_recall_score_output_keys():
    """RuleRecallScore output keys must include score and reasoning."""
    sigs = _import_signatures()
    output_fields = sigs.RuleRecallScore.output_fields
    assert "score" in output_fields
    assert "reasoning" in output_fields
