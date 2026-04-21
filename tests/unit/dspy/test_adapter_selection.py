"""T064 [US9] Unit tests for adapter selection in lm_factory.get_adapter().

Verifies (FR-040, SC-021):
  1. openai/* → ChatAdapter(use_native_function_calling=True)
  2. anthropic/* → ChatAdapter(use_native_function_calling=True)
  3. azure/* → ChatAdapter(use_native_function_calling=True)
  4. ollama/* → JSONAdapter(use_native_function_calling=False)
  5. unknown/* → ChatAdapter(use_native_function_calling=False)
  6. SIO_FORCE_ADAPTER=json → JSONAdapter regardless of provider
  7. SIO_FORCE_ADAPTER=chat → ChatAdapter regardless of provider
  8. SIO_FORCE_NATIVE_FC=0 overrides native_fc flag when SIO_FORCE_ADAPTER set

Run:
    uv run pytest tests/unit/dspy/test_adapter_selection.py -v
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_adapter():
    from sio.core.dspy.lm_factory import get_adapter  # noqa: PLC0415

    return get_adapter


def _make_lm(model: str):
    """Create a dspy.LM with the given model string."""
    import dspy  # noqa: PLC0415

    return dspy.LM(model, cache=False)


# ---------------------------------------------------------------------------
# Provider-based selection tests
# ---------------------------------------------------------------------------


class TestProviderAdapterRouting:
    """get_adapter() routes correctly based on the LM model string prefix."""

    def test_openai_returns_chat_adapter_with_native_fc(self, monkeypatch):
        """openai/* model → ChatAdapter(use_native_function_calling=True)."""
        import dspy  # noqa: PLC0415

        monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
        monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
        get_adapter = _get_adapter()
        lm = _make_lm("openai/gpt-4o")
        adapter = get_adapter(lm)
        assert isinstance(adapter, dspy.ChatAdapter), (
            f"Expected ChatAdapter for openai model, got {type(adapter).__name__}"
        )
        assert getattr(adapter, "use_native_function_calling", None) is True, (
            "openai provider should use native function calling"
        )

    def test_anthropic_returns_chat_adapter_with_native_fc(self, monkeypatch):
        """anthropic/* model → ChatAdapter(use_native_function_calling=True)."""
        import dspy  # noqa: PLC0415

        monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
        monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
        get_adapter = _get_adapter()
        lm = _make_lm("anthropic/claude-opus-4")
        adapter = get_adapter(lm)
        assert isinstance(adapter, dspy.ChatAdapter), (
            f"Expected ChatAdapter for anthropic model, got {type(adapter).__name__}"
        )
        assert getattr(adapter, "use_native_function_calling", None) is True

    def test_azure_returns_chat_adapter_with_native_fc(self, monkeypatch):
        """azure/* model → ChatAdapter(use_native_function_calling=True)."""
        import dspy  # noqa: PLC0415

        monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
        monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
        get_adapter = _get_adapter()
        lm = _make_lm("azure/DeepSeek-R1")
        adapter = get_adapter(lm)
        assert isinstance(adapter, dspy.ChatAdapter), (
            f"Expected ChatAdapter for azure model, got {type(adapter).__name__}"
        )
        assert getattr(adapter, "use_native_function_calling", None) is True

    def test_ollama_returns_json_adapter_without_native_fc(self, monkeypatch):
        """ollama/* model → JSONAdapter(use_native_function_calling=False)."""
        import dspy  # noqa: PLC0415

        monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
        monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
        get_adapter = _get_adapter()
        lm = _make_lm("ollama/llama3")
        adapter = get_adapter(lm)
        assert isinstance(adapter, dspy.JSONAdapter), (
            f"Expected JSONAdapter for ollama model, got {type(adapter).__name__}"
        )
        assert getattr(adapter, "use_native_function_calling", None) is False, (
            "ollama provider should NOT use native function calling"
        )

    def test_unknown_provider_returns_safe_fallback(self, monkeypatch):
        """unknown/* model → ChatAdapter(use_native_function_calling=False)."""
        import dspy  # noqa: PLC0415

        monkeypatch.delenv("SIO_FORCE_ADAPTER", raising=False)
        monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
        get_adapter = _get_adapter()
        lm = _make_lm("huggingface/llama-70b")
        adapter = get_adapter(lm)
        assert isinstance(adapter, dspy.ChatAdapter), (
            f"Expected ChatAdapter fallback for unknown provider, got {type(adapter).__name__}"
        )
        assert getattr(adapter, "use_native_function_calling", None) is False, (
            "unknown provider should NOT use native function calling (safe fallback)"
        )


# ---------------------------------------------------------------------------
# SIO_FORCE_ADAPTER env override tests
# ---------------------------------------------------------------------------


class TestForceAdapterEnvOverride:
    """SIO_FORCE_ADAPTER and SIO_FORCE_NATIVE_FC env vars override provider detection."""

    def test_force_json_overrides_openai_provider(self, monkeypatch):
        """SIO_FORCE_ADAPTER=json → JSONAdapter even for openai/*."""
        import dspy  # noqa: PLC0415

        monkeypatch.setenv("SIO_FORCE_ADAPTER", "json")
        monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
        get_adapter = _get_adapter()
        lm = _make_lm("openai/gpt-4o")
        adapter = get_adapter(lm)
        assert isinstance(adapter, dspy.JSONAdapter), (
            f"Expected JSONAdapter when SIO_FORCE_ADAPTER=json, got {type(adapter).__name__}"
        )

    def test_force_chat_overrides_ollama_provider(self, monkeypatch):
        """SIO_FORCE_ADAPTER=chat → ChatAdapter even for ollama/*."""
        import dspy  # noqa: PLC0415

        monkeypatch.setenv("SIO_FORCE_ADAPTER", "chat")
        monkeypatch.delenv("SIO_FORCE_NATIVE_FC", raising=False)
        get_adapter = _get_adapter()
        lm = _make_lm("ollama/llama3")
        adapter = get_adapter(lm)
        assert isinstance(adapter, dspy.ChatAdapter), (
            f"Expected ChatAdapter when SIO_FORCE_ADAPTER=chat, got {type(adapter).__name__}"
        )

    def test_force_native_fc_0_disables_native_calling_for_json_adapter(self, monkeypatch):
        """SIO_FORCE_NATIVE_FC=0 with SIO_FORCE_ADAPTER=json → native_fc=False."""
        monkeypatch.setenv("SIO_FORCE_ADAPTER", "json")
        monkeypatch.setenv("SIO_FORCE_NATIVE_FC", "0")
        get_adapter = _get_adapter()
        lm = _make_lm("openai/gpt-4o")
        adapter = get_adapter(lm)
        assert getattr(adapter, "use_native_function_calling", None) is False, (
            "SIO_FORCE_NATIVE_FC=0 should disable native function calling"
        )

    def test_force_native_fc_1_enables_native_calling_for_json_adapter(self, monkeypatch):
        """SIO_FORCE_NATIVE_FC=1 with SIO_FORCE_ADAPTER=json → native_fc=True."""
        monkeypatch.setenv("SIO_FORCE_ADAPTER", "json")
        monkeypatch.setenv("SIO_FORCE_NATIVE_FC", "1")
        get_adapter = _get_adapter()
        lm = _make_lm("openai/gpt-4o")
        adapter = get_adapter(lm)
        assert getattr(adapter, "use_native_function_calling", None) is True, (
            "SIO_FORCE_NATIVE_FC=1 should enable native function calling"
        )
