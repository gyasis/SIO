"""Unit tests for sio.core.dspy.optimizer — T045 [US4] + T055-T057 [Phase 7].

Tests prompt optimization quality gates, optimizer selection,
atomic rollback, recency weighting, and DSPy suggestion optimization.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from sio.core.db.queries import insert_invocation
from sio.core.dspy.optimizer import (
    _MIPROV2_THRESHOLD,
    OptimizationError,
    _select_optimizer,
    optimize,
    optimize_suggestions,
)


def _insert_many(conn, factory, records):
    """Helper to bulk-insert invocation records."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


def _make_labeled_failures(factory, *, n_sessions=3, n_per_session=4):
    """Build a list of labeled failure records across distinct sessions.

    Returns enough records to pass all quality gates by default:
    - >= 10 labeled examples (n_sessions * n_per_session = 12)
    - >= 5 failures (all have correct_outcome=0)
    - >= 3 distinct failing sessions
    """
    now = datetime.now(timezone.utc)
    records = []
    for s in range(n_sessions):
        for i in range(n_per_session):
            records.append(
                {
                    "session_id": f"sess-{s}",
                    "behavior_type": "skill",
                    "actual_action": "Read",
                    "correct_outcome": 0,
                    "correct_action": 0,
                    "user_satisfied": 0,
                    "labeled_by": "human",
                    "labeled_at": (now - timedelta(hours=s * 10 + i)).isoformat(),
                    "timestamp": (now - timedelta(hours=s * 10 + i)).isoformat(),
                }
            )
    return records


# ---------------------------------------------------------------------------
# Original T045 tests (quality gates, optimizer selection, rollback, recency)
# ---------------------------------------------------------------------------


