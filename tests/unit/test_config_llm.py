"""T004: TDD tests for LLM config fields in SIOConfig.

These tests are intentionally RED until the [llm] and [llm.sub] TOML
sections are wired into SIOConfig and load_config().
"""

from __future__ import annotations

from sio.core.config import SIOConfig, load_config


class TestDefaultLLMConfig:
    """Default SIOConfig must have all LLM fields set to None / safe defaults."""

    def test_default_config_has_no_llm(self):
        cfg = SIOConfig()
        assert cfg.llm_model is None

    def test_default_llm_api_key_env_is_none(self):
        cfg = SIOConfig()
        assert cfg.llm_api_key_env is None

    def test_default_llm_api_base_env_is_none(self):
        cfg = SIOConfig()
        assert cfg.llm_api_base_env is None

    def test_default_llm_temperature(self):
        cfg = SIOConfig()
        assert cfg.llm_temperature == 0.7

    def test_default_llm_max_tokens(self):
        cfg = SIOConfig()
        assert cfg.llm_max_tokens == 2000

    def test_default_llm_sub_model_is_none(self):
        cfg = SIOConfig()
        assert cfg.llm_sub_model is None


class TestLoadLLMSection:
    """TOML [llm] section must populate the LLM fields on SIOConfig."""

    def test_load_llm_section(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[llm]\n"
            'model = "openai/gpt-4o"\n'
            'api_key_env = "OPENAI_API_KEY"\n'
            'api_base_env = "OPENAI_API_BASE"\n'
            "temperature = 0.3\n"
            "max_tokens = 4000\n"
        )
        cfg = load_config(str(config_file))
        assert cfg.llm_model == "openai/gpt-4o"
        assert cfg.llm_api_key_env == "OPENAI_API_KEY"
        assert cfg.llm_api_base_env == "OPENAI_API_BASE"
        assert cfg.llm_temperature == 0.3
        assert cfg.llm_max_tokens == 4000

    def test_load_llm_sub_section(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[llm]\n"
            'model = "openai/gpt-4o"\n'
            "\n"
            "[llm.sub]\n"
            'model = "openai/gpt-4o-mini"\n'
        )
        cfg = load_config(str(config_file))
        assert cfg.llm_sub_model == "openai/gpt-4o-mini"

    def test_missing_llm_section_uses_defaults(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("retention_days = 60\n")
        cfg = load_config(str(config_file))
        assert cfg.llm_model is None
        assert cfg.llm_api_key_env is None
        assert cfg.llm_api_base_env is None
        assert cfg.llm_temperature == 0.7
        assert cfg.llm_max_tokens == 2000
        assert cfg.llm_sub_model is None

    def test_llm_temperature_override(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[llm]\n"
            'model = "anthropic/claude-3-haiku"\n'
            "temperature = 0.0\n"
        )
        cfg = load_config(str(config_file))
        assert cfg.llm_temperature == 0.0

    def test_llm_section_does_not_clobber_existing_fields(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "retention_days = 45\n"
            "\n"
            "[llm]\n"
            'model = "openai/gpt-4o"\n'
        )
        cfg = load_config(str(config_file))
        assert cfg.retention_days == 45
        assert cfg.llm_model == "openai/gpt-4o"
