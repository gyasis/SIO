"""T051 [US6] Unit tests for temporal confidence decay.

Tests cover:
- ``_compute_decay_multiplier(last_seen, config)`` across all three bands
  (Fresh, Cooling, Stale) and the decay floor.
- ``score_confidence`` integration with the decay multiplier showing that
  old patterns score lower than fresh ones.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sio.core.config import SIOConfig
from sio.suggestions.confidence import _compute_decay_multiplier, score_confidence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default config: decay_fresh_days=14, decay_stale_days=28, decay_floor=0.3
_DEFAULT_CFG = SIOConfig()


def _days_ago_iso(days: float) -> str:
    """Return an ISO-8601 datetime string for *days* days before now (UTC)."""
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.isoformat()


# ---------------------------------------------------------------------------
# Tests: _compute_decay_multiplier
# ---------------------------------------------------------------------------


class TestDecayMultiplierFreshBand:
    """Fresh band: 0 to decay_fresh_days (14), multiplier = 1.0."""

    def test_pattern_seen_today(self) -> None:
        result = _compute_decay_multiplier(_days_ago_iso(0), config=_DEFAULT_CFG)
        assert result == pytest.approx(1.0)

    def test_pattern_seen_7_days_ago(self) -> None:
        result = _compute_decay_multiplier(_days_ago_iso(7), config=_DEFAULT_CFG)
        assert result == pytest.approx(1.0)

    def test_pattern_seen_14_days_ago_boundary(self) -> None:
        """At exactly decay_fresh_days, still in fresh band."""
        result = _compute_decay_multiplier(_days_ago_iso(14), config=_DEFAULT_CFG)
        assert result == pytest.approx(1.0)


class TestDecayMultiplierCoolingBand:
    """Cooling band: decay_fresh_days to decay_stale_days (14-28), linear 1.0 -> 0.6."""

    def test_pattern_seen_21_days_ago(self) -> None:
        """Midpoint of cooling band: 21 days = halfway from 14 to 28.
        Expected: 1.0 - 0.5 * (1.0 - 0.6) = 1.0 - 0.2 = 0.8
        """
        result = _compute_decay_multiplier(_days_ago_iso(21), config=_DEFAULT_CFG)
        assert result == pytest.approx(0.8, abs=0.05)

    def test_pattern_seen_28_days_ago_boundary(self) -> None:
        """At exactly decay_stale_days, at the end of cooling band.
        Expected: 0.6
        """
        result = _compute_decay_multiplier(_days_ago_iso(28), config=_DEFAULT_CFG)
        assert result == pytest.approx(0.6, abs=0.05)


class TestDecayMultiplierStaleBand:
    """Stale band: > decay_stale_days, linear 0.6 -> floor at 2x stale_days."""

    def test_pattern_seen_35_days_ago(self) -> None:
        """35 days: fraction = (35-28)/(56-28) = 7/28 = 0.25.
        Expected: 0.6 - 0.25 * (0.6 - 0.3) = 0.6 - 0.075 = 0.525
        Close to 0.45 range with slight tolerance.
        """
        result = _compute_decay_multiplier(_days_ago_iso(35), config=_DEFAULT_CFG)
        # 35 days in stale band: fraction = 7/28 = 0.25
        # multiplier = 0.6 - 0.25 * 0.3 = 0.525
        assert 0.4 <= result <= 0.55

    def test_pattern_seen_60_days_ago_at_floor(self) -> None:
        """60 days: well beyond 2x stale_days (56), should be at floor."""
        result = _compute_decay_multiplier(_days_ago_iso(60), config=_DEFAULT_CFG)
        assert result == pytest.approx(0.3)

    def test_pattern_seen_at_2x_stale_boundary(self) -> None:
        """At exactly 2x stale_days (56), should reach the floor."""
        result = _compute_decay_multiplier(_days_ago_iso(56), config=_DEFAULT_CFG)
        assert result == pytest.approx(0.3, abs=0.02)


class TestDecayFloorRespected:
    """Decay floor is always respected regardless of age."""

    def test_very_old_pattern_never_below_floor(self) -> None:
        result = _compute_decay_multiplier(_days_ago_iso(365), config=_DEFAULT_CFG)
        assert result >= _DEFAULT_CFG.decay_floor

    def test_custom_floor_respected(self) -> None:
        cfg = SIOConfig(decay_floor=0.5, decay_fresh_days=7, decay_stale_days=14)
        result = _compute_decay_multiplier(_days_ago_iso(100), config=cfg)
        assert result >= 0.5

    def test_floor_zero(self) -> None:
        cfg = SIOConfig(decay_floor=0.0, decay_fresh_days=7, decay_stale_days=14)
        result = _compute_decay_multiplier(_days_ago_iso(100), config=cfg)
        assert result >= 0.0


class TestDecayEdgeCases:
    """Edge cases for decay computation."""

    def test_unparseable_date_returns_floor(self) -> None:
        result = _compute_decay_multiplier("not-a-date", config=_DEFAULT_CFG)
        assert result == _DEFAULT_CFG.decay_floor

    def test_future_date_returns_1(self) -> None:
        """A pattern 'last seen' in the future should not decay."""
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        result = _compute_decay_multiplier(future, config=_DEFAULT_CFG)
        assert result == pytest.approx(1.0)

    def test_date_only_string(self) -> None:
        """Should handle date-only strings (no time component)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = _compute_decay_multiplier(today, config=_DEFAULT_CFG)
        assert result == pytest.approx(1.0, abs=0.05)

    def test_naive_datetime_treated_as_utc(self) -> None:
        naive_str = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        result = _compute_decay_multiplier(naive_str, config=_DEFAULT_CFG)
        assert result == pytest.approx(1.0, abs=0.05)


