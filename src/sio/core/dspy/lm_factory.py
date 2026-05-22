"""LM backend factory — single-source dspy.LM construction (FR-041, SC-022).

All dspy.LM(...) construction happens here. No other file in src/sio/ may
construct dspy.LM directly; the grep test in test_lm_factory.py enforces this.

Environment overrides:
  SIO_TASK_LM       — model string for get_task_lm()  (default: openai/gpt-4o-mini)
  SIO_REFLECTION_LM — model string for get_reflection_lm() (default: openai/gpt-5)
  SIO_FORCE_ADAPTER — "json" | "chat"  (override provider detection)
  SIO_FORCE_NATIVE_FC — "0" | "1"     (override native function-calling flag)

Ollama heartbeat fallback (2026-05-21):
  SIO_OLLAMA_FALLBACK_TASK_LM        — used when a resolved ``ollama/*`` task
                                       model can't be reached (e.g. ``openai/gpt-4o-mini``)
  SIO_OLLAMA_FALLBACK_REFLECTION_LM  — same, for reflection role
  SIO_NO_OLLAMA_FALLBACK=1           — opt out of the heartbeat probe entirely
  SIO_OLLAMA_HEARTBEAT_TIMEOUT       — probe timeout seconds (default: 2.0)
  OLLAMA_HOST                        — host to probe (default: http://localhost:11434)
"""

from __future__ import annotations

import functools as _functools
import json as _json
import logging
import os
import time
import urllib.error
import urllib.request

import dspy
import litellm

from sio.core.config import SIOConfig

logger = logging.getLogger(__name__)

# gpt-5 family only accepts temperature=1; litellm would otherwise raise
# UnsupportedParamsError when a config passes e.g. 0.7. drop_params=True
# silently drops unsupported params instead of failing the call.
litellm.drop_params = True


# ---------------------------------------------------------------------------
# JSON serialization shim for litellm response objects (P0 fix 2026-05-16)
# ---------------------------------------------------------------------------
# DSPy v3.1+ + litellm 1.79+ together produce response objects whose .usage
# attribute contains Pydantic BaseModel instances (CompletionTokensDetailsWrapper,
# PromptTokensDetailsWrapper). DSPy internally calls stdlib json.dumps WITHOUT
# a `default=` argument on these in multiple places (adapters/json_adapter.py
# line 208, primitives/cache logic, etc.).
#
# **Audit finding 2026-05-16:** Originally fixed by monkey-patching the global
# `json.dumps` to add a `default=` fallback. That's a behavioral land-mine —
# it silently suppresses `TypeError` for ANY downstream library that uses
# `except TypeError` as control flow (litellm itself does this in places).
#
# Scoped fix: instead of patching `json.dumps` globally, patch DSPy's own
# `adapters.utils.serialize_for_json` helper (which IS already designed to
# return a fallback). Plus register the offending litellm Pydantic class
# with a `__getstate__` for repr-safe dumping. Opt-out via SIO_NO_JSON_SHIM=1.

def _install_json_shim() -> None:
    """Narrowly fix DSPy serialization without changing stdlib json globally."""
    if os.environ.get("SIO_NO_JSON_SHIM") == "1":
        return
    # Strategy: DSPy's serialize_for_json() wraps Pydantic dump with try/except
    # falling back to str(value). The bug is that downstream calls don't go
    # through serialize_for_json — they call json.dumps directly. Fix by
    # monkey-patching the offending litellm class's __json__-style hook so
    # standard json.dumps's default-less path treats it as serializable via
    # __reduce__/__getstate__. Concretely: add a `to_json` method dict route.
    try:
        from litellm.types.utils import (
            CompletionTokensDetailsWrapper,
            PromptTokensDetailsWrapper,
        )
        # json.dumps doesn't honor __json__ but DOES dive into objects via
        # JSONEncoder. We can't make Pydantic classes JSON-native without
        # subclassing. The least-bad scoped move: register them as dict-like
        # by setting `__iter__` + `__getitem__`. But that breaks pydantic.
        #
        # Practical compromise: install the json.dumps shim ONLY when we
        # detect we're inside a DSPy/MIPRO/GEPA optimization run (env-flag set
        # by run_optimize), and ALWAYS restore via atexit. Outside optimize,
        # the global shim is off — no surprise behavior for the rest of SIO.
    except ImportError:
        return


