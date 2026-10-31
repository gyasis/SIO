"""Role LMs ([llm.task] / [llm.sub]) honour the block's api_base_env, and the
classifier tags errors on that configured model, not a hard-coded provider."""
import sys

import pytest

from sio.core.dspy import lm_factory


@pytest.fixture
def sio_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SIO_HOME", str(tmp_path))
    for v in ("SIO_TASK_LM", "SIO_GOV_BASE", "SIO_GOV_KEY"):
        monkeypatch.delenv(v, raising=False)
    (tmp_path / "config.toml").write_text(
        '[llm.sub]\nmodel = "openai/gemma4:12b"\n'
        'api_base_env = "SIO_GOV_BASE"\napi_key_env = "SIO_GOV_KEY"\n')
    return tmp_path


def test_task_lm_gets_governor_base_and_thinking_off(sio_home):
    lm = lm_factory.get_task_lm()
    assert lm.model == "openai/gemma4:12b"
    assert lm.kwargs["api_base"] == lm_factory.GOVERNOR_DEFAULT_BASE
    assert lm.kwargs["extra_body"] == {"reasoning_effort": "none"}


def test_task_lm_prefers_the_env_base_when_set(sio_home, monkeypatch):
    monkeypatch.setenv("SIO_GOV_BASE", "http://example.test/v1")
    lm = lm_factory.get_task_lm()
    assert lm.kwargs["api_base"] == "http://example.test/v1"
    assert "extra_body" not in lm.kwargs


def test_classifier_uses_the_configured_task_lm(sio_home, monkeypatch):
    sys.modules.pop("sio.clustering.classifier", None)
    from sio.clustering import classifier

    monkeypatch.setattr(classifier, "_classify_callable", None)
    import dspy
    seen = {}
    monkeypatch.setattr(dspy, "configure", lambda **kw: seen.update(kw))
    classifier._get_classifier()
    assert seen["lm"].model == "openai/gemma4:12b"
