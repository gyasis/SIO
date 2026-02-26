"""Integration test: full ground truth lifecycle — T040.

Tests the complete cycle:
    generate -> review (approve/reject) -> load corpus -> verify dspy.Example format
"""

from __future__ import annotations

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
def config():
    return SIOConfig(llm_model="test/model")


def _make_dspy_result(**kwargs):
    defaults = {
        "target_surface": "skill_update",
        "rule_title": "Verify paths before reading",
        "prevention_instructions": "Use Glob before Read",
        "rationale": "Prevents FileNotFoundError",
        "quality_assessment": "High quality candidate",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestGroundTruthFullCycle:
    """T040: End-to-end generate -> review -> corpus flow."""

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_generate_approve_load_corpus(
        self, mock_create_lm, mock_module_cls, mem_db, config, tmp_path,
    ):
        """Full cycle: generate candidates, approve some, load as corpus."""
        import json

        import dspy

        # Setup mocks
        mock_create_lm.return_value = MagicMock()
        mock_instance = MagicMock()
        mock_instance.forward.return_value = _make_dspy_result()
        mock_module_cls.return_value = mock_instance

        # Create a dataset file
        ds_file = tmp_path / "dataset.json"
        ds_file.write_text(json.dumps({
            "examples": [{"error_text": "FileNotFoundError", "tool_name": "Read"}]
        }))

        pattern = {
            "id": 1,
            "pattern_id": "fnf-pattern",
            "description": "File not found errors",
            "tool_name": "Read",
            "error_count": 10,
            "session_count": 5,
            "error_type": "tool_failure",
        }
        dataset = {"id": 1, "file_path": str(ds_file)}

        # Step 1: Generate candidates
        from sio.ground_truth.generator import generate_candidates

        ids = generate_candidates(pattern, dataset, mem_db, config, n_candidates=3)
        assert len(ids) == 3

        # Verify all are pending
        for gt_id in ids:
            row = dict(mem_db.execute(
                "SELECT label, source FROM ground_truth WHERE id = ?", (gt_id,)
            ).fetchone())
            assert row["label"] == "pending"
            assert row["source"] == "agent"

        # Step 2: Review — approve first two, reject third
        from sio.ground_truth.reviewer import approve, reject

        assert approve(mem_db, ids[0]) is True
        assert approve(mem_db, ids[1], note="Good candidate") is True
        assert reject(mem_db, ids[2], note="Not relevant") is True

        # Verify labels
        row0 = dict(mem_db.execute(
            "SELECT label, source FROM ground_truth WHERE id = ?", (ids[0],)
        ).fetchone())
        assert row0["label"] == "positive"
        assert row0["source"] == "approved"

        row2 = dict(mem_db.execute(
            "SELECT label, source FROM ground_truth WHERE id = ?", (ids[2],)
        ).fetchone())
        assert row2["label"] == "negative"
        assert row2["source"] == "rejected"

        # Step 3: Load corpus — should only have the 2 approved
        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)
        assert len(corpus) == 2

        # Verify dspy.Example format
        for ex in corpus:
            assert isinstance(ex, dspy.Example)
            assert ex.error_examples is not None
            assert ex.error_type == "tool_failure"
            assert ex.target_surface == "skill_update"
            assert ex.rule_title == "Verify paths before reading"

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_generate_edit_load_corpus(
        self, mock_create_lm, mock_module_cls, mem_db, config, tmp_path,
    ):
        """Generate, edit a candidate, verify edited version in corpus."""
        import json

        mock_create_lm.return_value = MagicMock()
        mock_instance = MagicMock()
        mock_instance.forward.return_value = _make_dspy_result()
        mock_module_cls.return_value = mock_instance

        ds_file = tmp_path / "dataset.json"
        ds_file.write_text(json.dumps({"examples": []}))

        pattern = {
            "id": 1,
            "pattern_id": "edit-test",
            "description": "Test pattern",
            "tool_name": "Read",
            "error_count": 3,
            "session_count": 2,
            "error_type": "tool_failure",
        }
        dataset = {"id": 1, "file_path": str(ds_file)}

        from sio.ground_truth.generator import generate_candidates

        ids = generate_candidates(pattern, dataset, mem_db, config, n_candidates=1)
        assert len(ids) == 1

        # Edit the candidate
        from sio.ground_truth.reviewer import edit

        edit(mem_db, ids[0], {
            "rule_title": "Improved title",
            "prevention_instructions": "Better instructions",
        })

        # Load corpus — should have the edited version
        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)
        assert len(corpus) == 1
        assert corpus[0].rule_title == "Improved title"
        assert corpus[0].prevention_instructions == "Better instructions"

    def test_seed_then_load_corpus(self, mem_db, config):
        """Seed entries should be loadable as training corpus immediately."""
        from sio.ground_truth.corpus import load_training_corpus
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db)
        assert len(ids) == 10

        corpus = load_training_corpus(mem_db)
        assert len(corpus) == 10

        # All should have proper structure
        for ex in corpus:
            assert ex.error_examples is not None
            assert ex.error_type is not None
            assert ex.pattern_summary is not None
            assert ex.target_surface is not None
            assert ex.rule_title is not None
