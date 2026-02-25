"""T057 [US5] Unit tests for drift detector."""

from __future__ import annotations

import pytest

from sio.core.arena.drift_detector import measure_drift, requires_manual_approval


class TestMeasureDrift:
    """measure_drift computes cosine distance between prompts."""

    def test_identical_prompts_zero_drift(self):
        drift = measure_drift("Read the file", "Read the file")
        assert drift == pytest.approx(0.0, abs=0.01)

    def test_different_prompts_nonzero_drift(self):
        drift = measure_drift(
            "Read the file foo.py",
            "Delete all databases and restart",
        )
        assert drift > 0.0

    def test_returns_float(self):
        drift = measure_drift("hello", "world")
        assert isinstance(drift, float)

    def test_drift_between_zero_and_one(self):
        drift = measure_drift("read a file", "write a file")
        assert 0.0 <= drift <= 1.0


class TestRequiresManualApproval:
    """requires_manual_approval checks 40% threshold."""

    def test_below_threshold_auto_passes(self):
        assert requires_manual_approval(0.2) is False

    def test_above_threshold_requires_approval(self):
        assert requires_manual_approval(0.5) is True

    def test_at_threshold_requires_approval(self):
        assert requires_manual_approval(0.40) is True

    def test_custom_threshold(self):
        assert requires_manual_approval(0.3, threshold=0.25) is True
        assert requires_manual_approval(0.1, threshold=0.25) is False
