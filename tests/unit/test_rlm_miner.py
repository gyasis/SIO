"""T046 [US4] Unit tests for RLM corpus miner."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sio.core.dspy.rlm_miner import (
    DenoNotFoundError,
    MiningResult,
    mine_failure_context,
)


class TestMiningResult:
    """MiningResult dataclass contract."""

    def test_has_failure_analysis(self):
        r = MiningResult(failure_analysis="test", trajectory=[], llm_calls=0)
        assert r.failure_analysis == "test"

    def test_trajectory_is_list(self):
        r = MiningResult(failure_analysis="", trajectory=[{"step": 1}])
        assert isinstance(r.trajectory, list)
        assert r.trajectory[0]["step"] == 1

    def test_default_trajectory_empty(self):
        r = MiningResult(failure_analysis="x")
        assert r.trajectory == []
        assert r.llm_calls == 0


class TestDenoCheck:
    """Deno must be installed for RLM mining."""

    @patch("shutil.which", return_value=None)
    def test_deno_missing_raises_clear_error(self, mock_which):
        with pytest.raises(DenoNotFoundError, match="Deno is required"):
            mine_failure_context("/corpus", {"actual_action": "Read"})

    @patch("shutil.which", return_value="/usr/bin/deno")
    def test_deno_present_succeeds(self, mock_which):
        result = mine_failure_context(
            "/corpus",
            {"actual_action": "Read", "user_message": "read foo.py"},
        )
        assert isinstance(result, MiningResult)


class TestMineFailureContext:
    """mine_failure_context produces analysis from failure records."""

    @patch("shutil.which", return_value="/usr/bin/deno")
    def test_returns_mining_result(self, _):
        result = mine_failure_context(
            "/corpus",
            {"actual_action": "Read", "user_message": "read foo.py"},
        )
        assert isinstance(result, MiningResult)
        assert len(result.failure_analysis) > 0

    @patch("shutil.which", return_value="/usr/bin/deno")
    def test_trajectory_has_steps(self, _):
        result = mine_failure_context(
            "/corpus",
            {"actual_action": "Read", "user_message": "read foo.py"},
        )
        assert len(result.trajectory) >= 1
        assert "step" in result.trajectory[0]
        assert "code" in result.trajectory[0]
        assert "output" in result.trajectory[0]

    @patch("shutil.which", return_value="/usr/bin/deno")
    def test_analysis_references_skill(self, _):
        result = mine_failure_context(
            "/corpus",
            {"actual_action": "Bash", "user_message": "run tests"},
        )
        assert "Bash" in result.failure_analysis

    @patch("shutil.which", return_value="/usr/bin/deno")
    def test_llm_calls_tracked(self, _):
        result = mine_failure_context(
            "/corpus",
            {"actual_action": "Read", "user_message": "read foo.py"},
        )
        assert result.llm_calls >= 1


class TestRealRLMMining:
    """Tests for the real DSPy ChainOfThought mining path."""

    @patch("shutil.which", return_value="/usr/bin/deno")
    @patch("sio.core.dspy.rlm_miner._corpus_search")
    @patch("sio.core.dspy.rlm_miner._dspy_available", True)
    @patch("sio.core.dspy.rlm_miner.dspy")
    def test_dspy_path_invoked(self, mock_dspy, mock_corpus_search, _):
        """When DSPy is available, ChainOfThought is used."""
        # Set up mock prediction
        mock_prediction = type("Prediction", (), {
            "failure_analysis": "Root cause: missing file validation in Read skill.",
        })()
        mock_module = mock_dspy.ChainOfThought.return_value
        mock_module.return_value = mock_prediction

        # Corpus search returns context
        mock_corpus_search.return_value = "User asked to read foo.py but file was missing."

        result = mine_failure_context(
            "/corpus",
            {
                "actual_action": "Read",
                "user_message": "read foo.py",
                "correct_outcome": "error",
            },
        )
        assert isinstance(result, MiningResult)
        assert "missing file" in result.failure_analysis.lower()
        mock_dspy.ChainOfThought.assert_called_once()
        assert result.llm_calls == 1

    @patch("shutil.which", return_value="/usr/bin/deno")
    @patch("sio.core.dspy.rlm_miner._corpus_search")
    @patch("sio.core.dspy.rlm_miner._dspy_available", True)
    @patch("sio.core.dspy.rlm_miner.dspy")
    def test_dspy_failure_falls_back_to_heuristic(
        self, mock_dspy, mock_corpus_search, _,
    ):
        """When DSPy raises an exception, fallback to heuristic analysis."""
        mock_module = mock_dspy.ChainOfThought.return_value
        mock_module.side_effect = RuntimeError("LLM unavailable")

        result = mine_failure_context(
            "/corpus",
            {
                "actual_action": "Bash",
                "user_message": "run tests",
                "correct_outcome": "fail",
            },
        )
        assert isinstance(result, MiningResult)
        # Heuristic path should mention the skill name
        assert "Bash" in result.failure_analysis
        assert result.llm_calls >= 1

    @patch("shutil.which", return_value="/usr/bin/deno")
    @patch("sio.core.dspy.rlm_miner._dspy_available", False)
    def test_no_dspy_uses_heuristic(self, _):
        """When DSPy is not installed, heuristic path is used directly."""
        result = mine_failure_context(
            "/corpus",
            {
                "actual_action": "Read",
                "user_message": "read foo.py",
                "correct_outcome": "error",
            },
        )
        assert isinstance(result, MiningResult)
        assert "Read" in result.failure_analysis
        assert len(result.trajectory) >= 1

    @patch("shutil.which", return_value="/usr/bin/deno")
    @patch("sio.core.dspy.rlm_miner._corpus_search")
    @patch("sio.core.dspy.rlm_miner._dspy_available", True)
    @patch("sio.core.dspy.rlm_miner.dspy")
    def test_corpus_context_passed_to_dspy(
        self, mock_dspy, mock_corpus_search, _,
    ):
        """Corpus search results are passed as context to the DSPy module."""
        mock_prediction = type("Prediction", (), {
            "failure_analysis": "Analysis based on corpus context.",
        })()
        mock_module = mock_dspy.ChainOfThought.return_value
        mock_module.return_value = mock_prediction

        corpus_text = "Session contained Read failures for missing paths."
        mock_corpus_search.return_value = corpus_text

        mine_failure_context(
            "/corpus",
            {
                "actual_action": "Read",
                "user_message": "read foo.py",
                "correct_outcome": "error",
            },
        )

        # Verify the module was called with corpus context
        call_kwargs = mock_module.call_args
        assert call_kwargs is not None
        # The corpus_context kwarg should contain our text
        if call_kwargs.kwargs:
            assert corpus_text in str(call_kwargs)
        else:
            assert corpus_text in str(call_kwargs)
