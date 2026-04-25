"""LM backend factory — single-source dspy.LM construction (FR-041, SC-022).

All dspy.LM(...) construction happens here. No other file in src/sio/ may
construct dspy.LM directly; the grep test in test_lm_factory.py enforces this.

Environment overrides:
  SIO_TASK_LM       — model string for get_task_lm()  (default: openai/gpt-4o-mini)
  SIO_REFLECTION_LM — model string for get_reflection_lm() (default: openai/gpt-5)
  SIO_FORCE_ADAPTER — "json" | "chat"  (override provider detection)
  SIO_FORCE_NATIVE_FC — "0" | "1"     (override native function-calling flag)
"""

from __future__ import annotations

import logging
import os

import dspy
import litellm

from sio.core.config import SIOConfig

logger = logging.getLogger(__name__)

# gpt-5 family only accepts temperature=1; litellm would otherwise raise
# UnsupportedParamsError when a config passes e.g. 0.7. drop_params=True
# silently drops unsupported params instead of failing the call.
litellm.drop_params = True


# ---------------------------------------------------------------------------
# New contract functions (FR-041, contracts/dspy-module-api.md §1)
# ---------------------------------------------------------------------------


def get_task_lm() -> dspy.LM:
    """LM used for normal module forward passes. Cheap, fast, cached."""
    model = os.environ.get("SIO_TASK_LM", "openai/gpt-4o-mini")
    return dspy.LM(model, cache=True, temperature=0.0, max_tokens=4096)


def get_reflection_lm() -> dspy.LM:
    """Strong LM used by GEPA to critique prompt candidates. Expensive, uncached."""
    model = os.environ.get("SIO_REFLECTION_LM", "openai/gpt-5")
    return dspy.LM(model, cache=False, temperature=1.0, max_tokens=32000)


def get_adapter(lm: dspy.LM) -> dspy.Adapter:
    """Provider-aware adapter selection (FR-040, R-12).

    Honors SIO_FORCE_ADAPTER (json|chat) and SIO_FORCE_NATIVE_FC (0|1) env overrides.
    Falls back to provider detection:
      openai / anthropic  -> ChatAdapter(use_native_function_calling=True)
      ollama              -> JSONAdapter(use_native_function_calling=False)
      unknown             -> ChatAdapter(use_native_function_calling=False)

    NOTE: Azure provider support was removed 2026-04-24 after the Azure OpenAI
    endpoints were deactivated (2026-04-16). Do not re-introduce.
    """
    forced = os.environ.get("SIO_FORCE_ADAPTER")
    native_env = os.environ.get("SIO_FORCE_NATIVE_FC")

    if forced == "json":
        native = native_env != "0" if native_env is not None else True
        return dspy.JSONAdapter(use_native_function_calling=native)
    if forced == "chat":
        native = native_env != "0" if native_env is not None else True
        return dspy.ChatAdapter(use_native_function_calling=native)

    provider = lm.model.split("/", 1)[0]
    if provider in ("openai", "anthropic"):
        return dspy.ChatAdapter(use_native_function_calling=True)
    if provider == "ollama":
        return dspy.JSONAdapter(use_native_function_calling=False)
    return dspy.ChatAdapter(use_native_function_calling=False)


def configure_default() -> None:
    """Call at process start. Binds task LM + provider adapter to dspy globally."""
    lm = get_task_lm()
    dspy.configure(lm=lm, adapter=get_adapter(lm))


# ---------------------------------------------------------------------------
# Legacy factory functions (preserved for backward compatibility)
# ---------------------------------------------------------------------------


def create_lm(config: SIOConfig) -> dspy.LM | None:
    """Create a dspy.LM from config or auto-detected env vars.

    Priority:
    1. Config file llm_model setting
    2. OPENAI_API_KEY env var -> openai/gpt-4o-mini
    3. ANTHROPIC_API_KEY env var -> anthropic/claude-sonnet-4-20250514
    4. None (no LLM available)

    NOTE: Azure OpenAI was removed 2026-04-24 after endpoints deactivated
    2026-04-16 (see memory file ref_azure_endpoints_deactivated.md).
    Do not re-introduce an Azure branch here.
    """
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

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return dspy.LM(
            model="openai/gpt-4o-mini",
            api_key=openai_key,
            temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens,
        )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        return dspy.LM(
            model="anthropic/claude-sonnet-4-20250514",
            api_key=anthropic_key,
            temperature=config.llm_temperature,
            max_tokens=config.llm_max_tokens,
        )

    logger.info(
        "No LLM backend available. To configure, either: "
        "(1) set llm.model in ~/.sio/config.toml, "
        "(2) set OPENAI_API_KEY, or "
        "(3) set ANTHROPIC_API_KEY environment variable."
    )
    return None


def create_sub_lm(config: SIOConfig) -> dspy.LM | None:
    """Create a cheap sub-LM for metric evaluation and RLM.

    Uses config.llm_sub_model if set, otherwise falls back to the main LM.
    """
    if config.llm_sub_model:
        kwargs: dict = {
            "model": config.llm_sub_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
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

    return create_lm(config)