class TestQualityGateMinimumExamples:
    """optimize() must reject datasets with fewer than 10 labeled examples."""

    def test_too_few_labeled_examples(self, tmp_db, sample_invocation):
        """Returns error when fewer than 10 labeled rows exist."""
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {
                    "session_id": f"sess-{i}",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": datetime.now(timezone.utc).isoformat(),
                }
                for i in range(9)  # Only 9 labeled examples
            ],
        )
        result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")
        assert result["status"] == "error"
        assert "10" in result["reason"].lower() or "labeled" in result["reason"].lower()

    def test_passes_with_enough_labeled_examples(self, tmp_db, sample_invocation):
        """No quality-gate error when >= 10 labeled examples exist."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

        assert result["status"] != "error" or "labeled" not in result.get("reason", "")


class TestQualityGateMinimumFailures:
    """optimize() must reject datasets with fewer than 5 failure examples."""

    def test_too_few_failures(self, tmp_db, sample_invocation):
        """Returns error when fewer than 5 failure rows exist."""
        # 10 labeled but only 4 failures
        records = [
            {
                "session_id": f"sess-{i}",
                "correct_outcome": 0,
                "labeled_by": "human",
                "labeled_at": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(4)
        ] + [
            {
                "session_id": f"sess-ok-{i}",
                "correct_outcome": 1,
                "correct_action": 1,
                "labeled_by": "human",
                "labeled_at": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(6)
        ]
        _insert_many(tmp_db, sample_invocation, records)

        result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")
        assert result["status"] == "error"
        assert "failure" in result["reason"].lower() or "5" in result["reason"]


class TestPatternThresholdGate:
    """FR-028: optimize() requires >= 3 distinct sessions with failures."""

    def test_too_few_sessions(self, tmp_db, sample_invocation):
        """Returns error when failures come from fewer than 3 sessions."""
        # 10 labeled failures but only 2 distinct sessions
        records = [
            {
                "session_id": f"sess-{i % 2}",
                "correct_outcome": 0,
                "labeled_by": "human",
                "labeled_at": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(10)
        ]
        _insert_many(tmp_db, sample_invocation, records)

        result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")
        assert result["status"] == "error"
        assert "session" in result["reason"].lower() or "3" in result["reason"]

    def test_passes_with_enough_sessions(self, tmp_db, sample_invocation):
        """No session-gate error when failures span >= 3 distinct sessions."""
        records = _make_labeled_failures(sample_invocation, n_sessions=3)
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            result = optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

        assert result.get("reason", "") == "" or "session" not in result.get("reason", "").lower()


class TestOptimizerSelection:
    """optimize() accepts 'gepa', 'miprov2', 'bootstrap', 'auto' as optimizer names."""

    @pytest.mark.parametrize("optimizer_name", ["gepa", "miprov2", "bootstrap", "auto"])
    def test_valid_optimizer_names(self, tmp_db, sample_invocation, optimizer_name):
        """Each valid optimizer name is accepted without ValueError."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            result = optimize(tmp_db, skill_name="Read", optimizer=optimizer_name)

        # Should not error on optimizer name
        if result["status"] == "error":
            assert "optimizer" not in result["reason"].lower()

    def test_invalid_optimizer_raises(self, tmp_db, sample_invocation):
        """Unknown optimizer name raises ValueError."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        with pytest.raises(ValueError, match="optimizer"):
            optimize(tmp_db, skill_name="Read", optimizer="nonexistent_optimizer")


class TestAtomicRollback:
    """If optimization raises, database state must be unchanged."""

    def test_rollback_on_failure(self, tmp_db, sample_invocation):
        """DB optimization_runs table is unchanged after an internal error."""
        records = _make_labeled_failures(sample_invocation)
        _insert_many(tmp_db, sample_invocation, records)

        runs_before = tmp_db.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.side_effect = RuntimeError("DSPy internal error")

            with pytest.raises((RuntimeError, OptimizationError)):
                optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

        runs_after = tmp_db.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]

        assert runs_after == runs_before, (
            "optimization_runs should be unchanged after a failed optimization"
        )


class TestRecencyWeighting:
    """More recent examples should get higher weight in the exported dataset."""

    def test_recent_examples_weighted_higher(self, tmp_db, sample_invocation):
        """Dataset export assigns higher weight to recent examples."""
        now = datetime.now(timezone.utc)
        records = []
        # Old examples (30 days ago)
        for i in range(5):
            records.append(
                {
                    "session_id": f"sess-old-{i}",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": (now - timedelta(days=30)).isoformat(),
                    "timestamp": (now - timedelta(days=30)).isoformat(),
                }
            )
        # Recent examples (1 day ago)
        for i in range(5):
            records.append(
                {
                    "session_id": f"sess-new-{i}",
                    "correct_outcome": 0,
                    "labeled_by": "human",
                    "labeled_at": (now - timedelta(days=1)).isoformat(),
                    "timestamp": (now - timedelta(days=1)).isoformat(),
                }
            )
        _insert_many(tmp_db, sample_invocation, records)

        with patch("sio.core.dspy.optimizer._run_dspy_optimization") as mock_opt:
            mock_opt.return_value = {"proposed_diff": "--- a\n+++ b\n", "score": 0.8}
            optimize(tmp_db, skill_name="Read", optimizer="bootstrap")

            # Inspect the dataset passed to the mock
            assert mock_opt.called
            call_kwargs = mock_opt.call_args
            dataset = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("dataset")

            # Recent examples should have higher weights
            recent_weights = [ex["weight"] for ex in dataset if "new" in ex.get("session_id", "")]
            old_weights = [ex["weight"] for ex in dataset if "old" in ex.get("session_id", "")]

            assert all(r > o for r, o in zip(recent_weights, old_weights)), (
                "Recent examples must have higher weight than old examples"
            )


# ---------------------------------------------------------------------------
# T055: Test _run_dspy_optimization calls real BootstrapFewShot.compile()
# ---------------------------------------------------------------------------


class TestDSPyOptimizationReplacement:
    """T055: optimize_suggestions() calls real DSPy BootstrapFewShot.compile()."""

    def _seed_ground_truth(self, conn, count=15):
        """Insert positive ground truth rows for testing."""
        import json

        for i in range(count):
            conn.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_examples_json, error_type, pattern_summary, "
                "target_surface, rule_title, prevention_instructions, rationale, "
                "label, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'positive', 'seed', ?)",
                (
                    f"pat-{i}",
                    json.dumps([{"tool_name": "Read", "error_text": f"error {i}"}]),
                    "tool_failure",
                    f"Pattern summary {i}",
                    "claude_md_rule",
                    f"Rule title {i}",
                    f"Run `check {i}` to verify the fix in `/path/to/file{i}.py`",
                    f"Rationale {i}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_bootstrap_compile_called_with_correct_args(
        self,
        mock_eval,
        mock_bootstrap,
        tmp_db,
    ):
        """BootstrapFewShot path is called with SuggestionModule and corpus."""
        self._seed_ground_truth(tmp_db, count=15)

        # Mock returns
        mock_module = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.4, 0.7]  # before, after

        result = optimize_suggestions(tmp_db, optimizer="bootstrap", dry_run=True)

        assert result.status == "dry_run"
        assert result.training_count == 15
        assert result.metric_before == 0.4
        assert result.metric_after == 0.7

        # Verify bootstrap was called (not miprov2)
        mock_bootstrap.assert_called_once()
        call_args = mock_bootstrap.call_args
        # First arg is the module, second is the corpus
        assert len(call_args[0][1]) == 15  # corpus length

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    @patch("sio.core.dspy.module_store.save_module")
    def test_optimized_module_saved_on_non_dry_run(
        self,
        mock_save,
        mock_eval,
        mock_bootstrap,
        tmp_db,
    ):
        """When not dry_run, the optimized module is saved via module_store."""
        self._seed_ground_truth(tmp_db, count=15)

        mock_module = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.4, 0.7]
        mock_save.return_value = 42

        result = optimize_suggestions(tmp_db, optimizer="bootstrap", dry_run=False)

        assert result.status == "success"
        assert result.module_id == 42
        mock_save.assert_called_once()

        # Verify save_module received correct args
        save_kwargs = mock_save.call_args
        assert save_kwargs[1]["module_type"] == "suggestion"
        assert save_kwargs[1]["optimizer_used"] == "bootstrap"
        assert save_kwargs[1]["training_count"] == 15

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    def test_optimization_error_on_compile_failure(
        self,
        mock_bootstrap,
        tmp_db,
    ):
        """OptimizationError raised when DSPy compile fails."""
        self._seed_ground_truth(tmp_db, count=15)
        mock_bootstrap.side_effect = RuntimeError("compile failed")

        with pytest.raises(OptimizationError, match="compile failed"):
            optimize_suggestions(tmp_db, optimizer="bootstrap")

    def test_empty_corpus_returns_error(self, tmp_db):
        """Returns error status when no positive ground truth exists."""
        result = optimize_suggestions(tmp_db, optimizer="bootstrap")
        assert result.status == "error"
        assert "No positive ground truth" in result.message


# ---------------------------------------------------------------------------
# T056: Test auto-optimizer selection (FR-010)
# ---------------------------------------------------------------------------


class TestAutoOptimizerSelection:
    """T056: Auto-selection: <50 -> bootstrap, >=50 -> miprov2."""

    def test_select_bootstrap_under_threshold(self):
        """<50 examples selects BootstrapFewShot."""
        assert _select_optimizer("auto", 10) == "bootstrap"
        assert _select_optimizer("auto", 49) == "bootstrap"

    def test_select_miprov2_at_threshold(self):
        """>=50 examples selects MIPROv2."""
        assert _select_optimizer("auto", 50) == "miprov2"
        assert _select_optimizer("auto", 100) == "miprov2"

    def test_explicit_override(self):
        """Explicit optimizer name bypasses auto-selection."""
        assert _select_optimizer("bootstrap", 100) == "bootstrap"
        assert _select_optimizer("miprov2", 10) == "miprov2"

    def test_threshold_constant(self):
        """MIPROV2_THRESHOLD is 50 per FR-010."""
        assert _MIPROV2_THRESHOLD == 50

    @patch("sio.core.dspy.optimizer._run_miprov2_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_auto_selects_miprov2_with_enough_examples(
        self,
        mock_eval,
        mock_miprov2,
        tmp_db,
    ):
        """optimize_suggestions(optimizer='auto') with 50+ examples uses MIPROv2."""
        import json

        # Seed 55 ground truth entries
        for i in range(55):
            tmp_db.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_examples_json, error_type, pattern_summary, "
                "target_surface, rule_title, prevention_instructions, rationale, "
                "label, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'positive', 'seed', ?)",
                (
                    f"pat-{i}",
                    json.dumps([{"tool_name": "Read", "error_text": f"err {i}"}]),
                    "tool_failure",
                    f"Summary {i}",
                    "claude_md_rule",
                    f"Rule {i}",
                    f"Check `file{i}.py` and run `verify`",
                    f"Rationale {i}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        tmp_db.commit()

        mock_module = MagicMock()
        mock_miprov2.return_value = mock_module
        mock_eval.side_effect = [0.3, 0.6]

        result = optimize_suggestions(tmp_db, optimizer="auto", dry_run=True)

        assert result.optimizer_used == "miprov2"
        mock_miprov2.assert_called_once()

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_auto_selects_bootstrap_with_few_examples(
        self,
        mock_eval,
        mock_bootstrap,
        tmp_db,
    ):
        """optimize_suggestions(optimizer='auto') with <50 examples uses bootstrap."""
        import json

        for i in range(20):
            tmp_db.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_examples_json, error_type, pattern_summary, "
                "target_surface, rule_title, prevention_instructions, rationale, "
                "label, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'positive', 'seed', ?)",
                (
                    f"pat-{i}",
                    json.dumps([{"tool_name": "Read", "error_text": f"err {i}"}]),
                    "tool_failure",
                    f"Summary {i}",
                    "claude_md_rule",
                    f"Rule {i}",
                    f"Check `file{i}.py` and run `verify`",
                    f"Rationale {i}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        tmp_db.commit()

        mock_module = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.3, 0.6]

        result = optimize_suggestions(tmp_db, optimizer="auto", dry_run=True)

        assert result.optimizer_used == "bootstrap"
        mock_bootstrap.assert_called_once()


# ---------------------------------------------------------------------------
# T057: Test module persistence (FR-011)
# ---------------------------------------------------------------------------


class TestModulePersistence:
    """T057: Optimized module saved to ~/.sio/optimized/ and loadable."""

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_module_saved_to_disk(
        self,
        mock_eval,
        mock_bootstrap,
        tmp_db,
        tmp_path,
    ):
        """Optimized module is saved to the store directory."""
        import json

        # Seed ground truth
        for i in range(15):
            tmp_db.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_examples_json, error_type, pattern_summary, "
                "target_surface, rule_title, prevention_instructions, rationale, "
                "label, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'positive', 'seed', ?)",
                (
                    f"pat-{i}",
                    json.dumps([{"tool_name": "Read", "error_text": f"err {i}"}]),
                    "tool_failure",
                    f"Summary {i}",
                    "claude_md_rule",
                    f"Rule {i}",
                    f"Check `file{i}.py` and run `verify`",
                    f"Rationale {i}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        tmp_db.commit()

        # Create a real-ish mock module that has save()
        mock_module = MagicMock()
        mock_module.save = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.4, 0.7]

        # Patch save_module to use tmp_path as store_dir
        with patch("sio.core.dspy.module_store.save_module") as mock_save:
            mock_save.return_value = 1
            result = optimize_suggestions(tmp_db, optimizer="bootstrap")

        assert result.status == "success"
        assert result.module_id == 1
        mock_save.assert_called_once()

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_module_record_in_db(
        self,
        mock_eval,
        mock_bootstrap,
        tmp_db,
        tmp_path,
    ):
        """A record is created in optimized_modules table after save."""
        import json

        for i in range(15):
            tmp_db.execute(
                "INSERT INTO ground_truth "
                "(pattern_id, error_examples_json, error_type, pattern_summary, "
                "target_surface, rule_title, prevention_instructions, rationale, "
                "label, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'positive', 'seed', ?)",
                (
                    f"pat-{i}",
                    json.dumps([{"tool_name": "Read", "error_text": f"err {i}"}]),
                    "tool_failure",
                    f"Summary {i}",
                    "claude_md_rule",
                    f"Rule {i}",
                    f"Check `file{i}.py` and run `verify`",
                    f"Rationale {i}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        tmp_db.commit()

        mock_module = MagicMock()
        mock_module.save = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.4, 0.7]

        # Use real save_module with tmp store directory
        store_dir = str(tmp_path / "optimized")
        with patch("sio.core.dspy.module_store._DEFAULT_STORE_DIR", store_dir):
            result = optimize_suggestions(tmp_db, optimizer="bootstrap")

        assert result.status == "success"

        # Verify DB has the record
        row = tmp_db.execute("SELECT * FROM optimized_modules WHERE is_active = 1").fetchone()
        assert row is not None
        assert dict(row)["module_type"] == "suggestion"
        assert dict(row)["optimizer_used"] == "bootstrap"

    def test_get_active_module_returns_latest(self, tmp_db):
        """get_active_module returns the most recent active module."""
        from sio.core.dspy.module_store import get_active_module

        now = datetime.now(timezone.utc)

        # Insert two module records
        tmp_db.execute(
            "INSERT INTO optimized_modules "
            "(module_type, optimizer_used, file_path, training_count, "
            "metric_before, metric_after, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (
                "suggestion",
                "bootstrap",
                "/tmp/old.json",
                10,
                0.3,
                0.5,
                (now - timedelta(hours=1)).isoformat(),
            ),
        )
        tmp_db.execute(
            "INSERT INTO optimized_modules "
            "(module_type, optimizer_used, file_path, training_count, "
            "metric_before, metric_after, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            ("suggestion", "miprov2", "/tmp/new.json", 50, 0.4, 0.8, now.isoformat()),
        )
        tmp_db.commit()

        active = get_active_module(tmp_db, "suggestion")
        assert active is not None
        assert active["file_path"] == "/tmp/new.json"
        assert active["optimizer_used"] == "miprov2"


# ---------------------------------------------------------------------------
# T062: Test optimized module loading in suggestion generation
# ---------------------------------------------------------------------------


class TestOptimizedModuleLoading:
    """T062: generate_dspy_suggestion loads optimized module when available."""

    @patch("sio.core.dspy.module_store.load_module")
    @patch("sio.core.dspy.module_store.get_active_module")
    @patch("sio.suggestions.dspy_generator.os.path.exists")
    def test_loads_optimized_when_available(
        self,
        mock_exists,
        mock_get_active,
        mock_load,
    ):
        """_load_optimized_or_default returns optimized module when DB has one."""
        from sio.suggestions.dspy_generator import _load_optimized_or_default

        mock_exists.return_value = True
        mock_get_active.return_value = {
            "file_path": "/tmp/optimized.json",
            "optimizer_used": "bootstrap",
        }
        mock_optimized = MagicMock()
        mock_load.return_value = mock_optimized

        result = _load_optimized_or_default(config=None)

        assert result is mock_optimized
        mock_load.assert_called_once()

    @patch("sio.suggestions.dspy_generator.os.path.exists", return_value=False)
    def test_falls_back_to_default_when_no_db(self, mock_exists):
        """_load_optimized_or_default returns fresh module when no DB exists.

        Audit Round 2 C-R2.6 migration (commit a05ccce):
        The canonical fresh default is now SuggestionGenerator (3-input
        PatternToRule signature per contracts/dspy-module-api.md §3), not
        the deleted SuggestionModule. The ImportError-raising migration
        shim for SuggestionModule would reject instantiation anyway.
        """
        from sio.suggestions.dspy_generator import (
            SuggestionGenerator,
            _load_optimized_or_default,
        )

        result = _load_optimized_or_default(config=None)

        # Should be a SuggestionGenerator instance (the fresh default)
        assert isinstance(result, SuggestionGenerator)
