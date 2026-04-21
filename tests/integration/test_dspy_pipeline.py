"""T023: Integration test for the DSPy suggestion generation pipeline.

Tests the full pipeline with a mocked LLM: pattern -> generate_dspy_suggestion -> suggestion dict.
Verifies the suggestion references specific tool names and error text from the input pattern.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from sio.core.config import SIOConfig

VALID_TARGET_SURFACES = frozenset(
    {
        "claude_md_rule",
        "skill_update",
        "hook_config",
        "mcp_config",
        "settings_config",
        "agent_profile",
        "project_config",
    }
)


@pytest.fixture
def tool_failure_pattern():
    """A pattern representing recurring Bash tool failures."""
    return {
        "id": 101,
        "pattern_id": "pat-tool-failure-Bash-timeout-999",
        "description": "Bash tool times out when running long pytest suites",
        "tool_name": "Bash",
        "error_count": 18,
        "error_type": "tool_failure",
        "session_count": 6,
        "first_seen": "2026-02-10T08:00:00Z",
        "last_seen": "2026-02-25T16:30:00Z",
        "rank_score": 0.85,
    }


@pytest.fixture
def tool_failure_dataset(tmp_path):
    """Dataset metadata for the Bash timeout pattern with real file."""
    examples = {
        "examples": [
            {
                "error_text": "TimeoutError: Bash command exceeded 120s limit: pytest tests/ -v --tb=long",
                "tool_name": "Bash",
                "user_message": "Run the full test suite with verbose output.",
                "error_type": "tool_failure",
            },
            {
                "error_text": "TimeoutError: Bash command exceeded 120s limit: pytest tests/integration/ -v",
                "tool_name": "Bash",
                "user_message": "Run the integration tests.",
                "error_type": "tool_failure",
            },
            {
                "error_text": "TimeoutError: Bash command exceeded 120s limit: pytest tests/ -x --timeout=60",
                "tool_name": "Bash",
                "user_message": "Run tests and stop on first failure.",
                "error_type": "tool_failure",
            },
        ]
    }
    dataset_file = tmp_path / "bash_timeout_dataset.json"
    dataset_file.write_text(json.dumps(examples))
    return {
        "id": 15,
        "pattern_id": 101,
        "file_path": str(dataset_file),
        "positive_count": 12,
        "negative_count": 6,
    }


@pytest.fixture
def mock_lm_prediction():
    """A mock DSPy prediction that references the Bash tool and timeout errors.

    Audit Round 2 C-R2.6 migration: the canonical SuggestionGenerator emits
    PatternToRule-shape output fields (rule_title / rule_body / rule_rationale).
    This fixture populates the new names. Legacy attributes
    (prevention_instructions, rationale, target_surface) are ALSO set so the
    fixture is useful for any remaining legacy-path tests — but the production
    flow (`generate_dspy_suggestion`) now reads the new names exclusively.
    """
    pred = MagicMock()
    # New canonical fields (what production reads)
    pred.rule_title = "Use timeout flag for Bash pytest commands"
    pred.rule_body = (
        "When running pytest via the Bash tool, always use `--timeout=30` per-test "
        "and split large test suites into smaller runs. For integration tests, "
        "use `run_in_background` parameter to avoid blocking."
    )
    pred.rule_rationale = (
        "18 TimeoutError failures across 6 sessions show that Bash pytest runs "
        "consistently exceed the 120s tool limit. Splitting and adding per-test "
        "timeouts prevents cascading failures."
    )
    pred.reasoning = (
        "Step 1: All errors are TimeoutError from Bash tool running pytest. "
        "Step 2: The common factor is full test suite runs with verbose output. "
        "Step 3: A claude_md_rule to enforce timeout flags is the best surface."
    )
    # Legacy alias attributes (for any still-legacy paths)
    pred.target_surface = "claude_md_rule"
    pred.prevention_instructions = pred.rule_body
    pred.rationale = pred.rule_rationale
    return pred


@pytest.fixture
def mock_dspy_lm(monkeypatch):
    """Return a DSPy-compatible LM stub via DummyLM.

    Audit Round 2 follow-up: DSPy 3.1.3 `dspy.configure(lm=...)` rejects
    non-BaseLM instances (MagicMock fails). Using DummyLM bypasses the
    type check while remaining a controlled, deterministic stub.
    """
    from dspy.utils.dummies import DummyLM

    lm = DummyLM(answers=[{"rule_title": "mocked", "rule_body": "mocked body", "rule_rationale": "mocked"}])
    return lm


@pytest.fixture
def mock_config():
    return SIOConfig()


class TestDspyPipelineIntegration:
    """T023: Full pipeline integration test with mocked LLM."""

    def test_full_pipeline_generates_suggestion_for_tool_failure(
        self,
        tool_failure_pattern,
        tool_failure_dataset,
        mock_lm_prediction,
        mock_config,
    ):
        """End-to-end: pattern + dataset -> generate_dspy_suggestion -> valid suggestion dict."""
        # Audit Round 2 C-R2.6 migration:
        #   - Patch target is now SuggestionGenerator (in suggestions.dspy_generator),
        #     NOT the deleted SuggestionModule in core.dspy.modules.
        #   - create_lm returns DummyLM (BaseLM subclass) so dspy.configure(lm=..)
        #     in DSPy 3.1.3 accepts it. MagicMock was rejected as non-BaseLM.
        with (
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as mock_load,
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
        ):
            from dspy.utils.dummies import DummyLM
            mock_create_lm.return_value = DummyLM(answers=[{}])

            mock_module = MagicMock()
            mock_module.forward.return_value = mock_lm_prediction
            mock_load.return_value = mock_module

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            result = generate_dspy_suggestion(
                pattern=tool_failure_pattern,
                dataset=tool_failure_dataset,
                config=mock_config,
            )

            assert isinstance(result, dict)
            required_keys = {
                "target_surface",
                "rule_title",
                "prevention_instructions",
                "rationale",
                "reasoning_trace",
                "confidence",
                "proposed_change",
            }
            assert required_keys.issubset(result.keys())

    def test_suggestion_references_tool_name(
        self,
        tool_failure_pattern,
        tool_failure_dataset,
        mock_lm_prediction,
        mock_config,
    ):
        """The suggestion content must reference the specific tool name from the pattern."""
        # Audit Round 2 C-R2.6 migration:
        #   - Patch target is now SuggestionGenerator (in suggestions.dspy_generator),
        #     NOT the deleted SuggestionModule in core.dspy.modules.
        #   - create_lm returns DummyLM (BaseLM subclass) so dspy.configure(lm=..)
        #     in DSPy 3.1.3 accepts it. MagicMock was rejected as non-BaseLM.
        with (
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as mock_load,
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
        ):
            from dspy.utils.dummies import DummyLM
            mock_create_lm.return_value = DummyLM(answers=[{}])

            mock_module = MagicMock()
            mock_module.forward.return_value = mock_lm_prediction
            mock_load.return_value = mock_module

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            result = generate_dspy_suggestion(
                pattern=tool_failure_pattern,
                dataset=tool_failure_dataset,
                config=mock_config,
            )

            combined_text = " ".join(
                [
                    result.get("rule_title", ""),
                    result.get("prevention_instructions", ""),
                    result.get("rationale", ""),
                    result.get("proposed_change", ""),
                    result.get("description", ""),
                ]
            )
            assert "Bash" in combined_text or "bash" in combined_text.lower(), (
                "Suggestion should reference the 'Bash' tool from the pattern"
            )

    def test_suggestion_references_error_text(
        self,
        tool_failure_pattern,
        tool_failure_dataset,
        mock_lm_prediction,
        mock_config,
    ):
        """The suggestion content must reference the specific error from the pattern."""
        # Audit Round 2 C-R2.6 migration:
        #   - Patch target is now SuggestionGenerator (in suggestions.dspy_generator),
        #     NOT the deleted SuggestionModule in core.dspy.modules.
        #   - create_lm returns DummyLM (BaseLM subclass) so dspy.configure(lm=..)
        #     in DSPy 3.1.3 accepts it. MagicMock was rejected as non-BaseLM.
        with (
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as mock_load,
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
        ):
            from dspy.utils.dummies import DummyLM
            mock_create_lm.return_value = DummyLM(answers=[{}])

            mock_module = MagicMock()
            mock_module.forward.return_value = mock_lm_prediction
            mock_load.return_value = mock_module

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            result = generate_dspy_suggestion(
                pattern=tool_failure_pattern,
                dataset=tool_failure_dataset,
                config=mock_config,
            )

            combined_text = " ".join(
                [
                    result.get("rule_title", ""),
                    result.get("prevention_instructions", ""),
                    result.get("rationale", ""),
                    result.get("proposed_change", ""),
                ]
            ).lower()

            assert "timeout" in combined_text or "pytest" in combined_text, (
                "Suggestion should reference 'timeout' or 'pytest' from the error examples"
            )

    def test_target_surface_is_valid_for_pipeline(
        self,
        tool_failure_pattern,
        tool_failure_dataset,
        mock_lm_prediction,
        mock_config,
    ):
        """Pipeline output target_surface must be one of the 7 valid surfaces."""
        # Audit Round 2 C-R2.6 migration:
        #   - Patch target is now SuggestionGenerator (in suggestions.dspy_generator),
        #     NOT the deleted SuggestionModule in core.dspy.modules.
        #   - create_lm returns DummyLM (BaseLM subclass) so dspy.configure(lm=..)
        #     in DSPy 3.1.3 accepts it. MagicMock was rejected as non-BaseLM.
        with (
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as mock_load,
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
        ):
            from dspy.utils.dummies import DummyLM
            mock_create_lm.return_value = DummyLM(answers=[{}])

            mock_module = MagicMock()
            mock_module.forward.return_value = mock_lm_prediction
            mock_load.return_value = mock_module

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            result = generate_dspy_suggestion(
                pattern=tool_failure_pattern,
                dataset=tool_failure_dataset,
                config=mock_config,
            )

            assert result["target_surface"] in VALID_TARGET_SURFACES

    def test_pipeline_with_sanitization_does_not_leak_secrets(
        self,
        tool_failure_pattern,
        mock_lm_prediction,
        mock_config,
        tmp_path,
    ):
        """Even when error examples contain secrets, the pipeline must not pass them to the LLM."""
        examples_with_secrets = {
            "examples": [
                {
                    "error_text": "AuthError: invalid key sk-proj-abc123xyz7890abcdef for endpoint",
                    "tool_name": "Bash",
                    "user_message": "Deploy with token ghp_ABCDEF1234567890abcdef",
                    "error_type": "tool_failure",
                },
            ]
        }
        dataset_file = tmp_path / "secrets_dataset.json"
        dataset_file.write_text(json.dumps(examples_with_secrets))
        dataset = {
            "id": 99,
            "pattern_id": 101,
            "file_path": str(dataset_file),
            "positive_count": 1,
            "negative_count": 0,
        }

        # Audit Round 2 C-R2.6 migration:
        #   - Patch target is now SuggestionGenerator (in suggestions.dspy_generator),
        #     NOT the deleted SuggestionModule in core.dspy.modules.
        #   - create_lm returns DummyLM (BaseLM subclass) so dspy.configure(lm=..)
        #     in DSPy 3.1.3 accepts it. MagicMock was rejected as non-BaseLM.
        with (
            patch("sio.suggestions.dspy_generator._load_optimized_or_default") as mock_load,
            patch("sio.core.dspy.lm_factory.create_lm") as mock_create_lm,
        ):
            from dspy.utils.dummies import DummyLM
            mock_create_lm.return_value = DummyLM(answers=[{}])

            mock_module = MagicMock()
            mock_module.forward.return_value = mock_lm_prediction
            mock_load.return_value = mock_module

            from sio.suggestions.dspy_generator import generate_dspy_suggestion

            generate_dspy_suggestion(
                pattern=tool_failure_pattern,
                dataset=dataset,
                config=mock_config,
            )

            # Verify the module was called, and check what was passed to forward()
            mock_module.forward.assert_called_once()
            call_kwargs = mock_module.forward.call_args
            if call_kwargs.kwargs:
                passed_examples = call_kwargs.kwargs.get("error_examples", "")
            else:
                passed_examples = call_kwargs.args[0] if call_kwargs.args else ""

            # Secrets must not appear in what was passed to the LLM
            assert "sk-proj-abc123xyz" not in passed_examples, "API key leaked to LLM input"
