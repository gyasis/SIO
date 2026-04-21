"""Tests for sio.ground_truth.generator — T036."""

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
def fake_config():
    return SIOConfig(llm_model="test/model")


@pytest.fixture
def sample_pattern(mem_db):
    """Create a sample pattern and insert it into the DB for FK validation."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mem_db.execute(
        "INSERT INTO patterns "
        "(pattern_id, description, tool_name, error_count, session_count, "
        "first_seen, last_seen, rank_score, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "test-pattern-001",
            "Bash tool times out repeatedly",
            "Bash",
            5,
            3,
            now,
            now,
            0.5,
            now,
            now,
        ),
    )
    mem_db.commit()
    return {
        "id": 1,
        "pattern_id": "test-pattern-001",
        "description": "Bash tool times out repeatedly",
        "tool_name": "Bash",
        "error_count": 5,
        "session_count": 3,
        "error_type": "tool_failure",
    }


@pytest.fixture
def sample_dataset(tmp_path):
    import json

    ds_file = tmp_path / "dataset.json"
    ds_file.write_text(
        json.dumps(
            {
                "examples": [
                    {"error_text": "TimeoutError", "tool_name": "Bash"},
                    {"error_text": "TimeoutError", "tool_name": "Bash"},
                ]
            }
        )
    )
    return {"id": 1, "file_path": str(ds_file)}


def _make_dspy_result(**kwargs):
    """Create a mock DSPy result with the given output fields."""
    defaults = {
        "target_surface": "claude_md_rule",
        "rule_title": "Fix timeout issue",
        "prevention_instructions": "Use timeout parameter",
        "rationale": "Prevents repeated timeouts",
        "quality_assessment": "Good quality",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestGenerateCandidates:
    """T036: generate_candidates calls GroundTruthModule N times."""

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_generates_n_candidates(
        self,
        mock_create_lm,
        mock_module_cls,
        mem_db,
        fake_config,
        sample_pattern,
        sample_dataset,
    ):
        """Should call forward() N times and insert N rows."""
        mock_lm = MagicMock()
        mock_create_lm.return_value = mock_lm

        mock_instance = MagicMock()
        mock_instance.forward.return_value = _make_dspy_result()
        mock_module_cls.return_value = mock_instance

        from sio.ground_truth.generator import generate_candidates

        ids = generate_candidates(
            sample_pattern, sample_dataset, mem_db, fake_config, n_candidates=3
        )

        assert len(ids) == 3
        assert mock_instance.forward.call_count == 3

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_candidates_stored_as_pending_agent(
        self,
        mock_create_lm,
        mock_module_cls,
        mem_db,
        fake_config,
        sample_pattern,
        sample_dataset,
    ):
        """Each candidate should have label='pending' and source='agent'."""
        mock_create_lm.return_value = MagicMock()
        mock_instance = MagicMock()
        mock_instance.forward.return_value = _make_dspy_result()
        mock_module_cls.return_value = mock_instance

        from sio.ground_truth.generator import generate_candidates

        ids = generate_candidates(
            sample_pattern, sample_dataset, mem_db, fake_config, n_candidates=2
        )

        for row_id in ids:
            row = mem_db.execute(
                "SELECT label, source FROM ground_truth WHERE id = ?", (row_id,)
            ).fetchone()
            assert row is not None
            assert dict(row)["label"] == "pending"
            assert dict(row)["source"] == "agent"

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_returns_row_ids(
        self,
        mock_create_lm,
        mock_module_cls,
        mem_db,
        fake_config,
        sample_pattern,
        sample_dataset,
    ):
        """Returned IDs should be valid ground_truth row IDs."""
        mock_create_lm.return_value = MagicMock()
        mock_instance = MagicMock()
        mock_instance.forward.return_value = _make_dspy_result()
        mock_module_cls.return_value = mock_instance

        from sio.ground_truth.generator import generate_candidates

        ids = generate_candidates(
            sample_pattern, sample_dataset, mem_db, fake_config, n_candidates=2
        )

        for row_id in ids:
            row = mem_db.execute("SELECT id FROM ground_truth WHERE id = ?", (row_id,)).fetchone()
            assert row is not None

    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_no_lm_returns_empty(
        self,
        mock_create_lm,
        mem_db,
        fake_config,
        sample_pattern,
        sample_dataset,
    ):
        """When no LLM is available, should return empty list."""
        mock_create_lm.return_value = None

        from sio.ground_truth.generator import generate_candidates

        ids = generate_candidates(
            sample_pattern, sample_dataset, mem_db, fake_config, n_candidates=3
        )

        assert ids == []

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_partial_failure_still_returns_successes(
        self,
        mock_create_lm,
        mock_module_cls,
        mem_db,
        fake_config,
        sample_pattern,
        sample_dataset,
    ):
        """If one forward() call raises, other candidates still succeed."""
        mock_create_lm.return_value = MagicMock()
        mock_instance = MagicMock()
        mock_instance.forward.side_effect = [
            _make_dspy_result(),
            RuntimeError("LLM failed"),
            _make_dspy_result(),
        ]
        mock_module_cls.return_value = mock_instance

        from sio.ground_truth.generator import generate_candidates

        ids = generate_candidates(
            sample_pattern, sample_dataset, mem_db, fake_config, n_candidates=3
        )

        assert len(ids) == 2
