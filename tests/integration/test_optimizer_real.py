"""Integration test for DSPy optimizer — T058 [Phase 7].

Tests the full cycle with mock LLM:
  create ground truth -> optimize -> verify optimized module loaded on next suggest call.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sio.core.db.schema import init_db
from sio.core.dspy.optimizer import optimize_suggestions


@pytest.fixture
def integration_db():
    """In-memory SQLite with full SIO schema."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _seed_corpus(conn, count=20):
    """Insert positive ground truth entries."""
    for i in range(count):
        conn.execute(
            "INSERT INTO ground_truth "
            "(pattern_id, error_examples_json, error_type, pattern_summary, "
            "target_surface, rule_title, prevention_instructions, rationale, "
            "label, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'positive', 'seed', ?)",
            (
                f"pat-{i}",
                json.dumps(
                    [
                        {
                            "tool_name": "Read",
                            "error_text": f"FileNotFoundError: /tmp/test{i}.py",
                        }
                    ]
                ),
                "tool_failure",
                f"Read tool fails on missing file test{i}.py",
                "claude_md_rule",
                f"Verify file existence before Read ({i})",
                f"Run `test -f /tmp/test{i}.py` before calling Read. "
                f"Check `/path/to/config{i}.json` for valid paths.",
                f"Tool repeatedly fails because file does not exist ({i})",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    conn.commit()


class TestFullOptimizationCycle:
    """T058: Full cycle test — ground truth -> optimize -> load on suggest."""

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_optimize_and_save_cycle(
        self,
        mock_eval,
        mock_bootstrap,
        integration_db,
        tmp_path,
    ):
        """Creates corpus, runs optimization, verifies module saved in DB."""
        _seed_corpus(integration_db, count=20)

        # Mock the DSPy compilation
        mock_module = MagicMock()
        mock_module.save = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.35, 0.72]

        # Patch store_dir to use tmp_path
        store_dir = str(tmp_path / "optimized")
        with patch("sio.core.dspy.module_store._DEFAULT_STORE_DIR", store_dir):
            result = optimize_suggestions(
                integration_db,
                optimizer="bootstrap",
                dry_run=False,
            )

        assert result.status == "success"
        assert result.training_count == 20
        assert result.metric_before == 0.35
        assert result.metric_after == 0.72
        assert result.module_id is not None

        # Verify DB has the record
        from sio.core.dspy.module_store import get_active_module

        active = get_active_module(integration_db, "suggestion")
        assert active is not None
        assert active["optimizer_used"] == "bootstrap"
        assert active["training_count"] == 20
        assert active["is_active"] == 1

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_dry_run_does_not_save(
        self,
        mock_eval,
        mock_bootstrap,
        integration_db,
    ):
        """Dry run evaluates metrics but does not persist the module."""
        _seed_corpus(integration_db, count=15)

        mock_module = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.3, 0.6]

        result = optimize_suggestions(
            integration_db,
            optimizer="bootstrap",
            dry_run=True,
        )

        assert result.status == "dry_run"
        assert result.module_id is None

        # No module in DB
        from sio.core.dspy.module_store import get_active_module

        active = get_active_module(integration_db, "suggestion")
        assert active is None

    @patch("sio.core.dspy.optimizer._run_miprov2_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_auto_selects_miprov2_for_large_corpus(
        self,
        mock_eval,
        mock_miprov2,
        integration_db,
        tmp_path,
    ):
        """Auto-optimizer selects MIPROv2 when corpus has 50+ examples."""
        _seed_corpus(integration_db, count=55)

        mock_module = MagicMock()
        mock_module.save = MagicMock()
        mock_miprov2.return_value = mock_module
        mock_eval.side_effect = [0.3, 0.65]

        store_dir = str(tmp_path / "optimized")
        with patch("sio.core.dspy.module_store._DEFAULT_STORE_DIR", store_dir):
            result = optimize_suggestions(
                integration_db,
                optimizer="auto",
                dry_run=False,
            )

        assert result.optimizer_used == "miprov2"
        assert result.status == "success"
        mock_miprov2.assert_called_once()

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_optimized_module_loaded_on_suggest(
        self,
        mock_eval,
        mock_bootstrap,
        integration_db,
        tmp_path,
    ):
        """After optimization, _load_optimized_or_default returns the saved module."""
        _seed_corpus(integration_db, count=20)

        mock_module = MagicMock()
        mock_module.save = MagicMock()
        mock_bootstrap.return_value = mock_module
        mock_eval.side_effect = [0.35, 0.72]

        store_dir = str(tmp_path / "optimized")
        with patch("sio.core.dspy.module_store._DEFAULT_STORE_DIR", store_dir):
            result = optimize_suggestions(
                integration_db,
                optimizer="bootstrap",
                dry_run=False,
            )

        assert result.status == "success"

        # Now verify the module loader finds it
        from sio.core.dspy.module_store import get_active_module

        active = get_active_module(integration_db, "suggestion")
        assert active is not None
        assert active["is_active"] == 1

        # Simulate what _load_optimized_or_default does.
        # Audit Round 2 C-R2.6: canonical class is SuggestionGenerator
        # (PatternToRule signature), not the deleted SuggestionModule.
        from sio.core.dspy.module_store import load_module
        from sio.suggestions.dspy_generator import SuggestionGenerator

        with patch.object(SuggestionGenerator, "load") as mock_load:
            loaded = load_module(SuggestionGenerator, active["file_path"])

        # The module was instantiated and load() was called with the file path
        mock_load.assert_called_once_with(active["file_path"])
        assert isinstance(loaded, SuggestionGenerator)

    def test_empty_corpus_returns_error(self, integration_db):
        """Returns error when no positive ground truth exists."""
        result = optimize_suggestions(
            integration_db,
            optimizer="bootstrap",
        )

        assert result.status == "error"
        assert result.training_count == 0
        assert "No positive ground truth" in result.message

    @patch("sio.core.dspy.optimizer._run_bootstrap_optimization")
    @patch("sio.core.dspy.optimizer._evaluate_metric")
    def test_successive_optimizations_deactivate_previous(
        self,
        mock_eval,
        mock_bootstrap,
        integration_db,
        tmp_path,
    ):
        """Running optimization twice deactivates the first module."""
        _seed_corpus(integration_db, count=20)

        mock_module = MagicMock()
        mock_module.save = MagicMock()
        mock_bootstrap.return_value = mock_module

        store_dir = str(tmp_path / "optimized")
        with patch("sio.core.dspy.module_store._DEFAULT_STORE_DIR", store_dir):
            # First optimization
            mock_eval.side_effect = [0.3, 0.6]
            result1 = optimize_suggestions(integration_db, optimizer="bootstrap")
            assert result1.status == "success"

            # Second optimization
            mock_eval.side_effect = [0.5, 0.8]
            result2 = optimize_suggestions(integration_db, optimizer="bootstrap")
            assert result2.status == "success"

        # Only the second module should be active
        active_rows = integration_db.execute(
            "SELECT * FROM optimized_modules WHERE is_active = 1"
        ).fetchall()
        assert len(active_rows) == 1
        assert dict(active_rows[0])["id"] == result2.module_id

        # First should be deactivated
        first_row = integration_db.execute(
            "SELECT is_active FROM optimized_modules WHERE id = ?",
            (result1.module_id,),
        ).fetchone()
        assert dict(first_row)["is_active"] == 0
