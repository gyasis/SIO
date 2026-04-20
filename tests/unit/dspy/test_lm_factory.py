"""Failing tests for lm_factory.py — T021 (TDD red).

Tests assert (per contracts/dspy-module-api.md §1):
  1. get_task_lm() returns dspy.LM with cache=True
  2. get_reflection_lm() returns dspy.LM with cache=False
  3. SIO_TASK_LM env override changes model
  4. SIO_REFLECTION_LM env override changes model
  5. get_adapter() returns provider-aware adapter instances
  6. SIO_FORCE_ADAPTER=json env override works
  7. Grep: zero dspy.LM( calls outside lm_factory.py in src/sio/

Run to confirm RED before implementing lm_factory.py:
    uv run pytest tests/unit/dspy/test_lm_factory.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _import_lm_factory():
    from sio.core.dspy import lm_factory  # noqa: PLC0415
    return lm_factory


def _import_get_task_lm():
    from sio.core.dspy.lm_factory import get_task_lm  # noqa: PLC0415
    return get_task_lm


def _import_get_reflection_lm():
    from sio.core.dspy.lm_factory import get_reflection_lm  # noqa: PLC0415
    return get_reflection_lm


def _import_get_adapter():
    from sio.core.dspy.lm_factory import get_adapter  # noqa: PLC0415
    return get_adapter


# ---------------------------------------------------------------------------
# 1. get_task_lm() returns dspy.LM with cache=True
# ---------------------------------------------------------------------------

def test_get_task_lm_returns_dspy_lm():
    """get_task_lm() must return a dspy.LM instance."""
    import dspy  # noqa: PLC0415
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()
    assert isinstance(lm, dspy.LM), f"Expected dspy.LM, got {type(lm)}"


def test_get_task_lm_has_cache_true():
    """get_task_lm() must return a dspy.LM with cache=True."""
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()
    assert getattr(lm, "cache", None) is True, (
        f"Expected cache=True on task LM, got cache={getattr(lm, 'cache', 'MISSING')!r}"
    )


# ---------------------------------------------------------------------------
# 2. get_reflection_lm() returns dspy.LM with cache=False
# ---------------------------------------------------------------------------

def test_get_reflection_lm_returns_dspy_lm():
    """get_reflection_lm() must return a dspy.LM instance."""
    import dspy  # noqa: PLC0415
    get_reflection_lm = _import_get_reflection_lm()
    lm = get_reflection_lm()
    assert isinstance(lm, dspy.LM), f"Expected dspy.LM, got {type(lm)}"


def test_get_reflection_lm_has_cache_false():
    """get_reflection_lm() must return a dspy.LM with cache=False."""
    get_reflection_lm = _import_get_reflection_lm()
    lm = get_reflection_lm()
    assert getattr(lm, "cache", None) is False, (
        f"Expected cache=False on reflection LM, got cache={getattr(lm, 'cache', 'MISSING')!r}"
    )


# ---------------------------------------------------------------------------
# 3. SIO_TASK_LM env override
# ---------------------------------------------------------------------------

def test_get_task_lm_env_override(monkeypatch):
    """SIO_TASK_LM env var overrides the default model."""
    monkeypatch.setenv("SIO_TASK_LM", "test/dummy-model")
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()
    assert lm.model == "test/dummy-model", (
        f"Expected model='test/dummy-model', got {lm.model!r}"
    )


def test_get_task_lm_default_model_without_env(monkeypatch):
    """Without SIO_TASK_LM env var, get_task_lm() uses default model."""
    monkeypatch.delenv("SIO_TASK_LM", raising=False)
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()
    # Default from contract is openai/gpt-4o-mini
    assert lm.model == "openai/gpt-4o-mini", (
        f"Expected default model='openai/gpt-4o-mini', got {lm.model!r}"
    )


# ---------------------------------------------------------------------------
# 4. SIO_REFLECTION_LM env override
# ---------------------------------------------------------------------------

def test_get_reflection_lm_env_override(monkeypatch):
    """SIO_REFLECTION_LM env var overrides the default model."""
    monkeypatch.setenv("SIO_REFLECTION_LM", "test/big-model")
    get_reflection_lm = _import_get_reflection_lm()
    lm = get_reflection_lm()
    assert lm.model == "test/big-model", (
        f"Expected model='test/big-model', got {lm.model!r}"
    )


# ---------------------------------------------------------------------------
# 5. get_adapter() — provider-aware
# ---------------------------------------------------------------------------

def test_get_adapter_openai_returns_chat_adapter_with_native_fc(monkeypatch):
    """get_adapter() for openai/* model returns ChatAdapter(use_native_function_calling=True)."""
    import dspy  # noqa: PLC0415
    monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
    monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
    get_adapter = _import_get_adapter()

    monkeypatch.setenv("SIO_TASK_LM", "openai/gpt-4o")
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()

    adapter = get_adapter(lm)
    assert isinstance(adapter, dspy.ChatAdapter), (
        f"Expected ChatAdapter for openai model, got {type(adapter).__name__}"
    )
    assert getattr(adapter, "use_native_function_calling", None) is True, (
        "Expected use_native_function_calling=True for openai provider"
    )


def test_get_adapter_anthropic_returns_chat_adapter_with_native_fc(monkeypatch):
    """get_adapter() for anthropic/* model returns ChatAdapter(use_native_function_calling=True)."""
    import dspy  # noqa: PLC0415
    monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
    monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
    get_adapter = _import_get_adapter()

    monkeypatch.setenv("SIO_TASK_LM", "anthropic/claude-opus")
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()

    adapter = get_adapter(lm)
    assert isinstance(adapter, dspy.ChatAdapter)
    assert getattr(adapter, "use_native_function_calling", None) is True


def test_get_adapter_ollama_returns_json_adapter_without_native_fc(monkeypatch):
    """get_adapter() for ollama/* model returns JSONAdapter(use_native_function_calling=False)."""
    import dspy  # noqa: PLC0415
    monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
    monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
    get_adapter = _import_get_adapter()

    monkeypatch.setenv("SIO_TASK_LM", "ollama/llama3")
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()

    adapter = get_adapter(lm)
    assert isinstance(adapter, dspy.JSONAdapter), (
        f"Expected JSONAdapter for ollama model, got {type(adapter).__name__}"
    )
    assert getattr(adapter, "use_native_function_calling", None) is False


def test_get_adapter_unknown_provider_safe_fallback(monkeypatch):
    """get_adapter() for unknown/* model returns ChatAdapter(use_native_function_calling=False)."""
    import dspy  # noqa: PLC0415
    monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
    monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
    get_adapter = _import_get_adapter()

    monkeypatch.setenv("SIO_TASK_LM", "unknown/mystery-model")
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()

    adapter = get_adapter(lm)
    assert isinstance(adapter, dspy.ChatAdapter)
    assert getattr(adapter, "use_native_function_calling", None) is False


# ---------------------------------------------------------------------------
# 6. SIO_FORCE_ADAPTER=json override
# ---------------------------------------------------------------------------

def test_force_adapter_json_env_override(monkeypatch):
    """SIO_FORCE_ADAPTER=json forces JSONAdapter regardless of provider."""
    import dspy  # noqa: PLC0415
    monkeypatch.setenv("SIO_FORCE_ADAPTER", "json")
    monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
    get_adapter = _import_get_adapter()

    monkeypatch.setenv("SIO_TASK_LM", "openai/gpt-4o")
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()

    adapter = get_adapter(lm)
    assert isinstance(adapter, dspy.JSONAdapter), (
        f"Expected JSONAdapter when SIO_FORCE_ADAPTER=json, got {type(adapter).__name__}"
    )


def test_force_adapter_chat_env_override(monkeypatch):
    """SIO_FORCE_ADAPTER=chat forces ChatAdapter regardless of provider."""
    import dspy  # noqa: PLC0415
    monkeypatch.setenv("SIO_FORCE_ADAPTER", "chat")
    monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
    get_adapter = _import_get_adapter()

    monkeypatch.setenv("SIO_TASK_LM", "ollama/llama3")
    get_task_lm = _import_get_task_lm()
    lm = get_task_lm()

    adapter = get_adapter(lm)
    assert isinstance(adapter, dspy.ChatAdapter), (
        f"Expected ChatAdapter when SIO_FORCE_ADAPTER=chat, got {type(adapter).__name__}"
    )


# ---------------------------------------------------------------------------
# 7. Grep-style test: zero dspy.LM( calls outside lm_factory.py in src/sio/
# ---------------------------------------------------------------------------

def _src_sio_root() -> Path:
    """Resolve src/sio/ relative to this test file."""
    tests_unit_dspy = Path(__file__).parent
    project_root = tests_unit_dspy.parent.parent.parent
    candidate = project_root / "src" / "sio"
    if not candidate.is_dir():
        pytest.skip(f"src/sio/ not found at {candidate}; skipping grep test")
    return candidate


_FACTORY_FILE = Path("sio") / "core" / "dspy" / "lm_factory.py"
_DIRECT_LM_PATTERN = re.compile(r"dspy\.LM\s*\(")


def test_no_direct_dspy_lm_calls_outside_factory():
    """Zero lines in src/sio/ match dspy.LM( except in lm_factory.py (SC-022)."""
    src_root = _src_sio_root()
    violations: list[str] = []

    for py_file in src_root.rglob("*.py"):
        # Skip the canonical factory file
        if py_file.parts[-4:] == ("sio", "core", "dspy", "lm_factory.py"):
            continue
        # Skip test files under src/ (should not exist, but be safe)
        if "test" in py_file.name.lower():
            continue

        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _DIRECT_LM_PATTERN.search(line):
                violations.append(
                    f"{py_file.relative_to(src_root.parent.parent)}:{lineno}: {line.strip()}"
                )

    assert not violations, (
        "SC-022 violation — direct dspy.LM( calls found outside lm_factory.py:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
