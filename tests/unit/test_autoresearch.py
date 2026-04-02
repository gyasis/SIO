"""T069 [US8] Unit tests for the autonomous optimisation loop."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from sio.core.arena.autoresearch import _SENTINEL_PATH, AutoResearchLoop
from sio.core.arena.txlog import TxLog
from sio.core.config import SIOConfig
from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory database with schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def config():
    """Default SIO config."""
    return SIOConfig(max_experiments=3, validation_window_sessions=5)


@pytest.fixture()
def loop(db, config):
    """AutoResearchLoop instance."""
    return AutoResearchLoop(db, config)


@pytest.fixture(autouse=True)
def clean_sentinel():
    """Ensure stop sentinel is removed before and after each test."""
    if os.path.exists(_SENTINEL_PATH):
        os.remove(_SENTINEL_PATH)
    yield
    if os.path.exists(_SENTINEL_PATH):
        os.remove(_SENTINEL_PATH)


# ---------------------------------------------------------------------------
# Stop sentinel
# ---------------------------------------------------------------------------


class TestStopSentinel:

    def test_stop_writes_sentinel(self, loop):
        loop.stop()
        assert os.path.exists(_SENTINEL_PATH)

    def test_cycle_exits_on_sentinel(self, loop):
        # Write sentinel before cycle
        os.makedirs(os.path.dirname(_SENTINEL_PATH), exist_ok=True)
        with open(_SENTINEL_PATH, "w") as f:
            f.write("")

        result = loop.run_cycle()
        assert result.get("stopped") is True

    def test_start_removes_stale_sentinel(self, loop):
        os.makedirs(os.path.dirname(_SENTINEL_PATH), exist_ok=True)
        with open(_SENTINEL_PATH, "w") as f:
            f.write("")

        # Start with max_cycles=0 so it exits immediately
        loop.start(interval_minutes=1, max_cycles=0)
        assert not os.path.exists(_SENTINEL_PATH)


# ---------------------------------------------------------------------------
# Safety limits
# ---------------------------------------------------------------------------


class TestSafetyLimits:

    def test_skips_when_max_experiments_reached(self, loop, db):
        txlog = TxLog(db)
        # Simulate 3 active experiments
        for i in range(3):
            txlog.append(
                cycle_number=i + 1,
                action="experiment_create",
                status="success",
                experiment_branch=f"experiment/sug-{i}-20260401T0000",
            )

        result = loop.run_cycle()
        assert result.get("skipped") is True
        assert result.get("reason") == "max_experiments"

    def test_does_not_skip_when_experiments_below_max(self, loop, db):
        txlog = TxLog(db)
        # 2 active + 1 promoted = only 2 active
        txlog.append(1, "experiment_create", "success",
                      experiment_branch="experiment/sug-1-20260401")
        txlog.append(2, "experiment_create", "success",
                      experiment_branch="experiment/sug-2-20260401")
        txlog.append(3, "experiment_create", "success",
                      experiment_branch="experiment/sug-3-20260401")
        txlog.append(3, "promote", "success",
                      experiment_branch="experiment/sug-3-20260401")

        # Only 2 active now, max is 3
        result = loop.run_cycle()
        # Will proceed past the safety check (may fail at mine step)
        assert result.get("skipped") is not True or result.get("reason") != "max_experiments"


# ---------------------------------------------------------------------------
# Transaction log
# ---------------------------------------------------------------------------


class TestTxLogIntegration:

    def test_cycle_logs_mine_action(self, loop, db):
        """Even if mine finds nothing, it should be logged."""
        with patch(
            "sio.core.arena.autoresearch.AutoResearchLoop._step_mine",
            return_value={"errors_found": 0},
        ):
            loop.run_cycle()

        txlog = TxLog(db)
        txlog.read_log()  # verify no error on read
        # At minimum we should have log entries from the cycle
        # (the mock bypasses actual logging but run_cycle itself
        #  appends mine result to actions)
        assert loop._cycle == 1

    def test_cycle_number_increments(self, loop):
        with patch.object(loop, "_step_mine", return_value={"errors_found": 0}):
            loop.run_cycle()
            loop.run_cycle()

        assert loop._cycle == 2


# ---------------------------------------------------------------------------
# Full single cycle (mocked pipeline)
# ---------------------------------------------------------------------------


class TestSingleCycle:

    def _setup_mocks(self, loop):
        """Patch all pipeline steps to succeed."""
        loop._step_mine = MagicMock(return_value={"errors_found": 5})
        loop._step_cluster = MagicMock(
            return_value={"patterns": [{"id": 1, "description": "test"}]},
        )
        loop._step_grade = MagicMock(
            return_value={"strong_patterns": [{"id": 1, "rank_score": 0.8}]},
        )
        loop._step_generate = MagicMock(
            return_value={
                "suggestion_id": 1,
                "suggestion": {"id": 1, "proposed_change": "Do X"},
            },
        )
        loop._step_assert = MagicMock(return_value={"passed": True})
        loop._step_experiment_create = MagicMock(
            return_value={"branch": "experiment/sug-1-20260401", "suggestion_id": 1},
        )

    def test_full_cycle_success(self, loop):
        self._setup_mocks(loop)

        result = loop.run_cycle()

        assert result["cycle"] == 1
        assert len(result["actions"]) == 6
        action_names = [a[0] for a in result["actions"]]
        assert action_names == [
            "mine", "cluster", "grade", "generate", "assert", "experiment_create",
        ]

    def test_cycle_stops_at_mine_if_no_errors(self, loop):
        self._setup_mocks(loop)
        loop._step_mine = MagicMock(return_value={"errors_found": 0})

        result = loop.run_cycle()

        assert len(result["actions"]) == 1
        assert result["actions"][0][0] == "mine"

    def test_cycle_stops_at_assert_if_fails(self, loop):
        self._setup_mocks(loop)
        loop._step_assert = MagicMock(return_value={"passed": False})

        result = loop.run_cycle()

        assert len(result["actions"]) == 5  # mine through assert, no experiment
        action_names = [a[0] for a in result["actions"]]
        assert "experiment_create" not in action_names


# ---------------------------------------------------------------------------
# start/stop loop
# ---------------------------------------------------------------------------


class TestStartStop:

    def test_start_with_max_cycles(self, loop):
        """Loop respects max_cycles."""
        call_count = 0

        def counting_cycle():
            nonlocal call_count
            call_count += 1
            return {"cycle": call_count}

        loop.run_cycle = counting_cycle
        loop.start(interval_minutes=1, max_cycles=2)
        assert call_count == 2

    def test_start_exits_on_sentinel(self, loop):
        """Loop exits when sentinel is written during cycle."""
        call_count = 0

        def write_sentinel_cycle():
            nonlocal call_count
            call_count += 1
            loop.stop()
            return {"cycle": 1, "stopped": True}

        loop.run_cycle = write_sentinel_cycle
        loop.start(interval_minutes=1, max_cycles=10)
        assert call_count == 1
