"""T067 [US8] Unit tests for binary assertion checks."""

from __future__ import annotations

import os
import tempfile

import pytest

from sio.core.arena.assertions import (
    AssertionResult,
    budget_within_limits,
    confidence_above_threshold,
    error_rate_decreased,
    no_collisions,
    no_new_regressions,
    run_assertions,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pre_metrics():
    """Baseline metrics before experiment."""
    return {
        "error_rate": 0.25,
        "error_types": ["tool_failure", "user_correction", "repeated_attempt"],
    }


@pytest.fixture()
def post_metrics_improved():
    """Post-experiment metrics — improved, no new types."""
    return {
        "error_rate": 0.10,
        "error_types": ["tool_failure", "user_correction"],
    }


@pytest.fixture()
def post_metrics_regressed():
    """Post-experiment metrics — worse, with new error types."""
    return {
        "error_rate": 0.30,
        "error_types": [
            "tool_failure",
            "user_correction",
            "repeated_attempt",
            "new_unknown_error",
        ],
    }


class _FakeConfig:
    budget_cap_primary = 100


@pytest.fixture()
def fake_config():
    return _FakeConfig()


# ---------------------------------------------------------------------------
# error_rate_decreased
# ---------------------------------------------------------------------------


class TestErrorRateDecreased:
    def test_passes_when_rate_drops(self, pre_metrics, post_metrics_improved):
        r = error_rate_decreased(pre_metrics, post_metrics_improved)
        assert r.passed is True
        assert r.name == "error_rate_decreased"
        assert r.actual_value == pytest.approx(0.10)

    def test_fails_when_rate_increases(self, pre_metrics, post_metrics_regressed):
        r = error_rate_decreased(pre_metrics, post_metrics_regressed)
        assert r.passed is False
        assert r.actual_value == pytest.approx(0.30)

    def test_passes_when_rate_unchanged(self, pre_metrics):
        r = error_rate_decreased(pre_metrics, pre_metrics)
        assert r.passed is True

    def test_returns_assertion_result(self, pre_metrics, post_metrics_improved):
        r = error_rate_decreased(pre_metrics, post_metrics_improved)
        assert isinstance(r, AssertionResult)


# ---------------------------------------------------------------------------
# no_new_regressions
# ---------------------------------------------------------------------------


class TestNoNewRegressions:
    def test_passes_when_no_new_types(self, pre_metrics, post_metrics_improved):
        r = no_new_regressions(pre_metrics, post_metrics_improved)
        assert r.passed is True
        assert r.actual_value == 0.0

    def test_fails_when_new_type_appears(self, pre_metrics, post_metrics_regressed):
        r = no_new_regressions(pre_metrics, post_metrics_regressed)
        assert r.passed is False
        assert r.actual_value == 1.0  # 1 new type

    def test_passes_with_identical_types(self, pre_metrics):
        r = no_new_regressions(pre_metrics, pre_metrics)
        assert r.passed is True

    def test_handles_empty_pre(self, post_metrics_improved):
        r = no_new_regressions({}, post_metrics_improved)
        # All post types are "new" relative to empty
        assert r.passed is False


# ---------------------------------------------------------------------------
# confidence_above_threshold
# ---------------------------------------------------------------------------


class TestConfidenceAboveThreshold:
    def test_passes_when_above(self):
        r = confidence_above_threshold({"confidence": 0.85}, threshold=0.7)
        assert r.passed is True

    def test_fails_when_below(self):
        r = confidence_above_threshold({"confidence": 0.5}, threshold=0.7)
        assert r.passed is False

    def test_at_threshold_passes(self):
        r = confidence_above_threshold({"confidence": 0.7}, threshold=0.7)
        assert r.passed is True

    def test_uses_rank_score_fallback(self):
        r = confidence_above_threshold({"rank_score": 0.9}, threshold=0.7)
        assert r.passed is True

    def test_zero_confidence_fails(self):
        r = confidence_above_threshold({}, threshold=0.7)
        assert r.passed is False


# ---------------------------------------------------------------------------
# budget_within_limits
# ---------------------------------------------------------------------------


class TestBudgetWithinLimits:
    def test_passes_when_under_budget(self, fake_config):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("\n".join(["line"] * 50))
            f.flush()
            path = f.name
        try:
            r = budget_within_limits(path, fake_config)
            assert r.passed is True
            assert r.actual_value == 50.0
        finally:
            os.unlink(path)

    def test_fails_when_over_budget(self, fake_config):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("\n".join(["line"] * 150))
            f.flush()
            path = f.name
        try:
            r = budget_within_limits(path, fake_config)
            assert r.passed is False
            assert r.actual_value == 150.0
        finally:
            os.unlink(path)

    def test_nonexistent_file_passes(self, fake_config):
        r = budget_within_limits("/nonexistent/path.md", fake_config)
        assert r.passed is True
        assert r.actual_value == 0.0


# ---------------------------------------------------------------------------
# no_collisions
# ---------------------------------------------------------------------------


class TestNoCollisions:
    def test_passes_when_unique(self):
        sug = {"proposed_change": "Always use explicit column lists"}
        existing = [{"proposed_change": "Run ruff check after editing"}]
        r = no_collisions(sug, existing)
        assert r.passed is True

    def test_fails_when_duplicate(self):
        text = "Never use SELECT * in SQL queries"
        sug = {"proposed_change": text}
        existing = [{"proposed_change": text}]
        r = no_collisions(sug, existing)
        assert r.passed is False

    def test_passes_with_empty_existing(self):
        sug = {"proposed_change": "Some new rule"}
        r = no_collisions(sug, [])
        assert r.passed is True

    def test_custom_threshold(self):
        sug = {"proposed_change": "Use absolute paths always"}
        existing = [{"proposed_change": "Use absolute paths in all commands"}]
        r = no_collisions(sug, existing, threshold=0.50)
        # These are similar but whether they pass depends on ratio
        assert isinstance(r, AssertionResult)


# ---------------------------------------------------------------------------
# run_assertions (orchestrator)
# ---------------------------------------------------------------------------


class TestRunAssertions:
    def test_runs_multiple_assertions(
        self,
        pre_metrics,
        post_metrics_improved,
        fake_config,
    ):
        context = {
            "pre": pre_metrics,
            "post": post_metrics_improved,
        }
        results = run_assertions(
            ["error_rate_decreased", "no_new_regressions"],
            context,
        )
        assert len(results) == 2
        assert all(isinstance(r, AssertionResult) for r in results)
        assert all(r.passed for r in results)

    def test_unknown_assertion_fails(self):
        results = run_assertions(["nonexistent_check"], {})
        assert len(results) == 1
        assert results[0].passed is False
        assert "Unknown assertion" in results[0].detail

    def test_custom_assertion_callable(self):
        def my_check(ctx):
            return AssertionResult(
                passed=True,
                name="my_check",
                actual_value=1.0,
                threshold=0.0,
            )

        results = run_assertions(["my_check"], {"my_check": my_check})
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].name == "my_check"

    def test_empty_names_returns_empty(self):
        results = run_assertions([], {})
        assert results == []