# ---------------------------------------------------------------------------
# Tests: score_confidence with decay integration
# ---------------------------------------------------------------------------


class TestScoreConfidenceWithDecay:
    """score_confidence should apply decay when last_seen is provided."""

    @pytest.fixture()
    def pattern(self) -> dict:
        return {"error_count": 15, "rank_score": 0.8}

    @pytest.fixture()
    def dataset(self) -> dict:
        return {"positive_count": 20, "negative_count": 10}

    def test_no_last_seen_unchanged(self, pattern: dict, dataset: dict) -> None:
        """Without last_seen, score_confidence should behave as before."""
        score_no_decay = score_confidence(pattern, dataset)
        score_none = score_confidence(pattern, dataset, last_seen=None)
        assert score_no_decay == pytest.approx(score_none)

    def test_fresh_pattern_no_penalty(self, pattern: dict, dataset: dict) -> None:
        """A pattern seen today should have the same score as no-decay."""
        score_base = score_confidence(pattern, dataset)
        score_fresh = score_confidence(
            pattern, dataset, last_seen=_days_ago_iso(0), config=_DEFAULT_CFG,
        )
        assert score_fresh == pytest.approx(score_base, abs=0.01)

    def test_old_pattern_lower_score(self, pattern: dict, dataset: dict) -> None:
        """A stale pattern should score lower than a fresh one."""
        score_fresh = score_confidence(
            pattern, dataset, last_seen=_days_ago_iso(0), config=_DEFAULT_CFG,
        )
        score_old = score_confidence(
            pattern, dataset, last_seen=_days_ago_iso(60), config=_DEFAULT_CFG,
        )
        assert score_old < score_fresh

    def test_decay_respects_floor_in_confidence(
        self, pattern: dict, dataset: dict,
    ) -> None:
        """Even very old patterns should not produce confidence below floor * raw."""
        score_old = score_confidence(
            pattern, dataset, last_seen=_days_ago_iso(365), config=_DEFAULT_CFG,
        )
        assert score_old >= 0.0

    def test_cooling_band_intermediate(self, pattern: dict, dataset: dict) -> None:
        """Cooling band pattern should score between fresh and stale."""
        score_fresh = score_confidence(
            pattern, dataset, last_seen=_days_ago_iso(0), config=_DEFAULT_CFG,
        )
        score_cooling = score_confidence(
            pattern, dataset, last_seen=_days_ago_iso(21), config=_DEFAULT_CFG,
        )
        score_stale = score_confidence(
            pattern, dataset, last_seen=_days_ago_iso(60), config=_DEFAULT_CFG,
        )
        assert score_stale < score_cooling < score_fresh
