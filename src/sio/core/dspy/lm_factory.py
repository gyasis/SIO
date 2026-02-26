"""LM backend factory — creates dspy.LM from config or env var detection."""

from __future__ import annotations

import logging
import os

import dspy

from sio.core.config import SIOConfig

logger = logging.getLogger(__name__)


def create_lm(config: SIOConfig) -> dspy.LM | None:
    """Create a dspy.LM from config or auto-detected env vars.

    Priority:
    1. Config file llm_model setting
    2. AZURE_OPENAI_API_KEY env var -> azure/DeepSeek-R1-0528
    3. ANTHROPIC_API_KEY env var -> anthropic/claude-sonnet-4-20250514
    4. OPENAI_API_KEY env var -> openai/gpt-4o
    5. None (no LLM available)

    Args:
        config: SIOConfig instance with optional LLM settings.

    Returns:
        Configured dspy.LM or None if no LLM is available.
    """
    # Priority 1: Explicit config
    if config.llm_model:
        kwargs: dict = {
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
            "num_retries": 3,
        }
        if config.llm_api_key_env:
            api_key = os.environ.get(config.llm_api_key_env)
            if api_key:
                kwargs["api_key"] = api_key
        if config.llm_api_base_env:
            api_base = os.environ.get(config.llm_api_base_env)
            if api_base:
                kwargs["api_base"] = api_base
        return dspy.LM(**kwargs)

    # Priority 2: Azure OpenAI
    azure_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if azure_key:
        kwargs = {
            "model": "azure/DeepSeek-R1-0528",
            "api_key": azure_key,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
            "num_retries": 3,
        }
        azure_base = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if azure_base:
            kwargs["api_base"] = azure_base
        return dspy.LM(**kwargs)

    # Priority 3: Anthropic
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        return dspy.LM(
            model="anthropic/claude-sonnet-4-20250514",
            api_key=anthropic_key,
            temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens,
        )

    # Priority 4: OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return dspy.LM(
            model="openai/gpt-4o",
            api_key=openai_key,
            temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens,
        )

    # Priority 5: No LLM available
    logger.info(
        "No LLM backend available. To configure, either: "
        "(1) set llm.model in ~/.sio/config.toml, "
        "(2) set AZURE_OPENAI_API_KEY, "
        "(3) set ANTHROPIC_API_KEY, or "
        "(4) set OPENAI_API_KEY environment variable."
    )
    return None


def create_sub_lm(config: SIOConfig) -> dspy.LM | None:
    """Create a cheap sub-LM for metric evaluation and RLM.

    Uses config.llm_sub_model if set, otherwise falls back to the main LM.

    Args:
        config: SIOConfig instance with optional sub-LM settings.

    Returns:
        Configured dspy.LM for sub-tasks, or None if no LLM is available.
    """
    if config.llm_sub_model:
        kwargs: dict = {
            "model": config.llm_sub_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
        }
        # Sub-LM inherits API credentials from the main config
        if config.llm_api_key_env:
            api_key = os.environ.get(config.llm_api_key_env)
            if api_key:
                kwargs["api_key"] = api_key
        if config.llm_api_base_env:
            api_base = os.environ.get(config.llm_api_base_env)
            if api_base:
                kwargs["api_base"] = api_base
        return dspy.LM(**kwargs)

    # Fall back to main LM
    return create_lm(config)
