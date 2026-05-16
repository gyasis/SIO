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

import json as _json
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
# JSON serialization shim for litellm response objects
# ---------------------------------------------------------------------------
# DSPy v3.1+ + litellm 1.79+ together produce response objects whose .usage
# attribute contains Pydantic BaseModel instances (CompletionTokensDetailsWrapper,
# PromptTokensDetailsWrapper). DSPy internally calls stdlib json.dumps WITHOUT
# a `default=` argument on these in multiple places (adapters/json_adapter.py
# line 208, primitives/cache logic, etc.). That fails with
# "Object of type CompletionTokensDetailsWrapper is not JSON serializable".
#
# Fix: install a JSONEncoder default that turns any Pydantic BaseModel into a
# dict via .model_dump() (preferred) or falls back to repr. This applies to
# every json.dumps call in the process, including DSPy's. It does NOT change
# any user code in SIO that already supplies its own default=.
#
# Origin: 2026-05-16 MIPROv2 runs a592c6e2 and 80336d65 both failed with this.
# See sio_optimizer_ladder PRD; remove this shim if/when DSPy ships a fix.

_orig_json_dumps = _json.dumps


def _sio_json_default(o):
    if hasattr(o, "model_dump"):
        try:
            return o.model_dump()
        except Exception:
            pass
    if hasattr(o, "dict"):
        try:
            return o.dict()
        except Exception:
            pass
    return repr(o)


def _patched_dumps(obj, *args, **kwargs):
    if "default" not in kwargs:
        kwargs["default"] = _sio_json_default
    return _orig_json_dumps(obj, *args, **kwargs)


_json.dumps = _patched_dumps


# ---------------------------------------------------------------------------
# New contract functions (FR-041, contracts/dspy-module-api.md §1)
# ---------------------------------------------------------------------------


def _resolve_role_lm(role: str, env_var: str, fallback_subkey: str | None,
                     default_model: str, default_temperature: float,
                     default_max_tokens: int, default_cache: bool) -> dspy.LM:
    """Resolve a per-role LM in this order:
      1) ENV var override (SIO_TASK_LM / SIO_REFLECTION_LM)
      2) ``[llm.<role>]`` block in ~/.sio/config.toml
      3) ``[llm.<fallback_subkey>]`` block (e.g. [llm.sub] for task)
      4) Hard default (Gemini family — matches active env)

    Also enforces the [llm.banned].models list — refuses to load any model
    listed there (Principle XII clause 4).
    """
    env_override = os.environ.get(env_var)
    if env_override:
        return _check_banned(_build_lm(env_override, default_temperature,
                                       default_max_tokens, default_cache))

    cfg = _read_config_role(role) or (
        _read_config_role(fallback_subkey) if fallback_subkey else None
    )
    if cfg:
        api_key = _resolve_api_key(cfg.get("api_key_env"))
        kwargs = {
            "model": cfg.get("model", default_model),
            "temperature": cfg.get("temperature", default_temperature),
            "max_tokens": cfg.get("max_tokens", default_max_tokens),
            "cache": cfg.get("cache", default_cache),
        }
        if api_key:
            kwargs["api_key"] = api_key
        return _check_banned(dspy.LM(**kwargs))

    return _check_banned(_build_lm(default_model, default_temperature,
                                   default_max_tokens, default_cache))


def _read_config_role(role_key: str | None) -> dict | None:
    """Read [llm.<role_key>] from ~/.sio/config.toml. Returns None on miss."""
    if not role_key:
        return None
    try:
        import tomllib  # type: ignore[import-not-found]  # py311+
    except ImportError:
        return None
    cfg_path = os.path.expanduser("~/.sio/config.toml")
    if not os.path.exists(cfg_path):
        return None
    try:
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("llm", {}).get(role_key)
    except Exception:
        return None


def _resolve_api_key(env_var_name: str | None) -> str | None:
    if not env_var_name:
        return None
    return os.environ.get(env_var_name)


def _build_lm(model: str, temperature: float, max_tokens: int, cache: bool) -> dspy.LM:
    return dspy.LM(model, cache=cache, temperature=temperature, max_tokens=max_tokens)


def _check_banned(lm: dspy.LM) -> dspy.LM:
    """Refuse to return an LM whose model is in [llm.banned].models (XII clause 4)."""
    try:
        import tomllib  # type: ignore[import-not-found]
        cfg_path = os.path.expanduser("~/.sio/config.toml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                banned = tomllib.load(f).get("llm", {}).get("banned", {}).get("models", [])
            if any(b in lm.model for b in banned):
                raise ValueError(
                    f"Refusing to load banned model '{lm.model}'. "
                    f"See [llm.banned] in ~/.sio/config.toml and "
                    f"~/.claude/rules/domains/cost-control.md."
                )
    except (ValueError, ImportError):
        raise
    except Exception:
        pass  # config read failures shouldn't break LM construction
    return lm


def get_task_lm() -> dspy.LM:
    """LM used for normal module forward passes. Cheap, fast, cached.

    Resolution order: SIO_TASK_LM env > [llm.task] config > [llm.sub] config >
    hard default (gemini/gemini-flash-latest — matches active Gemini env).
    """
    return _resolve_role_lm(
        role="task", env_var="SIO_TASK_LM", fallback_subkey="sub",
        default_model="gemini/gemini-flash-latest",
        default_temperature=0.0, default_max_tokens=4096, default_cache=True,
    )


def get_reflection_lm() -> dspy.LM:
    """Strong LM used by GEPA to critique prompt candidates. Expensive, uncached.

    Resolution order: SIO_REFLECTION_LM env > [llm.reflection] config > [llm]
    config > hard default (gemini/gemini-pro-latest — matches active Gemini env).
    NEVER defaults to gpt-5 (must be opt-in via env or --reflection-mode).
    """
    return _resolve_role_lm(
        role="reflection", env_var="SIO_REFLECTION_LM", fallback_subkey=None,
        default_model="gemini/gemini-pro-latest",
        default_temperature=1.0, default_max_tokens=32000, default_cache=False,
    )


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
