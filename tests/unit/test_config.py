"""T078b Unit tests for config loader."""

from __future__ import annotations

import pytest

from sio.core.config import SIOConfig, load_config


class TestDefaults:
    """Default config when no file exists."""

    def test_returns_sio_config(self):
        cfg = load_config("/nonexistent/config.toml")
        assert isinstance(cfg, SIOConfig)

    def test_default_embedding_backend(self):
        cfg = load_config("/nonexistent/config.toml")
        assert cfg.embedding_backend == "fastembed"

    def test_default_retention_days(self):
        cfg = load_config("/nonexistent/config.toml")
        assert cfg.retention_days == 90

    def test_default_thresholds(self):
        cfg = load_config("/nonexistent/config.toml")
        assert cfg.min_examples == 10
        assert cfg.min_failures == 5
        assert cfg.min_sessions == 3
        assert cfg.pattern_threshold == 3

    def test_default_optimizer(self):
        cfg = load_config("/nonexistent/config.toml")
        assert cfg.optimizer == "gepa"

    def test_default_drift_threshold(self):
        cfg = load_config("/nonexistent/config.toml")
        assert cfg.drift_threshold == 0.40


class TestTomlParsing:
    """Config loaded from TOML file."""

    def test_all_fields(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            'embedding_backend = "api"\n'
            'embedding_model = "custom-model"\n'
            "retention_days = 30\n"
            "min_examples = 20\n"
            'optimizer = "miprov2"\n'
            "drift_threshold = 0.50\n"
        )
        cfg = load_config(str(config_file))
        assert cfg.embedding_backend == "api"
        assert cfg.embedding_model == "custom-model"
        assert cfg.retention_days == 30
        assert cfg.min_examples == 20
        assert cfg.optimizer == "miprov2"
        assert cfg.drift_threshold == 0.50

    def test_partial_config_uses_defaults(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("retention_days = 60\n")
        cfg = load_config(str(config_file))
        assert cfg.retention_days == 60
        assert cfg.embedding_backend == "fastembed"  # default
        assert cfg.min_examples == 10  # default

    def test_invalid_toml_raises_value_error(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("invalid = [[[bad toml")
        with pytest.raises(ValueError, match="Invalid config"):
            load_config(str(config_file))
