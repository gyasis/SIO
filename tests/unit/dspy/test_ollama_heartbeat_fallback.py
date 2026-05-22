"""Tests for Ollama heartbeat fallback in lm_factory (2026-05-21).

Covers _apply_ollama_heartbeat_fallback() and _ollama_heartbeat() — the
helpers that swap a configured ``ollama/<model>`` to a cloud fallback when
the Ollama server is unreachable at LM-resolution time.

Mocks ``urllib.request.urlopen`` so tests don't hit the network. Resets the
per-host heartbeat cache between tests so results don't bleed across.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from sio.core.dspy import lm_factory


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Per-test cleanup: clear heartbeat cache, scrub all Ollama-related env."""
    lm_factory._reset_heartbeat_cache()
    for var in (
        "SIO_NO_OLLAMA_FALLBACK",
        "SIO_OLLAMA_FALLBACK_TASK_LM",
        "SIO_OLLAMA_FALLBACK_REFLECTION_LM",
        "SIO_OLLAMA_HEARTBEAT_TIMEOUT",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    lm_factory._reset_heartbeat_cache()


def _fake_response(status: int = 200) -> MagicMock:
    """Build a context-manager-style fake for urlopen."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(status=status))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Non-ollama models are passed through unchanged (no heartbeat call)
# ---------------------------------------------------------------------------


def test_non_ollama_model_unchanged_no_heartbeat():
    """openai/gpt-4o-mini must be returned as-is, with no urlopen call."""
    with patch("sio.core.dspy.lm_factory.urllib.request.urlopen") as m:
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "openai/gpt-4o-mini", role="task"
        )
    assert result == "openai/gpt-4o-mini"
    m.assert_not_called()


def test_anthropic_model_unchanged_no_heartbeat():
    with patch("sio.core.dspy.lm_factory.urllib.request.urlopen") as m:
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "anthropic/claude-opus-4", role="reflection"
        )
    assert result == "anthropic/claude-opus-4"
    m.assert_not_called()


# ---------------------------------------------------------------------------
# Ollama-up keeps the original model
# ---------------------------------------------------------------------------


def test_ollama_alive_keeps_original_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.100:11434")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        return_value=_fake_response(200),
    ):
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    assert result == "ollama/qwen3-coder:30b"


# ---------------------------------------------------------------------------
# Ollama-down + fallback configured → swap
# ---------------------------------------------------------------------------


def test_ollama_down_with_fallback_swaps_task(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://10.99.99.99:11434")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    assert result == "openai/gpt-4o-mini"


def test_ollama_down_with_fallback_swaps_reflection(monkeypatch):
    """Reflection role reads SIO_OLLAMA_FALLBACK_REFLECTION_LM, not the task one."""
    monkeypatch.setenv("OLLAMA_HOST", "http://10.99.99.99:11434")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_REFLECTION_LM", "openai/gpt-5-mini")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        side_effect=TimeoutError("timed out"),
    ):
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/deepseek-r1:32b", role="reflection"
        )
    assert result == "openai/gpt-5-mini"


# ---------------------------------------------------------------------------
# Ollama-down + no fallback configured → keep original (downstream will fail)
# ---------------------------------------------------------------------------


def test_ollama_down_no_fallback_keeps_original(monkeypatch, caplog):
    monkeypatch.setenv("OLLAMA_HOST", "http://10.99.99.99:11434")
    # Note: no SIO_OLLAMA_FALLBACK_* env set
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        side_effect=urllib.error.URLError("nope"),
    ), caplog.at_level("WARNING"):
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    assert result == "ollama/qwen3-coder:30b"
    assert any(
        "no SIO_OLLAMA_FALLBACK_TASK_LM configured" in r.message
        for r in caplog.records
    ), "Expected a warning naming the missing env var"


# ---------------------------------------------------------------------------
# Opt-out via SIO_NO_OLLAMA_FALLBACK=1 disables the whole helper
# ---------------------------------------------------------------------------


def test_opt_out_skips_heartbeat_and_passes_through(monkeypatch):
    monkeypatch.setenv("SIO_NO_OLLAMA_FALLBACK", "1")
    monkeypatch.setenv("OLLAMA_HOST", "http://10.99.99.99:11434")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    with patch("sio.core.dspy.lm_factory.urllib.request.urlopen") as m:
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    assert result == "ollama/qwen3-coder:30b"
    m.assert_not_called()


# ---------------------------------------------------------------------------
# Heartbeat cache prevents repeated probes within 30s window
# ---------------------------------------------------------------------------


def test_heartbeat_cached_within_window(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.100:11434")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        return_value=_fake_response(200),
    ) as m:
        for _ in range(5):
            lm_factory._apply_ollama_heartbeat_fallback(
                "ollama/qwen3-coder:30b", role="task"
            )
    assert m.call_count == 1, (
        f"Expected one urlopen call (cached), got {m.call_count}"
    )


# ---------------------------------------------------------------------------
# Custom timeout env is honored
# ---------------------------------------------------------------------------


def test_custom_timeout_env_honored(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.100:11434")
    monkeypatch.setenv("SIO_OLLAMA_HEARTBEAT_TIMEOUT", "0.5")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        return_value=_fake_response(200),
    ) as m:
        lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    # urlopen called with timeout=0.5
    _, kwargs = m.call_args
    assert kwargs.get("timeout") == 0.5


def test_invalid_timeout_env_falls_back_to_default(monkeypatch):
    """A malformed SIO_OLLAMA_HEARTBEAT_TIMEOUT must not crash; default is used."""
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.100:11434")
    monkeypatch.setenv("SIO_OLLAMA_HEARTBEAT_TIMEOUT", "not-a-number")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        return_value=_fake_response(200),
    ) as m:
        lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    _, kwargs = m.call_args
    assert kwargs.get("timeout") == 2.0  # default


# ---------------------------------------------------------------------------
# Default OLLAMA_HOST is localhost:11434 when env unset
# ---------------------------------------------------------------------------


def test_default_host_is_localhost(monkeypatch):
    """With OLLAMA_HOST unset, probe should hit http://localhost:11434."""
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        return_value=_fake_response(200),
    ) as m:
        lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    url_arg = m.call_args[0][0]
    assert url_arg == "http://localhost:11434/api/version"


# ---------------------------------------------------------------------------
# 5xx response is treated as a failure too
# ---------------------------------------------------------------------------


def test_5xx_response_treated_as_dead(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.100:11434")
    monkeypatch.setenv("SIO_OLLAMA_FALLBACK_TASK_LM", "openai/gpt-4o-mini")
    with patch(
        "sio.core.dspy.lm_factory.urllib.request.urlopen",
        return_value=_fake_response(503),
    ):
        result = lm_factory._apply_ollama_heartbeat_fallback(
            "ollama/qwen3-coder:30b", role="task"
        )
    assert result == "openai/gpt-4o-mini"
