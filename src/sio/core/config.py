"""Configuration loader — reads ~/.sio/config.toml with sensible defaults."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


@dataclass
class SIOConfig:
    """SIO configuration."""

    embedding_backend: str = "fastembed"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_api_url: str | None = None
    embedding_api_key: str | None = None
    retention_days: int = 90
    min_examples: int = 10
    min_failures: int = 5
    min_sessions: int = 3
    pattern_threshold: int = 3
    optimizer: str = "gepa"
    drift_threshold: float = 0.40
    collision_threshold: float = 0.85
    # v2 fields
    similarity_threshold: float = 0.80
    min_pattern_occurrences: int = 3
    min_dataset_examples: int = 5
    daily_enabled: bool = True
    weekly_enabled: bool = True
    stale_days: int = 30
    # [llm] section
    llm_model: str | None = None
    llm_api_key_env: str | None = None
    llm_api_base_env: str | None = None
    llm_temperature: float = 0.7
    llm_max_tokens: int = 16000
    # [llm.sub] section
    llm_sub_model: str | None = None


_DEFAULTS = SIOConfig()


def load_config(path: str | None = None) -> SIOConfig:
    """Load SIO configuration from TOML file.

    Args:
        path: Path to config file. Default: ~/.sio/config.toml

    Returns:
        SIOConfig with values from file + defaults for missing keys.

    Raises:
        ValueError: If TOML is invalid.
    """
    if path is None:
        path = os.path.expanduser("~/.sio/config.toml")

    if not os.path.exists(path):
        return SIOConfig()

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        raise ValueError(f"Invalid config file {path}: {e}") from e

    # Warn about unrecognized top-level keys
    _KNOWN_TOP_KEYS = frozenset({
        "embedding_backend", "embedding_model", "embedding_api_url",
        "embedding_api_key", "retention_days", "min_examples",
        "min_failures", "min_sessions", "pattern_threshold", "optimizer",
        "drift_threshold", "collision_threshold", "similarity_threshold",
        "min_pattern_occurrences", "min_dataset_examples", "daily_enabled",
        "weekly_enabled", "stale_days", "llm",
    })
    unknown_keys = set(data.keys()) - _KNOWN_TOP_KEYS
    if unknown_keys:
        logger.warning(
            "Unrecognized keys in config TOML: %s",
            ", ".join(sorted(unknown_keys)),
        )

    # Parse [llm] section
    llm_section = data.get("llm", {})
    llm_sub_section = llm_section.get("sub", {})

    return SIOConfig(
        embedding_backend=data.get("embedding_backend", _DEFAULTS.embedding_backend),
        embedding_model=data.get("embedding_model", _DEFAULTS.embedding_model),
        embedding_api_url=data.get("embedding_api_url"),
        embedding_api_key=data.get("embedding_api_key"),
        retention_days=data.get("retention_days", _DEFAULTS.retention_days),
        min_examples=data.get("min_examples", _DEFAULTS.min_examples),
        min_failures=data.get("min_failures", _DEFAULTS.min_failures),
        min_sessions=data.get("min_sessions", _DEFAULTS.min_sessions),
        pattern_threshold=data.get("pattern_threshold", _DEFAULTS.pattern_threshold),
        optimizer=data.get("optimizer", _DEFAULTS.optimizer),
        drift_threshold=data.get("drift_threshold", _DEFAULTS.drift_threshold),
        collision_threshold=data.get(
            "collision_threshold", _DEFAULTS.collision_threshold,
        ),
        # v2 keys
        similarity_threshold=data.get(
            "similarity_threshold", _DEFAULTS.similarity_threshold,
        ),
        min_pattern_occurrences=data.get(
            "min_pattern_occurrences", _DEFAULTS.min_pattern_occurrences,
        ),
        min_dataset_examples=data.get(
            "min_dataset_examples", _DEFAULTS.min_dataset_examples,
        ),
        daily_enabled=data.get("daily_enabled", _DEFAULTS.daily_enabled),
        weekly_enabled=data.get("weekly_enabled", _DEFAULTS.weekly_enabled),
        stale_days=data.get("stale_days", _DEFAULTS.stale_days),
        # [llm] keys
        llm_model=llm_section.get("model", _DEFAULTS.llm_model),
        llm_api_key_env=llm_section.get("api_key_env", _DEFAULTS.llm_api_key_env),
        llm_api_base_env=llm_section.get("api_base_env", _DEFAULTS.llm_api_base_env),
        llm_temperature=llm_section.get("temperature", _DEFAULTS.llm_temperature),
        llm_max_tokens=llm_section.get("max_tokens", _DEFAULTS.llm_max_tokens),
        # [llm.sub] keys
        llm_sub_model=llm_sub_section.get("model", _DEFAULTS.llm_sub_model),
    )
