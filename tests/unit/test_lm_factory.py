"""T005: TDD tests for LM factory — src/sio/core/dspy/lm_factory.py.

These tests are intentionally RED until lm_factory.py is implemented.
All dspy imports are mocked so the tests run without dspy installed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sio.core.config import SIOConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> SIOConfig:
    """Build an SIOConfig with LLM fields. Relies on T004 fields existing."""
    defaults = {
        "llm_model": None,
        "llm_api_key_env": None,
        "llm_api_base_env": None,
        "llm_temperature": 0.7,
        "llm_max_tokens": 2000,
        "llm_sub_model": None,
    }
    defaults.update(overrides)
    return SIOConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateLMFromConfig:
    """create_lm() should return a dspy.LM when config has a model set."""

    @patch.dict("os.environ", {}, clear=True)
    @patch("dspy.LM")
    def test_create_lm_from_config(self, mock_lm_cls):
        from sio.core.dspy.lm_factory import create_lm

        mock_lm_cls.return_value = MagicMock()
        cfg = _make_config(llm_model="openai/gpt-4o", llm_api_key_env="OPENAI_API_KEY")
        result = create_lm(cfg)
        assert result is not None
        mock_lm_cls.assert_called_once()


class TestEnvDetection:
    """create_lm() should detect provider from environment variables."""

    @patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "az-key-123"}, clear=True)
    @patch("dspy.LM")
    def test_create_lm_azure_env_detection(self, mock_lm_cls):
        from sio.core.dspy.lm_factory import create_lm

        mock_lm_cls.return_value = MagicMock()
        cfg = _make_config()  # no explicit model
        result = create_lm(cfg)
        assert result is not None

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "ant-key-123"}, clear=True)
    @patch("dspy.LM")
    def test_create_lm_anthropic_env_detection(self, mock_lm_cls):
        from sio.core.dspy.lm_factory import create_lm

        mock_lm_cls.return_value = MagicMock()
        cfg = _make_config()
        result = create_lm(cfg)
        assert result is not None

    @patch.dict("os.environ", {"OPENAI_API_KEY": "oai-key-123"}, clear=True)
    @patch("dspy.LM")
    def test_create_lm_openai_env_detection(self, mock_lm_cls):
        from sio.core.dspy.lm_factory import create_lm

        mock_lm_cls.return_value = MagicMock()
        cfg = _make_config()
        result = create_lm(cfg)
        assert result is not None

    @patch.dict("os.environ", {}, clear=True)
    def test_create_lm_returns_none(self):
        from sio.core.dspy.lm_factory import create_lm

        cfg = _make_config()  # no model, no env vars
        result = create_lm(cfg)
        assert result is None


class TestEnvPriority:
    """When multiple API keys are set, Azure > Anthropic > OpenAI."""

    @patch.dict(
        "os.environ",
        {
            "AZURE_OPENAI_API_KEY": "az-key",
            "ANTHROPIC_API_KEY": "ant-key",
            "OPENAI_API_KEY": "oai-key",
        },
        clear=True,
    )
    @patch("dspy.LM")
    def test_env_var_priority_order(self, mock_lm_cls):
        from sio.core.dspy.lm_factory import create_lm

        mock_lm_cls.return_value = MagicMock()
        cfg = _make_config()
        result = create_lm(cfg)
        assert result is not None
        # The first positional arg or 'model' kwarg should reference azure
        call_args = mock_lm_cls.call_args
        all_args_str = str(call_args)
        assert "azure" in all_args_str.lower(), (
            f"Expected Azure provider to be selected, got: {call_args}"
        )


class TestCreateSubLM:
    """create_lm() or a dedicated create_sub_lm() should handle sub-model."""

    @patch.dict("os.environ", {}, clear=True)
    @patch("dspy.LM")
    def test_create_sub_lm(self, mock_lm_cls):
        from sio.core.dspy.lm_factory import create_lm

        mock_lm_cls.return_value = MagicMock()
        cfg = _make_config(
            llm_model="openai/gpt-4o",
            llm_sub_model="openai/gpt-4o-mini",
            llm_api_key_env="OPENAI_API_KEY",
        )
        # The factory should be able to create the sub LM
        # Implementation may use create_sub_lm(cfg) or create_lm(cfg, sub=True)
        # We test that the sub model string is accessible and usable
        assert cfg.llm_sub_model == "openai/gpt-4o-mini"
        # Factory must expose sub-model creation
        from sio.core.dspy.lm_factory import create_sub_lm

        result = create_sub_lm(cfg)
        assert result is not None
