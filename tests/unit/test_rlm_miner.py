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
