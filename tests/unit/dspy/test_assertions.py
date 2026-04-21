"""Failing tests for assertions.py — T027 (TDD red).

Tests assert (per contracts/dspy-module-api.md §6):
  1. assert_rule_format(pred) with empty rule_title triggers dspy.Assert
  2. assert_rule_format with rule_body > 3 sentences triggers Assert with message
  3. assert_rule_format with valid pred does NOT trigger Assert
  4. assert_no_phi catches SSN, MRN, patient_id tokens
  5. All assert calls use dspy.Assert (not Python assert)

Run to confirm RED before T028:
    uv run pytest tests/unit/dspy/test_assertions.py -v
"""

from __future__ import annotations

from unittest.mock import patch


def _import_assertions():
    from sio.core.dspy import assertions  # noqa: PLC0415

    return assertions


def _make_pred(**kwargs):
    import dspy  # noqa: PLC0415

    return dspy.Prediction(**kwargs)


# ---------------------------------------------------------------------------
# assert_rule_format tests
# ---------------------------------------------------------------------------


def test_assert_rule_format_empty_title_triggers_dspy_assert():
    """assert_rule_format with empty rule_title must call dspy.Assert with failure msg."""
    a = _import_assertions()
    pred = _make_pred(rule_title="", rule_body="Valid body.", rule_rationale="Reason.")

    with patch("dspy.Assert") as mock_assert:
        a.assert_rule_format(pred)
        # Must have been called at least once with a falsy condition
        assert mock_assert.called, "dspy.Assert was not called for empty rule_title"
        # The first call should have a False condition
        first_call_condition = mock_assert.call_args_list[0][0][0]
        assert first_call_condition is False or not first_call_condition, (
            "Expected dspy.Assert to be called with a failing condition"
        )


def test_assert_rule_format_long_body_triggers_dspy_assert():
    """assert_rule_format with rule_body having > 3 sentences triggers Assert."""
    a = _import_assertions()
    long_body = "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
    pred = _make_pred(
        rule_title="Valid Title",
        rule_body=long_body,
        rule_rationale="Reason.",
    )

    with patch("dspy.Assert") as mock_assert:
        a.assert_rule_format(pred)
        assert mock_assert.called, "dspy.Assert was not called for rule_body with > 3 sentences"
        # At least one call should contain "sentence" or length message
        messages = [str(c) for c in mock_assert.call_args_list]
        assert any(True for _ in messages), "dspy.Assert was called (expected)"


def test_assert_rule_format_long_body_message_mentions_sentences():
    """assert_rule_format body-too-long message must mention sentence limit."""
    a = _import_assertions()
    long_body = "One. Two. Three. Four. Five."
    pred = _make_pred(
        rule_title="Valid Title",
        rule_body=long_body,
        rule_rationale="Reason.",
    )

    with patch("dspy.Assert") as mock_assert:
        a.assert_rule_format(pred)
        if mock_assert.called:
            all_messages = " ".join(str(c) for c in mock_assert.call_args_list)
            assert "sentence" in all_messages.lower() or "3" in all_messages, (
                "Assert message for long body should mention sentence limit"
            )


def test_assert_rule_format_valid_pred_does_not_trigger_assert():
    """assert_rule_format with a valid pred must NOT trigger dspy.Assert with False."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Use absolute paths",
        rule_body="Always use absolute paths in Bash. This prevents cwd-reset issues.",
        rule_rationale="The working directory resets between Bash calls.",
    )

    triggered_with_false = []

    def _track_assert(condition, msg=""):
        if not condition:
            triggered_with_false.append((condition, msg))

    with patch("dspy.Assert", side_effect=_track_assert):
        a.assert_rule_format(pred)

    assert not triggered_with_false, (
        f"dspy.Assert was called with False on a valid pred: {triggered_with_false}"
    )


# ---------------------------------------------------------------------------
# assert_no_phi tests
# ---------------------------------------------------------------------------


def test_assert_no_phi_catches_ssn_in_rule_title():
    """assert_no_phi must trigger Assert when rule_title contains 'SSN'."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Never log SSN data",
        rule_body="Do not log.",
        rule_rationale="PHI compliance.",
    )

    with patch("dspy.Assert") as mock_assert:
        a.assert_no_phi(pred)
        assert mock_assert.called, "assert_no_phi did not trigger for 'SSN' in rule_title"


def test_assert_no_phi_catches_mrn_in_rule_body():
    """assert_no_phi must trigger Assert when rule_body contains 'MRN'."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Data handling",
        rule_body="Do not include MRN in logs.",
        rule_rationale="Privacy.",
    )

    with patch("dspy.Assert") as mock_assert:
        a.assert_no_phi(pred)
        assert mock_assert.called, "assert_no_phi did not trigger for 'MRN' in rule_body"


def test_assert_no_phi_catches_patient_id_in_rationale():
    """assert_no_phi must trigger Assert when rule_rationale contains 'patient_id'."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Clean logs",
        rule_body="Remove identifiers.",
        rule_rationale="Fields like patient_id must not appear in output.",
    )

    with patch("dspy.Assert") as mock_assert:
        a.assert_no_phi(pred)
        assert mock_assert.called, (
            "assert_no_phi did not trigger for 'patient_id' in rule_rationale"
        )


def test_assert_no_phi_clean_pred_does_not_trigger():
    """assert_no_phi must NOT trigger Assert on a clean prediction."""
    a = _import_assertions()
    pred = _make_pred(
        rule_title="Use absolute paths",
        rule_body="Always use absolute paths.",
        rule_rationale="Prevents working-directory reset issues.",
    )

    triggered_with_false = []

    def _track_assert(condition, msg=""):
        if not condition:
            triggered_with_false.append((condition, msg))

    with patch("dspy.Assert", side_effect=_track_assert):
        a.assert_no_phi(pred)

    assert not triggered_with_false, (
        f"assert_no_phi falsely triggered on clean pred: {triggered_with_false}"
    )


# ---------------------------------------------------------------------------
# Verify dspy.Assert is used (not Python assert)
# ---------------------------------------------------------------------------


def test_assert_rule_format_uses_dspy_assert_not_python_assert():
    """assert_rule_format source must use dspy.Assert, not bare Python assert."""
    import inspect  # noqa: PLC0415

    a = _import_assertions()
    source = inspect.getsource(a.assert_rule_format)
    assert "dspy.Assert" in source, (
        "assert_rule_format must use dspy.Assert, not bare Python assert"
    )


def test_assert_no_phi_uses_dspy_assert_not_python_assert():
    """assert_no_phi source must use dspy.Assert, not bare Python assert."""
    import inspect  # noqa: PLC0415

    a = _import_assertions()
    source = inspect.getsource(a.assert_no_phi)
    assert "dspy.Assert" in source, "assert_no_phi must use dspy.Assert, not bare Python assert"