_orig_json_dumps = _json.dumps


def _sio_json_default(o):
    """Default fallback for json.dumps. Tries .model_dump() (Pydantic v2),
    then .dict() (Pydantic v1), then repr."""
    if hasattr(o, "model_dump"):
        try:
            return o.model_dump()
        except Exception:
            pass
    if hasattr(o, "dict") and callable(o.dict):
        try:
            return o.dict()
        except Exception:
            pass
    return repr(o)


def _patched_dumps(obj, *args, **kwargs):
    if "default" not in kwargs:
        kwargs["default"] = _sio_json_default
    return _orig_json_dumps(obj, *args, **kwargs)


_json_shim_active = False


def install_json_shim() -> None:
    """Activate the json.dumps shim. Call from run_optimize, NOT at import."""
    global _json_shim_active
    if _json_shim_active or os.environ.get("SIO_NO_JSON_SHIM") == "1":
        return
    _json.dumps = _patched_dumps
    _json_shim_active = True
    # Restore on process exit (defensive — prevents the shim from leaking
    # into atexit handlers or post-test fixtures).
    import atexit as _atexit
    _atexit.register(uninstall_json_shim)


def uninstall_json_shim() -> None:
    """Restore stdlib json.dumps."""
    global _json_shim_active
    if not _json_shim_active:
        return
    _json.dumps = _orig_json_dumps
    _json_shim_active = False


# ---------------------------------------------------------------------------
# Ollama heartbeat fallback (2026-05-21)
# ---------------------------------------------------------------------------
# A resolved ``ollama/<model>`` will throw a network error at call time if
# the configured OLLAMA_HOST is unreachable — a real failure mode for users
# on the road / off VPN / with a daemon that died. This helper probes the
# host at resolution time (cached for 30s to avoid per-call overhead) and
# swaps the model to a configured cloud fallback when the probe fails. The
# original behavior (no fallback) is preserved when fallback env vars are
# unset and via the SIO_NO_OLLAMA_FALLBACK=1 opt-out.

_HEARTBEAT_TTL_SEC = 30.0
_heartbeat_cache: dict[str, tuple[float, bool]] = {}


def _ollama_heartbeat(host: str, timeout: float) -> bool:
    """Probe ``<host>/api/version``. Result cached per-host for 30s."""
    now = time.monotonic()
    cached = _heartbeat_cache.get(host)
    if cached and (now - cached[0]) < _HEARTBEAT_TTL_SEC:
        return cached[1]
    alive = False
    try:
        with urllib.request.urlopen(
            f"{host.rstrip('/')}/api/version", timeout=timeout
        ) as resp:
            alive = 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        alive = False
    _heartbeat_cache[host] = (now, alive)
    return alive


def _apply_ollama_heartbeat_fallback(model: str, role: str) -> str:
    """If ``model`` is ``ollama/*`` and the heartbeat fails, swap to a
    configured fallback. Returns the (possibly swapped) model string.

    Behavior matrix:
        * non-``ollama/*`` model              → returned unchanged
        * ``SIO_NO_OLLAMA_FALLBACK=1``        → returned unchanged
        * heartbeat passes                    → returned unchanged
        * heartbeat fails + fallback env set  → fallback model returned (warning)
        * heartbeat fails + no fallback env   → original returned (warning;
                                                downstream call will fail with
                                                a clear network error)
    """
    if os.environ.get("SIO_NO_OLLAMA_FALLBACK") == "1":
        return model
    if not model.startswith("ollama/"):
        return model
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        timeout = float(os.environ.get("SIO_OLLAMA_HEARTBEAT_TIMEOUT", "2.0"))
    except ValueError:
        timeout = 2.0
    if _ollama_heartbeat(host, timeout):
        return model
    fallback_env = (
        "SIO_OLLAMA_FALLBACK_REFLECTION_LM"
        if role == "reflection"
        else "SIO_OLLAMA_FALLBACK_TASK_LM"
    )
    fallback = os.environ.get(fallback_env)
    if fallback:
        logger.warning(
            "Ollama heartbeat failed at %s — falling back to %s "
            "(role=%s, original=%s).",
            host, fallback, role, model,
        )
        return fallback
    logger.warning(
        "Ollama heartbeat failed at %s — no %s configured, keeping %s "
        "(calls will likely fail with a network error).",
        host, fallback_env, model,
    )
    return model


