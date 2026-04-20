"""T059 [US9] Tests for SuggestionGenerator dspy.Module class.

Per contracts/dspy-module-api.md §3, SuggestionGenerator must:
- Be a dspy.Module subclass in src/sio/suggestions/dspy_generator.py
- Have a forward() accepting (pattern_description, example_errors, project_context)
- Return a dspy.Prediction with rule_title, rule_body, rule_rationale
- Fire dspy.Assert on malformed output (empty rule_title triggers assertion)

These tests are EXPECTED RED until T066 (Wave 9) refactors dspy_generator.py
to wrap PatternToRule in a proper Module form that matches this API.

Run to confirm RED:
    uv run pytest tests/unit/dspy/test_suggestion_generator_module.py -v
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_suggestion_generator():
    """Import SuggestionGenerator from the canonical location."""
    # Per contracts/dspy-module-api.md §3 — must live in dspy_generator.py
    from sio.suggestions.dspy_generator import SuggestionGenerator  # noqa: PLC0415
    return SuggestionGenerator


def _mock_lm_fixture(rule_title: str = "Use absolute paths", rule_body: str = "Always use absolute paths.", rule_rationale: str = "Prevents cwd issues."):
    """Return a mock LM that produces deterministic PatternToRule output."""
    import dspy  # noqa: PLC0415

    class _MockLM(dspy.LM):
        def __init__(self):
            # Skip parent __init__ to avoid API key requirement
            self.model = "mock/model"
            self.cache = False
            self.temperature = 0.0
            self.max_tokens = 256
            self._history = []

        def __call__(self, prompt=None, messages=None, **kwargs):
            # Return a minimal response that DSPy can parse
            return [
                {
                    "role": "assistant",
                    "content": (
                        f"rule_title: {rule_title}\n"
                        f"rule_body: {rule_body}\n"
                        f"rule_rationale: {rule_rationale}\n"
                    ),
                }
            ]

    return _MockLM()


# ---------------------------------------------------------------------------
# T059-1: Class structure tests (RED until T066 Wave 9)
# ---------------------------------------------------------------------------

def test_suggestion_generator_is_dspy_module():
    """SuggestionGenerator must be a dspy.Module subclass (FR-035)."""
    import dspy  # noqa: PLC0415
    SuggestionGenerator = _get_suggestion_generator()
    assert issubclass(SuggestionGenerator, dspy.Module), (
        "SuggestionGenerator must subclass dspy.Module"
    )


def test_suggestion_generator_forward_signature():
    """SuggestionGenerator.forward() must accept (pattern_description, example_errors, project_context)."""
    import inspect  # noqa: PLC0415
    SuggestionGenerator = _get_suggestion_generator()
    sig = inspect.signature(SuggestionGenerator.forward)
    params = list(sig.parameters.keys())
    assert "pattern_description" in params, (
        "forward() must accept 'pattern_description'"
    )
    assert "example_errors" in params, (
        "forward() must accept 'example_errors'"
    )
    assert "project_context" in params, (
        "forward() must accept 'project_context'"
    )


def test_suggestion_generator_has_generate_predictor():
    """SuggestionGenerator must have a 'generate' ChainOfThought predictor attribute."""
    SuggestionGenerator = _get_suggestion_generator()
    gen = SuggestionGenerator()
    assert hasattr(gen, "generate"), (
        "SuggestionGenerator must have a 'generate' predictor attribute"
    )


def test_suggestion_generator_forward_returns_prediction_fields():
    """SuggestionGenerator.forward() must return a Prediction with rule_title, rule_body, rule_rationale.

    Uses a patched LM to avoid real API calls.
    """
    from unittest.mock import patch  # noqa: PLC0415

    import dspy  # noqa: PLC0415

    SuggestionGenerator = _get_suggestion_generator()

    # Mock the forward pass by patching the generate predictor
    mock_pred = dspy.Prediction(
        rule_title="Use absolute paths",
        rule_body="Always use absolute paths in Bash calls.",
        rule_rationale="Prevents working-directory reset issues.",
    )

    gen = SuggestionGenerator()

    with patch.object(gen, "generate", return_value=mock_pred):
        result = gen.forward(
            pattern_description="Bash cwd reset errors",
            example_errors=["cwd reset", "path not found"],
            project_context="SIO project",
        )

    assert hasattr(result, "rule_title"), "Prediction must have rule_title"
    assert hasattr(result, "rule_body"), "Prediction must have rule_body"
    assert hasattr(result, "rule_rationale"), "Prediction must have rule_rationale"


def test_suggestion_generator_assert_fires_on_empty_rule_title():
    """dspy.Assert must fire when forward() returns an empty rule_title.

    Uses a mock that returns empty rule_title to trigger the assertion guard.
    """
    from unittest.mock import patch  # noqa: PLC0415

    import dspy  # noqa: PLC0415

    SuggestionGenerator = _get_suggestion_generator()

    # Empty rule_title should trigger assert_rule_format
    mock_pred = dspy.Prediction(
        rule_title="",  # EMPTY — should trigger assertion
        rule_body="Some body.",
        rule_rationale="Some rationale.",
    )

    gen = SuggestionGenerator()

    triggered = []

    def _track_assert(condition, msg="", **kwargs):
        if not condition:
            triggered.append((condition, msg))

    with patch.object(gen, "generate", return_value=mock_pred):
        with patch("dspy.Assert", side_effect=_track_assert):
            try:
                gen.forward(
                    pattern_description="Test pattern",
                    example_errors=["error 1"],
                    project_context="test",
                )
            except Exception:
                pass  # Assert may raise — that's fine

    assert triggered, (
        "dspy.Assert should have been called with a failing condition for empty rule_title. "
        "Ensure SuggestionGenerator.forward() calls assert_rule_format(pred)."
    )
