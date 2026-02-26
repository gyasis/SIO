"""Tests for Phase 12 adversarial audit round 2 fixes (T142-T147)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sio.core.config import SIOConfig
from sio.core.db.schema import init_db


@pytest.fixture
def mem_db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def _insert_pattern(mem_db):
    """Insert a test pattern for FK validation."""
    now = datetime.now(timezone.utc).isoformat()
    mem_db.execute(
        "INSERT INTO patterns "
        "(pattern_id, description, tool_name, error_count, session_count, "
        "first_seen, last_seen, rank_score, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("pat-1", "Test pattern", "Bash", 3, 1, now, now, 0.5, now, now),
    )
    mem_db.commit()


# ---------------------------------------------------------------------------
# T142: Single --candidates flag and PATTERN_ID arg
# ---------------------------------------------------------------------------


class TestCandidatesFlag:
    """T142: --candidates is the only flag (not --n-candidates), default=3."""

    def test_gt_generate_has_candidates_option(self):
        from sio.cli.main import gt_generate

        param_names = [p.name for p in gt_generate.params]
        assert "candidates" in param_names
        # --n-candidates should be removed
        assert "n_candidates" not in param_names

    def test_gt_generate_candidates_default_is_3(self):
        from sio.cli.main import gt_generate

        for p in gt_generate.params:
            if p.name == "candidates":
                assert p.default == 3
                break
        else:
            pytest.fail("--candidates param not found")

    def test_gt_generate_has_pattern_id_argument(self):
        from sio.cli.main import gt_generate

        param_names = [p.name for p in gt_generate.params]
        assert "pattern_id" in param_names


# ---------------------------------------------------------------------------
# T143: Strict FK enforcement
# ---------------------------------------------------------------------------


class TestStrictFKEnforcement:
    """T143: insert_ground_truth raises ValueError on missing pattern_id."""

    def test_strict_raises_on_missing_pattern(self, mem_db):
        from sio.core.db.queries import insert_ground_truth

        with pytest.raises(ValueError, match="no matching patterns row"):
            insert_ground_truth(
                mem_db,
                pattern_id="nonexistent",
                error_examples_json="[]",
                error_type="test",
                pattern_summary="test",
                target_surface="claude_md_rule",
                rule_title="Test",
                prevention_instructions="Test",
                rationale="Test",
                source="seed",
                strict=True,
            )

    def test_non_strict_warns_but_inserts(self, mem_db):
        from sio.core.db.queries import insert_ground_truth

        row_id = insert_ground_truth(
            mem_db,
            pattern_id="nonexistent",
            error_examples_json="[]",
            error_type="test",
            pattern_summary="test",
            target_surface="claude_md_rule",
            rule_title="Test",
            prevention_instructions="Test",
            rationale="Test",
            source="seed",
            strict=False,
        )
        assert row_id > 0

    def test_valid_pattern_inserts_normally(self, mem_db, _insert_pattern):
        from sio.core.db.queries import insert_ground_truth

        row_id = insert_ground_truth(
            mem_db,
            pattern_id="pat-1",
            error_examples_json="[]",
            error_type="test",
            pattern_summary="test",
            target_surface="claude_md_rule",
            rule_title="Test",
            prevention_instructions="Test",
            rationale="Test",
            source="seed",
        )
        assert row_id > 0


# ---------------------------------------------------------------------------
# T144: Deterministic _normalize_surface with difflib
# ---------------------------------------------------------------------------


class TestNormalizeSurfaceDeterministic:
    """T144: _normalize_surface uses difflib for deterministic fuzzy matching."""

    def test_exact_match(self):
        from sio.ground_truth.generator import _normalize_surface

        assert _normalize_surface("claude_md_rule") == "claude_md_rule"
        assert _normalize_surface("hook_config") == "hook_config"

    def test_fuzzy_match(self):
        from sio.ground_truth.generator import _normalize_surface

        # Close misspellings should match
        result = _normalize_surface("claude_md_rules")
        assert result == "claude_md_rule"

    def test_unknown_falls_back_to_default(self):
        from sio.ground_truth.generator import _normalize_surface

        assert _normalize_surface("completely_unknown_xyz") == "claude_md_rule"

    def test_deterministic_across_calls(self):
        """Same input always produces same output."""
        from sio.ground_truth.generator import _normalize_surface

        results = [_normalize_surface("config") for _ in range(10)]
        assert len(set(results)) == 1  # All the same


# ---------------------------------------------------------------------------
# T145: Deep copy in _apply_recency_weighting
# ---------------------------------------------------------------------------


class TestDeepCopyRecencyWeighting:
    """T145: _apply_recency_weighting uses deep copy for nested dicts."""

    def test_nested_dict_not_shared(self):
        from sio.core.dspy.optimizer import _apply_recency_weighting

        originals = [
            {"timestamp": "2025-01-01", "meta": {"key": "value1"}},
            {"timestamp": "2025-01-02", "meta": {"key": "value2"}},
        ]
        result = _apply_recency_weighting(originals)

        # Modify nested dict in result — should NOT affect originals
        result[0]["meta"]["key"] = "MUTATED"
        assert originals[0]["meta"]["key"] == "value1"
        assert originals[1]["meta"]["key"] == "value2"

    def test_nested_list_not_shared(self):
        from sio.core.dspy.optimizer import _apply_recency_weighting

        originals = [
            {"timestamp": "2025-01-01", "tags": ["a", "b"]},
        ]
        result = _apply_recency_weighting(originals)
        result[0]["tags"].append("MUTATED")
        assert "MUTATED" not in originals[0]["tags"]


# ---------------------------------------------------------------------------
# T146: DSPy fallback logging and quality_assessment="FALLBACK"
# ---------------------------------------------------------------------------


class TestDSPyFallbackLogging:
    """T146: Missing DSPy fields trigger warnings and FALLBACK marker."""

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_missing_quality_assessment_gets_fallback_marker(
        self, mock_create_lm, mock_module_cls, mem_db, _insert_pattern,
    ):
        """When DSPy omits quality_assessment, it should be set to FALLBACK string."""

        mock_create_lm.return_value = MagicMock()
        mock_instance = MagicMock()
        # Result missing quality_assessment attribute
        result = SimpleNamespace(
            target_surface="claude_md_rule",
            rule_title="Test rule",
            prevention_instructions="Do the thing",
            rationale="Because reasons",
        )
        mock_instance.forward.return_value = result
        mock_module_cls.return_value = mock_instance

        from sio.ground_truth.generator import generate_candidates

        config = SIOConfig(llm_model="test/model")
        dataset = {"id": 1, "file_path": ""}
        pattern = {
            "id": 1, "pattern_id": "pat-1",
            "description": "Test", "tool_name": "Bash",
            "error_count": 1, "error_type": "tool_failure",
        }

        ids = generate_candidates(pattern, dataset, mem_db, config, n_candidates=1)
        assert len(ids) == 1

        row = mem_db.execute(
            "SELECT quality_assessment FROM ground_truth WHERE id = ?",
            (ids[0],),
        ).fetchone()
        assert "FALLBACK" in dict(row)["quality_assessment"]


# ---------------------------------------------------------------------------
# T147: --surface on ground-truth review
# ---------------------------------------------------------------------------


class TestReviewSurfaceFlag:
    """T147: ground-truth review has --surface filter option."""

    def test_gt_review_has_surface_option(self):
        from sio.cli.main import gt_review

        param_names = [p.name for p in gt_review.params]
        assert "surface" in param_names

    def test_gt_review_surface_default_is_none(self):
        from sio.cli.main import gt_review

        for p in gt_review.params:
            if p.name == "surface":
                assert p.default is None
                break
        else:
            pytest.fail("--surface param not found")
