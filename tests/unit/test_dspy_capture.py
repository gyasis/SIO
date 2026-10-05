"""dspy_capture must never turn a successful LM call into a failure.

Regression (2026-10-05): litellm puts a ``CompletionTokensDetailsWrapper`` in
the usage dict; ``json.dumps`` raised TypeError inside the wrapped
``dspy.LM.__call__`` and only ``OSError`` was caught, so every fresh API call
under a runlogged command "failed" — DSPy retried in JSON mode, failed again,
and ``sio suggest --auto`` fell back to template suggestions. Cached responses
(empty usage) survived, which is why the failure looked intermittent.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

dspy = pytest.importorskip("dspy")

from sio.core.runlog import dspy_capture  # noqa: E402


class CompletionTokensDetailsWrapper:
    """Stand-in for the litellm object: not JSON-serializable, no model_dump."""

    def __init__(self) -> None:
        self.reasoning_tokens = 0
        self.audio_tokens = None


@pytest.fixture
def capture_file(tmp_path, monkeypatch):
    path = tmp_path / "run_dspy.jsonl"
    monkeypatch.setattr(dspy_capture, "_ensure_capture_file", lambda: path)
    return path


def test_append_record_survives_non_serializable_usage(capture_file):
    dspy_capture._append_record({
        "ok": True,
        "usage": {"completion_tokens": 5,
                  "completion_tokens_details": CompletionTokensDetailsWrapper()},
    })
    rec = json.loads(capture_file.read_text().strip())
    assert rec["ok"] is True
    assert rec["usage"]["completion_tokens_details"]["reasoning_tokens"] == 0


def test_wrapped_call_returns_result_despite_odd_usage(tmp_path, monkeypatch):
    calls = []

    def fake_orig(self, *args, **kwargs):
        calls.append(1)
        return ["completion text"]

    fake_rl = SimpleNamespace(run_id="r1", stages=[], cmd="suggest",
                              _path=tmp_path / "run.json")
    monkeypatch.setattr(dspy.LM, "__call__", fake_orig)
    monkeypatch.setattr(dspy_capture, "current", lambda: fake_rl)
    monkeypatch.setattr(dspy_capture, "_CAPTURE_FILE", None)
    monkeypatch.setattr(dspy_capture, "_INSTALLED", False)
    dspy_capture.install()
    try:
        lm_self = SimpleNamespace(
            model="openai/gpt-4o-mini",
            history=[{"usage": {"completion_tokens_details":
                                CompletionTokensDetailsWrapper()}}],
        )
        assert dspy.LM.__call__(lm_self, prompt="hi") == ["completion text"]
    finally:
        dspy_capture.uninstall()
    assert calls == [1]
    rec = json.loads((tmp_path / "run_dspy.jsonl").read_text().strip().splitlines()[-1])
    assert rec["ok"] is True
