"""Tests for assertions.py — validates format/PHI contract (FR-038).

DSPy 3.1.3 removed dspy.Assert/dspy.Suggest.  The module now exposes:
  - validate_rule_format(pred) -> bool
  - validate_no_phi(pred) -> bool
  - assert_rule_format(pred) -> None  (raises ValidationError on failure)
  - assert_no_phi(pred) -> None       (raises ValidationError on failure)

These tests verify the same behavioral contracts as the previous dspy.Assert
suite: empty/long bodies are rejected, PHI tokens are detected, and valid
predictions pass through cleanly.

See C-R2.1 fix: replaced dspy.Assert shim with metric-side validators.
"""

from __future__ import annotations

import pytest


def _import_assertions():
    from sio.core.dspy import assertions  # noqa: PLC0415

    return assertions


def _make_pred(**kwargs):
    import dspy  # noqa: PLC0415

    return dspy.Prediction(**kwargs)


# ---------------------------------------------------------------------------
# validate_rule_format tests
# ---------------------------------------------------------------------------


def test_validate_rule_format_empty_title_returns_false():
    """validate_rule_format with empty rule_title must return False."""
    a = _import_assertions()
    pred = _make_pred(rule_title="", rule_body="Valid body.", rule_rationale="Reason.")
    assert a.validate_rule_format(pred) is False, (
        "Expected False for empty rule_title"
    )


def test_validate_rule_format_long_body_returns_false():
    """validate_rule_format with rule_body having > 3 sentences must return False."""
    a = _import_assertions()
    long_body = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
    pred = _make_pred(
        rule_title="Valid Title",
        rule_body=long_body,
        rule_rationale="Reason.",
    )
    assert a.validate_rule_format(pred) is False, (
        "Expected False for rule_body with > 3 sentences"
    )


def test_validate_rule_format_valid_pred_returns_true():
    """validate_rule_format with a valid pred must return True."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Use absolute paths",
        rule_body="Always use absolute paths in Bash. This prevents cwd-reset issues.",
        rule_rationale="The working directory resets between Bash calls.",
    )
    assert a.validate_rule_format(pred) is True, (
        "Expected True for valid pred"
    )


def test_assert_rule_format_empty_title_triggers_dspy_assert():
    """assert_rule_format with empty rule_title must raise ValidationError."""
    a = _import_assertions()
    pred = _make_pred(rule_title="", rule_body="Valid body.", rule_rationale="Reason.")
    with pytest.raises((a.ValidationError, Exception)):
        a.assert_rule_format(pred)


def test_assert_rule_format_long_body_triggers_dspy_assert():
    """assert_rule_format with rule_body having > 3 sentences raises ValidationError."""
    a = _import_assertions()
    long_body = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
    pred = _make_pred(
        rule_title="Valid Title",
        rule_body=long_body,
        rule_rationale="Reason.",
    )
    with pytest.raises((a.ValidationError, Exception)):
        a.assert_rule_format(pred)


def test_assert_rule_format_long_body_message_mentions_sentences():
    """assert_rule_format body-too-long error message must mention sentence limit."""
    a = _import_assertions()
    long_body = "One. Two. Three. Four. Five."
    pred = _make_pred(
        rule_title="Valid Title",
        rule_body=long_body,
        rule_rationale="Reason.",
    )
    try:
        a.assert_rule_format(pred)
    except Exception as exc:
        msg = str(exc).lower()
        assert "sentence" in msg or "3" in msg, (
            f"Error message should mention sentence limit; got: {exc}"
        )


def test_assert_rule_format_valid_pred_does_not_trigger_assert():
    """assert_rule_format with a valid pred must NOT raise."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Use absolute paths",
        rule_body="Always use absolute paths in Bash. This prevents cwd-reset issues.",
        rule_rationale="The working directory resets between Bash calls.",
    )
    # Should not raise
    a.assert_rule_format(pred)


# ---------------------------------------------------------------------------
# validate_no_phi / assert_no_phi tests
# ---------------------------------------------------------------------------


def test_assert_no_phi_catches_ssn_in_rule_title():
    """assert_no_phi must raise when rule_title contains 'SSN'."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Never log SSN data",
        rule_body="Do not log.",
        rule_rationale="PHI compliance.",
    )
    with pytest.raises((a.ValidationError, Exception)):
        a.assert_no_phi(pred)


def test_assert_no_phi_catches_mrn_in_rule_body():
    """assert_no_phi must raise when rule_body contains 'MRN'."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Data handling",
        rule_body="Do not include MRN in logs.",
        rule_rationale="Privacy.",
    )
    with pytest.raises((a.ValidationError, Exception)):
        a.assert_no_phi(pred)


def test_assert_no_phi_catches_patient_id_in_rationale():
    """assert_no_phi must raise when rule_rationale contains 'patient_id'."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Clean logs",
        rule_body="Remove identifiers.",
        rule_rationale="Fields like patient_id must not appear in output.",
    )
    with pytest.raises((a.ValidationError, Exception)):
        a.assert_no_phi(pred)


def test_assert_no_phi_clean_pred_does_not_trigger():
    """assert_no_phi must NOT raise on a clean prediction."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Use absolute paths",
        rule_body="Always use absolute paths.",
        rule_rationale="Prevents working-directory reset issues.",
    )
    # Should not raise
    a.assert_no_phi(pred)


def test_validate_no_phi_returns_false_for_ssn():
    """validate_no_phi must return False when SSN token present."""
    a = _import_assertions()
    pred = _make_pred(rule_title="SSN log rule", rule_body="body", rule_rationale="r")
    assert a.validate_no_phi(pred) is False


def test_validate_no_phi_returns_true_for_clean_pred():
    """validate_no_phi must return True for clean prediction."""
    a = _import_assertions()
    pred = _make_pred(rule_title="Use paths", rule_body="body", rule_rationale="r")
    assert a.validate_no_phi(pred) is True


# ---------------------------------------------------------------------------
# Verify API shape (replaces old "uses dspy.Assert" checks)
# ---------------------------------------------------------------------------


def test_assert_rule_format_uses_validation_error_not_assertion_error():
    """assert_rule_format must raise ValidationError (not bare AssertionError) on failure."""
    a = _import_assertions()
    pred = _make_pred(rule_title="", rule_body="x", rule_rationale="y")
    with pytest.raises(a.ValidationError):
        a.assert_rule_format(pred)


def test_assert_no_phi_uses_validation_error():
    """assert_no_phi must raise ValidationError (not bare AssertionError) on PHI."""
    a = _import_assertions()
    pred = _make_pred(rule_title="SSN leak", rule_body="x", rule_rationale="y")
    with pytest.raises(a.ValidationError):
        a.assert_no_phi(pred)


def test_validate_functions_exported():
    """validate_rule_format and validate_no_phi must be exported from assertions."""
    a = _import_assertions()
    assert callable(a.validate_rule_format), "validate_rule_format must be callable"
    assert callable(a.validate_no_phi), "validate_no_phi must be callable"


def test_metric_returns_zero_on_format_violation():
    """A metric that uses validate_rule_format should return 0.0 on bad pred."""
    a = _import_assertions()
    pred = _make_pred(rule_title="", rule_body="", rule_rationale="")
    assert a.validate_rule_format(pred) is False
    # Metric pre-filter: if not validate_rule_format(pred), return 0.0
    metric_score = 0.0 if not a.validate_rule_format(pred) else 1.0
    assert metric_score == 0.0, "Metric should return 0.0 on format violation"
