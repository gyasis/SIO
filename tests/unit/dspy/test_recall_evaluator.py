"""T060 [US9] Tests for RecallEvaluator dspy.Module class.

Per contracts/dspy-module-api.md §3 and §7:
- RecallEvaluator must be a dspy.Module subclass in src/sio/training/recall_trainer.py
- forward(gold_rule, candidate_rule) returns Prediction with score:float + reasoning:str
- Uses the RuleRecallScore signature

These tests are EXPECTED RED until T067 (Wave 9) rewrites recall_trainer.py.

Run to confirm RED:
    uv run pytest tests/unit/dspy/test_recall_evaluator.py -v
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_recall_evaluator():
    """Import RecallEvaluator from the canonical location."""
    from sio.training.recall_trainer import RecallEvaluator  # noqa: PLC0415

    return RecallEvaluator


# ---------------------------------------------------------------------------
# T060-1: Class structure tests (RED until T067 Wave 9)
# ---------------------------------------------------------------------------


def test_recall_evaluator_is_dspy_module():
    """RecallEvaluator must be a dspy.Module subclass."""
    import dspy  # noqa: PLC0415

    RecallEvaluator = _get_recall_evaluator()
    assert issubclass(RecallEvaluator, dspy.Module), (
        "RecallEvaluator must subclass dspy.Module (contracts/dspy-module-api.md §7)"
    )


def test_recall_evaluator_forward_signature():
    """RecallEvaluator.forward() must accept (gold_rule, candidate_rule)."""
    import inspect  # noqa: PLC0415

    RecallEvaluator = _get_recall_evaluator()
    sig = inspect.signature(RecallEvaluator.forward)
    params = list(sig.parameters.keys())
    assert "gold_rule" in params, "forward() must accept 'gold_rule'"
    assert "candidate_rule" in params, "forward() must accept 'candidate_rule'"


def test_recall_evaluator_forward_returns_score_and_reasoning():
    """forward() must return a Prediction with float score in [0,1] and reasoning str."""
    from unittest.mock import patch  # noqa: PLC0415

    import dspy  # noqa: PLC0415

    RecallEvaluator = _get_recall_evaluator()

    mock_pred = dspy.Prediction(
        score=0.85,
        reasoning="The candidate captures the preventive intent of the gold rule.",
    )

    evaluator = RecallEvaluator()

    with patch.object(evaluator, "evaluate", return_value=mock_pred):
        result = evaluator.forward(
            gold_rule="Always use absolute paths in Bash.",
            candidate_rule="Use absolute file paths when issuing shell commands.",
        )

    assert hasattr(result, "score"), "Prediction must have 'score' field"
    assert hasattr(result, "reasoning"), "Prediction must have 'reasoning' field"
    assert isinstance(float(result.score), float), "score must be convertible to float"
    assert 0.0 <= float(result.score) <= 1.0, f"score must be in [0, 1], got {result.score}"


def test_recall_evaluator_uses_rule_recall_score_signature():
    """RecallEvaluator must use the RuleRecallScore signature."""
    import inspect  # noqa: PLC0415

    RecallEvaluator = _get_recall_evaluator()
    source = inspect.getsource(RecallEvaluator)
    assert "RuleRecallScore" in source, (
        "RecallEvaluator source must reference RuleRecallScore signature "
        "(contracts/dspy-module-api.md §7)"
    )
