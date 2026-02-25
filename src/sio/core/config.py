"""Configuration loader — reads ~/.sio/config.toml with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


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
    )
