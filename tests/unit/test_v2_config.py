"""T-V2CFG Unit tests for v2 config keys on SIOConfig.

These tests are intentionally RED until v2 fields are added to
src/sio/core/config.py and load_config() is updated to read them.

v2 fields under test
--------------------
    similarity_threshold     float = 0.80
    min_pattern_occurrences  int   = 3
    min_dataset_examples     int   = 5
    daily_enabled            bool  = True
    weekly_enabled           bool  = True
    stale_days               int   = 30
"""

from __future__ import annotations

import pytest

from sio.core.config import SIOConfig, load_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(tmp_path, content: str):
    """Write *content* to a temp config.toml and return its str path."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(content, encoding="utf-8")
    return str(cfg)


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestV2Defaults:
    """SIOConfig() should expose all v2 fields with correct default values."""

    def test_v2_defaults(self):
        """All v2 fields exist and carry the documented default values."""
        cfg = SIOConfig()

        assert cfg.similarity_threshold == 0.80
        assert cfg.min_pattern_occurrences == 3
        assert cfg.min_dataset_examples == 5
        assert cfg.daily_enabled is True
        assert cfg.weekly_enabled is True
        assert cfg.stale_days == 30

    def test_v1_defaults_unchanged(self):
        """Adding v2 fields must not disturb any v1 default value."""
        cfg = SIOConfig()

        # Embedding
        assert cfg.embedding_backend == "fastembed"
        assert cfg.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
        assert cfg.embedding_api_url is None
        assert cfg.embedding_api_key is None

        # Retention & thresholds
        assert cfg.retention_days == 90
        assert cfg.min_examples == 10
        assert cfg.min_failures == 5
        assert cfg.min_sessions == 3
        assert cfg.pattern_threshold == 3

        # Optimizer & drift
        assert cfg.optimizer == "gepa"
        assert cfg.drift_threshold == pytest.approx(0.40)
        assert cfg.collision_threshold == pytest.approx(0.85)


class TestLoadV2ConfigFromToml:
    """load_config() reads v2 keys correctly when present in the TOML file."""

    def test_load_v2_config_from_toml(self, tmp_path):
        """All six v2 keys are parsed and override the defaults."""
        path = _write_toml(
            tmp_path,
            "similarity_threshold = 0.95\n"
            "min_pattern_occurrences = 7\n"
            "min_dataset_examples = 12\n"
            "daily_enabled = false\n"
            "weekly_enabled = false\n"
            "stale_days = 60\n",
        )
        cfg = load_config(path)

        assert cfg.similarity_threshold == pytest.approx(0.95)
        assert cfg.min_pattern_occurrences == 7
        assert cfg.min_dataset_examples == 12
        assert cfg.daily_enabled is False
        assert cfg.weekly_enabled is False
        assert cfg.stale_days == 60

    def test_load_mixed_config(self, tmp_path):
        """A TOML file with both v1 and v2 keys loads all values correctly."""
        path = _write_toml(
            tmp_path,
            # v1 keys
            'embedding_backend = "api"\n'
            "retention_days = 45\n"
            "drift_threshold = 0.55\n"
            # v2 keys
            "similarity_threshold = 0.75\n"
            "min_pattern_occurrences = 5\n"
            "min_dataset_examples = 8\n"
            "daily_enabled = true\n"
            "weekly_enabled = false\n"
            "stale_days = 14\n",
        )
        cfg = load_config(path)

        # v1 overrides
        assert cfg.embedding_backend == "api"
        assert cfg.retention_days == 45
        assert cfg.drift_threshold == pytest.approx(0.55)

        # v2 overrides
        assert cfg.similarity_threshold == pytest.approx(0.75)
        assert cfg.min_pattern_occurrences == 5
        assert cfg.min_dataset_examples == 8
        assert cfg.daily_enabled is True
        assert cfg.weekly_enabled is False
        assert cfg.stale_days == 14

    def test_v2_defaults_when_missing_from_toml(self, tmp_path):
        """A TOML file with only v1 keys leaves all v2 fields at their defaults."""
        path = _write_toml(
            tmp_path,
            # Only v1 content — no v2 keys at all
            'embedding_backend = "fastembed"\nretention_days = 30\nmin_examples = 15\n',
        )
        cfg = load_config(path)

        # v2 fields must fall back to defaults
        assert cfg.similarity_threshold == pytest.approx(0.80)
        assert cfg.min_pattern_occurrences == 3
        assert cfg.min_dataset_examples == 5
        assert cfg.daily_enabled is True
        assert cfg.weekly_enabled is True
        assert cfg.stale_days == 30

        # Verify v1 values were still loaded from the file
        assert cfg.embedding_backend == "fastembed"
        assert cfg.retention_days == 30
        assert cfg.min_examples == 15


class TestSimilarityThresholdRange:
    """similarity_threshold accepts valid float values across the [0, 1] range."""

    @pytest.mark.parametrize(
        "value",
        [0.0, 0.50, 0.75, 0.80, 0.90, 0.95, 1.0],
    )
    def test_similarity_threshold_range(self, tmp_path, value):
        """Each float value in [0.0, 1.0] round-trips through TOML correctly."""
        path = _write_toml(tmp_path, f"similarity_threshold = {value}\n")
        cfg = load_config(path)

        assert cfg.similarity_threshold == pytest.approx(value)

    def test_similarity_threshold_dataclass_direct(self):
        """SIOConfig accepts arbitrary float values set at construction time."""
        for value in (0.0, 0.5, 0.8, 0.99, 1.0):
            cfg = SIOConfig(similarity_threshold=value)
            assert cfg.similarity_threshold == pytest.approx(value)
