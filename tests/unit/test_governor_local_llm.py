"""2026-10-15: `sio suggest` uses local gemma4:12b via the Atelier governor,
never paid OpenAI by default. All network is mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sio.core.config import SIOConfig
from sio.core.dspy import lm_factory
from sio.suggestions import refiner

GOV = "http://192.168.0.159:8799/llm/ollama/v1"


def _cfg(**kw) -> SIOConfig:
    d = dict(
        llm_model="openai/gemma4:12b",
        llm_api_key_env="SIO_GOV_KEY",
        llm_api_base_env="SIO_GOV_BASE",
        llm_temperature=0.7,
        llm_max_tokens=16000,
        llm_sub_model="openai/gemma4:12b",
    )
    d.update(kw)
    return SIOConfig(**d)


@pytest.fixture(autouse=True)
def _scrub(monkeypatch):
    for v in ("SIO_GOV_BASE", "SIO_GOV_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "GEMINI_API_KEY", "GOOGLE_API_KEY", "SIO_ALLOW_PAID_REFINER",
              "SIO_REFINER_NO_GOVERNOR"):
        monkeypatch.delenv(v, raising=False)


@pytest.mark.parametrize("fn", ["create_lm", "create_sub_lm"])
def test_create_lm_defaults_to_governor_with_thinking_off(fn):
    with patch("sio.core.dspy.lm_factory.dspy.LM") as lm:
        getattr(lm_factory, fn)(_cfg())
    kw = lm.call_args.kwargs
    assert kw["model"] == "openai/gemma4:12b"
    assert kw["api_base"] == GOV
    assert kw["api_key"]  # dummy, non-empty
    assert kw["extra_body"] == {"reasoning_effort": "none"}


def test_create_lm_env_override_and_non_governor_has_no_extra_body(monkeypatch):
    monkeypatch.setenv("SIO_GOV_BASE", "http://localhost:9999/v1")
    with patch("sio.core.dspy.lm_factory.dspy.LM") as lm:
        lm_factory.create_lm(_cfg())
    kw = lm.call_args.kwargs
    assert kw["api_base"] == "http://localhost:9999/v1"
    assert "extra_body" not in kw


def _fake_openai(calls):
    client = MagicMock()
    def create(**kw):
        calls.append(kw)
        r = MagicMock()
        r.choices = [MagicMock(message=MagicMock(content="ok rule"))]
        return r
    client.chat.completions.create.side_effect = create
    return client


def test_refiner_governor_first_paid_skipped_without_optin(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    calls: list = []
    with patch("openai.OpenAI", return_value=_fake_openai(calls)) as oa, \
         patch.object(refiner, "_try_anthropic") as an, \
         patch.object(refiner, "_try_gemini") as ge, \
         patch.object(refiner, "_try_openai") as op:
        assert refiner.is_refinement_available()
        out = refiner._try_governor("p", "s")
        assert out == "ok rule"
        assert oa.call_args.kwargs["base_url"] == GOV
        assert calls[0]["model"] == "gemma4:12b"
        assert calls[0]["extra_body"] == {"reasoning_effort": "none"}
        # full chain: governor answers -> paid never touched
        refiner.refine_suggestion("Use the right flag", ["err: bad flag --x"], "Bash")
        for m in (an, ge, op):
            m.assert_not_called()


def test_refiner_paid_chain_only_with_optin_after_governor_failure(monkeypatch):
    monkeypatch.setenv("SIO_ALLOW_PAID_REFINER", "1")
    order: list = []
    with patch.object(refiner, "_try_governor", side_effect=lambda *a: order.append("gov")), \
         patch.object(refiner, "_try_anthropic", side_effect=lambda *a: order.append("an")), \
         patch.object(refiner, "_try_gemini", side_effect=lambda *a: order.append("ge")), \
         patch.object(refiner, "_try_openai", side_effect=lambda *a: order.append("op")):
        refiner.refine_suggestion("Use the right flag", ["err: bad flag --x"], "Bash")
    assert order == ["gov", "an", "ge", "op"]


def test_refiner_governor_failure_without_optin_stops(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with patch.object(refiner, "_try_governor", return_value=None), \
         patch.object(refiner, "_try_openai") as op:
        refiner.refine_suggestion("Use the right flag", ["err: bad flag --x"], "Bash")
    op.assert_not_called()


def test_refinement_unavailable_when_governor_disabled_and_no_optin(monkeypatch):
    monkeypatch.setenv("SIO_REFINER_NO_GOVERNOR", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert not refiner.is_refinement_available()
    monkeypatch.setenv("SIO_ALLOW_PAID_REFINER", "1")
    assert refiner.is_refinement_available()