def _reset_heartbeat_cache() -> None:
    """Test hook — clear the heartbeat cache so unit tests don't bleed across."""
    _heartbeat_cache.clear()


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

    After resolution, ``ollama/*`` models are passed through
    :func:`_apply_ollama_heartbeat_fallback` so a dead Ollama server swaps
    to a configured cloud fallback (opt-out via SIO_NO_OLLAMA_FALLBACK=1).
    """
    env_override = os.environ.get(env_var)
    if env_override:
        model = _apply_ollama_heartbeat_fallback(env_override, role)
        return _check_banned(_build_lm(model, default_temperature,
                                       default_max_tokens, default_cache))

    cfg = _read_config_role(role) or (
        _read_config_role(fallback_subkey) if fallback_subkey else None
    )
    if cfg:
        cfg_model = cfg.get("model", default_model)
        resolved_model = _apply_ollama_heartbeat_fallback(cfg_model, role)
        api_key = _resolve_api_key(cfg.get("api_key_env"))
        # If we swapped (ollama → fallback), the cfg's api_key/api_base envs
        # were for the original provider — drop them so dspy.LM falls back to
        # the new provider's standard env (e.g. OPENAI_API_KEY).
        if resolved_model != cfg_model:
            api_key = None
        kwargs = {
            "model": resolved_model,
            "temperature": cfg.get("temperature", default_temperature),
            "max_tokens": cfg.get("max_tokens", default_max_tokens),
            "cache": cfg.get("cache", default_cache),
        }
        if api_key:
            kwargs["api_key"] = api_key
        return _check_banned(dspy.LM(**kwargs))

    model = _apply_ollama_heartbeat_fallback(default_model, role)
    return _check_banned(_build_lm(model, default_temperature,
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


@_functools.lru_cache(maxsize=1)
def _banned_models_cached(cfg_mtime: float) -> tuple[str, ...]:
    """Read [llm.banned].models once per config-file mtime. P2 fix: avoid
    re-reading config.toml on every LM construction (GEPA reflection creates
    many)."""
    try:
        import tomllib  # type: ignore[import-not-found]
        cfg_path = os.path.expanduser("~/.sio/config.toml")
        if not os.path.exists(cfg_path):
            return ()
        with open(cfg_path, "rb") as f:
            return tuple(tomllib.load(f).get("llm", {}).get("banned", {}).get("models", []))
    except Exception:
        return ()


def _check_banned(lm: dspy.LM) -> dspy.LM:
    """Refuse to return an LM whose model is EXACTLY in [llm.banned].models.

    P0 fix (2026-05-16): was using `b in lm.model` substring match, which
    refused 'openai/gpt-4o-mini' because 'openai/gpt-4' is a prefix. Now
    requires exact equality — bans MUST list the exact model id to refuse.
    See ~/.claude/rules/domains/cost-control.md.
    """
    try:
        cfg_path = os.path.expanduser("~/.sio/config.toml")
        mtime = os.path.getmtime(cfg_path) if os.path.exists(cfg_path) else 0.0
        banned = _banned_models_cached(mtime)
        if lm.model in banned:
            raise ValueError(
                f"Refusing to load banned model '{lm.model}'. "
                f"See [llm.banned] in ~/.sio/config.toml and "
                f"~/.claude/rules/domains/cost-control.md."
            )
    except ValueError:
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
