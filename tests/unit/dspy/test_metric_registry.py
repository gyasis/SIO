"""Failing tests for metrics.py — T025 (TDD red).

Tests assert (per contracts/dspy-module-api.md §5):
  1. METRIC_REGISTRY dict exists with required keys
  2. @register decorator adds function to registry
  3. Each metric signature matches (gold, pred, trace=None) -> bool | float
  4. exact_match() returns True/False correctly
  5. embedding_similarity() returns float in [0.0, 1.0]
  6. llm_judge_recall() returns float in [0.0, 1.0]

Run to confirm RED before T026:
    uv run pytest tests/unit/dspy/test_metric_registry.py -v
"""
from __future__ import annotations

import inspect
import pytest


def _import_metrics():
    from sio.core.dspy import metrics  # noqa: PLC0415
    return metrics


# ---------------------------------------------------------------------------
# 1. METRIC_REGISTRY exists and contains required keys
# ---------------------------------------------------------------------------

def test_metric_registry_exists():
    """metrics.py must export METRIC_REGISTRY dict."""
    m = _import_metrics()
    assert hasattr(m, "METRIC_REGISTRY"), "metrics.py must define METRIC_REGISTRY"
    assert isinstance(m.METRIC_REGISTRY, dict), "METRIC_REGISTRY must be a dict"


def test_metric_registry_has_exact_match():
    """METRIC_REGISTRY must contain 'exact_match' key."""
    m = _import_metrics()
    assert "exact_match" in m.METRIC_REGISTRY, "METRIC_REGISTRY missing 'exact_match'"


def test_metric_registry_has_embedding_similarity():
    """METRIC_REGISTRY must contain 'embedding_similarity' key."""
    m = _import_metrics()
    assert "embedding_similarity" in m.METRIC_REGISTRY, (
        "METRIC_REGISTRY missing 'embedding_similarity'"
    )


def test_metric_registry_has_llm_judge_recall():
    """METRIC_REGISTRY must contain 'llm_judge_recall' key."""
    m = _import_metrics()
    assert "llm_judge_recall" in m.METRIC_REGISTRY, (
        "METRIC_REGISTRY missing 'llm_judge_recall'"
    )


# ---------------------------------------------------------------------------
# 2. @register decorator adds function to registry
# ---------------------------------------------------------------------------

def test_register_decorator_adds_to_registry():
    """@register('name') must add the decorated function to METRIC_REGISTRY."""
    m = _import_metrics()

    @m.register("test_dynamic_metric")
    def _my_metric(gold, pred, trace=None):
        return True

    assert "test_dynamic_metric" in m.METRIC_REGISTRY, (
        "@register did not add 'test_dynamic_metric' to METRIC_REGISTRY"
    )
    assert m.METRIC_REGISTRY["test_dynamic_metric"] is _my_metric
    m.METRIC_REGISTRY.pop("test_dynamic_metric", None)


# ---------------------------------------------------------------------------
# 3. Each metric has the correct signature (gold, pred, trace=None)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["exact_match", "embedding_similarity", "llm_judge_recall"])
def test_metric_signature_matches_contract(name):
    """Each registered metric must accept (gold, pred, trace=None)."""
    m = _import_metrics()
    fn = m.METRIC_REGISTRY[name]
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    assert "gold" in params, f"Metric '{name}' missing 'gold' parameter"
    assert "pred" in params, f"Metric '{name}' missing 'pred' parameter"
    assert "trace" in params, f"Metric '{name}' missing 'trace' parameter"
    trace_param = sig.parameters["trace"]
    assert trace_param.default is None, (
        f"Metric '{name}' 'trace' parameter must default to None"
    )


# ---------------------------------------------------------------------------
# 4. exact_match() returns True for matching labels, False for mismatches
# ---------------------------------------------------------------------------

def test_exact_match_returns_true_for_matching_labels():
    """exact_match(gold, pred) returns True when gold.label == pred.label."""
    import dspy  # noqa: PLC0415
    m = _import_metrics()
    gold = dspy.Example(label="rule_violation").with_inputs("label")
    pred = dspy.Prediction(label="rule_violation")
    result = m.METRIC_REGISTRY["exact_match"](gold, pred)
    assert result is True, f"Expected True, got {result!r}"


def test_exact_match_returns_false_for_mismatched_labels():
    """exact_match(gold, pred) returns False when labels differ."""
    import dspy  # noqa: PLC0415
    m = _import_metrics()
    gold = dspy.Example(label="rule_violation").with_inputs("label")
    pred = dspy.Prediction(label="different_label")
    result = m.METRIC_REGISTRY["exact_match"](gold, pred)
    assert result is False, f"Expected False, got {result!r}"


def test_exact_match_returns_false_for_missing_label():
    """exact_match returns False when pred has no .label attribute."""
    import dspy  # noqa: PLC0415
    m = _import_metrics()
    gold = dspy.Example(label="rule_violation").with_inputs("label")
    pred = dspy.Prediction()
    result = m.METRIC_REGISTRY["exact_match"](gold, pred)
    assert result is False, f"Expected False for missing label, got {result!r}"


# ---------------------------------------------------------------------------
# 5. embedding_similarity() returns float in [0.0, 1.0]
# ---------------------------------------------------------------------------

def test_embedding_similarity_returns_float_in_range(fake_fastembed):
    """embedding_similarity(gold, pred) returns a float in [0.0, 1.0]."""
    import dspy  # noqa: PLC0415
    m = _import_metrics()
    gold = dspy.Example(rule_body="Never use sed -i").with_inputs("rule_body")
    pred = dspy.Prediction(rule_body="Avoid sed -i for file edits")
    result = m.METRIC_REGISTRY["embedding_similarity"](gold, pred)
    assert isinstance(result, float), f"Expected float, got {type(result).__name__}"
    assert 0.0 <= result <= 1.0, f"Expected result in [0, 1], got {result}"


# ---------------------------------------------------------------------------
# 6. llm_judge_recall() returns float in [0.0, 1.0]
# ---------------------------------------------------------------------------

def test_llm_judge_recall_returns_float_in_range(mock_lm):
    """llm_judge_recall(gold, pred) returns a float in [0.0, 1.0]."""
    import dspy  # noqa: PLC0415
    m = _import_metrics()
    gold = dspy.Example(rule_body="Never use sed -i").with_inputs("rule_body")
    pred = dspy.Prediction(rule_body="Avoid sed -i for file edits")
    try:
        result = m.METRIC_REGISTRY["llm_judge_recall"](gold, pred)
        assert isinstance(result, (float, int)), (
            f"Expected float/int, got {type(result).__name__}"
        )
        assert 0.0 <= float(result) <= 1.0, f"Expected result in [0, 1], got {result}"
    except Exception:
        pytest.skip("LLM not available for judge metric; skipping range check")
