"""Tests for Phase 11 minor fixes T101-T114.

Covers:
- T101: FK validation (foreign_keys=ON)
- T102: --candidates flag on generate
- T103: --count flag on seed
- T104: --surface filter on seed
- T105: quality_assessment persistence
- T106: Seeded pattern_ids reference real patterns
- T107: _compute_satisfaction_rate returns None
- T108: _apply_recency_weighting no mutation
- T109: row_factory validation
- T110: Unrecognized TOML keys warning
- T111: query_emb shape safety
- T112: Similarity threshold filtering
- T113: Reduce per-operation commits (_batch flag)
- T114: LLM disabled diagnostic logging
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sio.core.config import SIOConfig, load_config
from sio.core.db.schema import init_db


@pytest.fixture
def mem_db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def config():
    return SIOConfig()


# ---------------------------------------------------------------------------
# T101: FK validation
# ---------------------------------------------------------------------------


class TestForeignKeyValidation:
    """T101: Application-level FK validation for ground_truth.pattern_id."""

    def test_raises_on_missing_pattern_strict(self, mem_db):
        """insert_ground_truth raises ValueError when pattern_id is missing (strict=True)."""
        from sio.core.db.queries import insert_ground_truth

        with pytest.raises(ValueError, match="nonexistent-pattern"):
            insert_ground_truth(
                mem_db,
                pattern_id="nonexistent-pattern",
                error_examples_json="[]",
                error_type="tool_failure",
                pattern_summary="Test",
                target_surface="claude_md_rule",
                rule_title="Test",
                prevention_instructions="Do something",
                rationale="Because",
            )

    def test_warns_on_missing_pattern_non_strict(self, mem_db, caplog):
        """insert_ground_truth warns when pattern_id is missing and strict=False."""
        from sio.core.db.queries import insert_ground_truth

        with caplog.at_level(logging.WARNING, logger="sio.core.db.queries"):
            insert_ground_truth(
                mem_db,
                pattern_id="nonexistent-pattern",
                error_examples_json="[]",
                error_type="tool_failure",
                pattern_summary="Test",
                target_surface="claude_md_rule",
                rule_title="Test",
                prevention_instructions="Do something",
                rationale="Because",
                strict=False,
            )

        assert "nonexistent-pattern" in caplog.text
        assert "no matching patterns row" in caplog.text

    def test_no_warning_when_pattern_exists(self, mem_db, caplog):
        """No warning when pattern_id references a valid pattern."""
        from datetime import datetime, timezone

        from sio.core.db.queries import insert_ground_truth

        now = datetime.now(timezone.utc).isoformat()
        mem_db.execute(
            "INSERT INTO patterns "
            "(pattern_id, description, tool_name, error_count, session_count, "
            "first_seen, last_seen, rank_score, created_at, updated_at) "
            "VALUES (?, ?, NULL, 1, 1, ?, ?, 1.0, ?, ?)",
            ("valid-pattern", "Test pattern", now, now, now, now),
        )
        mem_db.commit()

        with caplog.at_level(logging.WARNING, logger="sio.core.db.queries"):
            insert_ground_truth(
                mem_db,
                pattern_id="valid-pattern",
                error_examples_json="[]",
                error_type="tool_failure",
                pattern_summary="Test",
                target_surface="claude_md_rule",
                rule_title="Test",
                prevention_instructions="Do something",
                rationale="Because",
            )

        assert "no matching patterns row" not in caplog.text


# ---------------------------------------------------------------------------
# T103 + T104: --count and --surface flags on seed
# ---------------------------------------------------------------------------


class TestSeedCountAndSurface:
    """T103/T104: seed_ground_truth accepts count and surface."""

    def test_count_limits_entries(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db, count=3)
        assert len(ids) == 3

    def test_default_count_is_10(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db)
        assert len(ids) == 10

    def test_surface_filter(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db, surface="claude_md_rule")
        assert len(ids) >= 1

        rows = mem_db.execute(
            "SELECT DISTINCT target_surface FROM ground_truth"
        ).fetchall()
        surfaces = {dict(r)["target_surface"] for r in rows}
        assert surfaces == {"claude_md_rule"}

    def test_surface_filter_with_count(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db, surface="claude_md_rule", count=1)
        assert len(ids) == 1

    def test_unknown_surface_returns_empty(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        ids = seed_ground_truth(config, mem_db, surface="nonexistent_surface")
        assert len(ids) == 0


# ---------------------------------------------------------------------------
# T105: quality_assessment persistence
# ---------------------------------------------------------------------------


class TestQualityAssessmentPersistence:
    """T105: quality_assessment from DSPy is stored in ground_truth."""

    def test_quality_assessment_column_exists(self, mem_db):
        """The quality_assessment column should exist after init_db."""
        # Column may be added via ALTER TABLE migration
        info = mem_db.execute("PRAGMA table_info(ground_truth)").fetchall()
        col_names = {dict(r)["name"] for r in info}
        assert "quality_assessment" in col_names

    def test_insert_with_quality_assessment(self, mem_db):
        from sio.core.db.queries import insert_ground_truth

        row_id = insert_ground_truth(
            mem_db,
            pattern_id="test-pat",
            error_examples_json="[]",
            error_type="tool_failure",
            pattern_summary="Test pattern",
            target_surface="claude_md_rule",
            rule_title="Test rule",
            prevention_instructions="Do something",
            rationale="Because",
            quality_assessment="High quality candidate",
            strict=False,
        )
        row = mem_db.execute(
            "SELECT quality_assessment FROM ground_truth WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert dict(row)["quality_assessment"] == "High quality candidate"

    def test_insert_without_quality_assessment(self, mem_db):
        from sio.core.db.queries import insert_ground_truth

        row_id = insert_ground_truth(
            mem_db,
            pattern_id="test-pat-2",
            error_examples_json="[]",
            error_type="tool_failure",
            pattern_summary="Test pattern",
            target_surface="claude_md_rule",
            rule_title="Test rule",
            prevention_instructions="Do something",
            rationale="Because",
            strict=False,
        )
        row = mem_db.execute(
            "SELECT quality_assessment FROM ground_truth WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert dict(row)["quality_assessment"] is None

    @patch("sio.core.dspy.modules.GroundTruthModule")
    @patch("sio.core.dspy.lm_factory.create_lm")
    def test_generator_persists_quality_assessment(
        self, mock_create_lm, mock_module_cls, mem_db,
    ):
        """generate_candidates should store quality_assessment from DSPy."""
        mock_create_lm.return_value = MagicMock()
        mock_instance = MagicMock()
        mock_instance.forward.return_value = SimpleNamespace(
            target_surface="claude_md_rule",
            rule_title="Fix it",
            prevention_instructions="Do this",
            rationale="Why",
            quality_assessment="Very thorough candidate",
        )
        mock_module_cls.return_value = mock_instance

        from sio.ground_truth.generator import generate_candidates

        # Create pattern in DB so FK validation passes
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        mem_db.execute(
            "INSERT INTO patterns "
            "(pattern_id, description, tool_name, error_count, session_count, "
            "first_seen, last_seen, rank_score, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("gen-qa-test", "Test", "Bash", 5, 3, now, now, 1.0, now, now),
        )
        mem_db.commit()

        pattern = {
            "id": 1, "pattern_id": "gen-qa-test",
            "description": "Test", "tool_name": "Bash",
            "error_count": 5, "session_count": 3, "error_type": "tool_failure",
        }
        dataset = {"id": 0, "file_path": ""}
        config = SIOConfig(llm_model="test/model")

        ids = generate_candidates(pattern, dataset, mem_db, config, n_candidates=1)
        assert len(ids) == 1

        row = mem_db.execute(
            "SELECT quality_assessment FROM ground_truth WHERE id = ?",
            (ids[0],),
        ).fetchone()
        assert dict(row)["quality_assessment"] == "Very thorough candidate"


# ---------------------------------------------------------------------------
# T106: Seeded pattern_ids reference real patterns
# ---------------------------------------------------------------------------


class TestSeededPatternIds:
    """T106: Seed entries create stub patterns when needed."""

    def test_seed_creates_stub_patterns(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        seed_ground_truth(config, mem_db)

        # Every seeded ground_truth.pattern_id should have a matching patterns row
        gt_rows = mem_db.execute(
            "SELECT DISTINCT pattern_id FROM ground_truth"
        ).fetchall()
        for row in gt_rows:
            pid = dict(row)["pattern_id"]
            pat_row = mem_db.execute(
                "SELECT id FROM patterns WHERE pattern_id = ?", (pid,)
            ).fetchone()
            assert pat_row is not None, f"No patterns row for pattern_id={pid}"

    def test_does_not_duplicate_patterns(self, config, mem_db):
        from sio.ground_truth.seeder import seed_ground_truth

        seed_ground_truth(config, mem_db)
        # Seed again -- should not create duplicate patterns
        seed_ground_truth(config, mem_db)

        count = mem_db.execute(
            "SELECT COUNT(*) FROM patterns WHERE pattern_id LIKE 'seed-%'"
        ).fetchone()[0]
        # Should have exactly 10 unique seed pattern_ids
        assert count == 10


# ---------------------------------------------------------------------------
# T107: _compute_satisfaction_rate returns None
# ---------------------------------------------------------------------------


class TestComputeSatisfactionRate:
    """T107: Returns None when no labeled data."""

    def test_returns_none_for_empty_list(self):
        from sio.core.dspy.optimizer import _compute_satisfaction_rate

        assert _compute_satisfaction_rate([]) is None

    def test_returns_none_for_unlabeled(self):
        from sio.core.dspy.optimizer import _compute_satisfaction_rate

        examples = [
            {"user_satisfied": None},
            {"user_satisfied": None},
        ]
        assert _compute_satisfaction_rate(examples) is None

    def test_returns_float_for_labeled(self):
        from sio.core.dspy.optimizer import _compute_satisfaction_rate

        examples = [
            {"user_satisfied": 1},
            {"user_satisfied": 0},
            {"user_satisfied": 1},
        ]
        result = _compute_satisfaction_rate(examples)
        assert result is not None
        assert abs(result - 2 / 3) < 1e-9

    def test_returns_zero_for_all_unsatisfied(self):
        from sio.core.dspy.optimizer import _compute_satisfaction_rate

        examples = [{"user_satisfied": 0}, {"user_satisfied": 0}]
        assert _compute_satisfaction_rate(examples) == 0.0


# ---------------------------------------------------------------------------
# T108: _apply_recency_weighting no mutation
# ---------------------------------------------------------------------------


class TestRecencyWeightingNoMutation:
    """T108: _apply_recency_weighting does not mutate input."""

    def test_does_not_mutate_input(self):
        from sio.core.dspy.optimizer import _apply_recency_weighting

        original = [
            {"timestamp": "2026-01-01T00:00:00Z", "session_id": "s1"},
            {"timestamp": "2026-01-02T00:00:00Z", "session_id": "s2"},
        ]
        # Capture original state
        orig_keys_0 = set(original[0].keys())
        orig_keys_1 = set(original[1].keys())

        result = _apply_recency_weighting(original)

        # Input should not be mutated
        assert "weight" not in original[0]
        assert "weight" not in original[1]
        assert set(original[0].keys()) == orig_keys_0
        assert set(original[1].keys()) == orig_keys_1

        # Result should have weights
        assert all("weight" in e for e in result)

    def test_empty_input_returns_empty(self):
        from sio.core.dspy.optimizer import _apply_recency_weighting

        result = _apply_recency_weighting([])
        assert result == []

    def test_returns_new_list(self):
        from sio.core.dspy.optimizer import _apply_recency_weighting

        original = [{"timestamp": "2026-01-01T00:00:00Z"}]
        result = _apply_recency_weighting(original)
        assert result is not original


# ---------------------------------------------------------------------------
# T109: row_factory validation
# ---------------------------------------------------------------------------


class TestRowFactoryValidation:
    """T109: _row_to_dict converts sqlite3.Row to dict."""

    def test_normal_row_converts(self, mem_db):
        from sio.core.db.queries import _row_to_dict

        row = mem_db.execute(
            "SELECT 1 as a, 2 as b"
        ).fetchone()
        d = _row_to_dict(row)
        assert d == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# T110: Unrecognized TOML keys warning
# ---------------------------------------------------------------------------


class TestUnrecognizedTomlKeys:
    """T110: Warning when config has unknown keys."""

    def test_warns_on_unknown_keys(self, tmp_path, caplog):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            'retention_days = 30\n'
            'bogus_key = "should warn"\n'
            'another_unknown = 42\n'
        )
        with caplog.at_level(logging.WARNING, logger="sio.core.config"):
            cfg = load_config(str(config_file))

        assert cfg.retention_days == 30
        assert "bogus_key" in caplog.text or "another_unknown" in caplog.text

    def test_no_warning_for_known_keys(self, tmp_path, caplog):
        config_file = tmp_path / "config.toml"
        config_file.write_text('retention_days = 60\nmin_examples = 20\n')
        with caplog.at_level(logging.WARNING, logger="sio.core.config"):
            load_config(str(config_file))

        assert "Unrecognized" not in caplog.text


# ---------------------------------------------------------------------------
# T111: query_emb shape safety
# ---------------------------------------------------------------------------


class TestQueryEmbShapeSafety:
    """T111: query_emb is flattened before cosine similarity."""

    def test_2d_query_emb_handled(self):
        """search_embedding handles 2D query embedding from encode_single."""
        from sio.core.dspy.corpus_indexer import CorpusIndex

        idx = CorpusIndex(
            file_count=1, chunk_count=2,
            _chunks=[
                {"text": "hello world", "path": "/a.md"},
                {"text": "foo bar baz", "path": "/b.md"},
            ],
        )

        # Inject mock backend and embeddings
        idx._embeddings = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ], dtype=np.float32)

        mock_backend = MagicMock()
        # Return 2D array (shape [1, 4]) to test flattening
        mock_backend.encode_single.return_value = np.array(
            [[1.0, 0.0, 0.0, 0.0]], dtype=np.float32
        )
        idx._backend = mock_backend

        results = idx.search_embedding("hello", top_k=2)
        # Should not crash and should return results
        assert len(results) >= 1
        assert results[0].score > 0


# ---------------------------------------------------------------------------
# T112: Similarity threshold filtering
# ---------------------------------------------------------------------------


class TestSimilarityThresholdFiltering:
    """T112: search_embedding filters results below 0.3 threshold."""

    def test_low_similarity_filtered(self):
        from sio.core.dspy.corpus_indexer import CorpusIndex

        idx = CorpusIndex(
            file_count=1, chunk_count=3,
            _chunks=[
                {"text": "very relevant", "path": "/a.md"},
                {"text": "somewhat relevant", "path": "/b.md"},
                {"text": "totally irrelevant", "path": "/c.md"},
            ],
        )

        # Create embeddings where chunk 2 has near-zero similarity
        idx._embeddings = np.array([
            [1.0, 0.0, 0.0],  # High sim with query
            [0.5, 0.5, 0.0],  # Medium sim
            [0.0, 0.0, 1.0],  # Orthogonal = 0 sim
        ], dtype=np.float32)

        mock_backend = MagicMock()
        mock_backend.encode_single.return_value = np.array(
            [1.0, 0.0, 0.0], dtype=np.float32
        )
        idx._backend = mock_backend

        results = idx.search_embedding("relevant", top_k=3)
        # The orthogonal result (score ~0) should be filtered out
        for r in results:
            assert r.score >= 0.3


# ---------------------------------------------------------------------------
# T113: Batch commits
# ---------------------------------------------------------------------------


class _CommitSpy:
    """Wrapper around sqlite3.Connection that tracks commit() calls.

    sqlite3.Connection.commit is a read-only C attribute, so
    unittest.mock.patch.object cannot be used directly. This wrapper
    delegates all attribute access to the real connection and intercepts
    commit() to record call counts.
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "commit_count", 0)

    def commit(self):
        object.__getattribute__(self, "_conn").commit()
        cnt = object.__getattribute__(self, "commit_count")
        object.__setattr__(self, "commit_count", cnt + 1)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        if name in ("_conn", "commit_count"):
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_conn"), name, value)


class TestBatchCommits:
    """T113/T127: _batch=True skips per-operation commits; _batch=False calls commit."""

    def test_batch_true_skips_commit(self, mem_db, sample_invocation):
        from sio.core.db.queries import insert_invocation

        spy = _CommitSpy(mem_db)
        insert_invocation(spy, sample_invocation(), _batch=True)
        assert spy.commit_count == 0

    def test_batch_false_calls_commit(self, mem_db, sample_invocation):
        from sio.core.db.queries import insert_invocation

        spy = _CommitSpy(mem_db)
        insert_invocation(spy, sample_invocation(), _batch=False)
        assert spy.commit_count == 1

    def test_batch_insert_error_record_no_commit(self, mem_db):
        from datetime import datetime, timezone

        from sio.core.db.queries import insert_error_record

        record = {
            "session_id": "s1", "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_type": "test", "source_file": "test.md",
            "tool_name": "Bash", "error_text": "Error",
            "user_message": "msg", "context_before": None,
            "context_after": None, "error_type": "tool_failure",
            "mined_at": datetime.now(timezone.utc).isoformat(),
        }
        spy = _CommitSpy(mem_db)
        row_id = insert_error_record(spy, record, _batch=True)
        assert row_id > 0
        assert spy.commit_count == 0

    def test_batch_insert_error_record_commits(self, mem_db):
        from datetime import datetime, timezone

        from sio.core.db.queries import insert_error_record

        record = {
            "session_id": "s1", "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_type": "test", "source_file": "test.md",
            "tool_name": "Bash", "error_text": "Error",
            "user_message": "msg", "context_before": None,
            "context_after": None, "error_type": "tool_failure",
            "mined_at": datetime.now(timezone.utc).isoformat(),
        }
        spy = _CommitSpy(mem_db)
        insert_error_record(spy, record, _batch=False)
        assert spy.commit_count == 1

    def test_batch_link_error_to_pattern(self, mem_db):
        from datetime import datetime, timezone

        from sio.core.db.queries import insert_error_record, insert_pattern, link_error_to_pattern

        now = datetime.now(timezone.utc).isoformat()
        pat_id = insert_pattern(mem_db, {
            "pattern_id": "batch-test",
            "description": "test",
            "tool_name": None,
            "error_count": 1,
            "session_count": 1,
            "first_seen": now,
            "last_seen": now,
            "rank_score": 1.0,
            "centroid_embedding": None,
            "created_at": now,
            "updated_at": now,
        })
        err_id = insert_error_record(mem_db, {
            "session_id": "s1", "timestamp": now,
            "source_type": "test", "source_file": "test.md",
            "tool_name": "Bash", "error_text": "Error",
            "user_message": "msg", "context_before": None,
            "context_after": None, "error_type": "tool_failure",
            "mined_at": now,
        })
        spy = _CommitSpy(mem_db)
        link_error_to_pattern(spy, pat_id, err_id, _batch=True)
        assert spy.commit_count == 0


# ---------------------------------------------------------------------------
# T114: LLM disabled diagnostic logging
# ---------------------------------------------------------------------------


class TestLLMDisabledDiagnostic:
    """T114: create_lm logs INFO when returning None."""

    @patch.dict("os.environ", {}, clear=True)
    def test_logs_info_when_no_llm(self, caplog):
        from sio.core.dspy.lm_factory import create_lm

        cfg = SIOConfig()
        with caplog.at_level(logging.INFO, logger="sio.core.dspy.lm_factory"):
            result = create_lm(cfg)

        assert result is None
        assert "No LLM backend available" in caplog.text
        assert "config.toml" in caplog.text


# ---------------------------------------------------------------------------
# Fixture needed by T113
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_invocation():
    """Minimal invocation factory for batch tests."""
    from datetime import datetime, timezone

    def _make(**overrides):
        record = {
            "session_id": "test-session",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": "claude-code",
            "user_message": "test",
            "behavior_type": "skill",
            "actual_action": "Read",
            "expected_action": None,
            "activated": 1,
            "correct_action": 1,
            "correct_outcome": 1,
            "user_satisfied": None,
            "user_note": None,
            "passive_signal": None,
            "history_file": None,
            "line_start": None,
            "line_end": None,
            "token_count": None,
            "latency_ms": None,
            "labeled_by": None,
            "labeled_at": None,
        }
        record.update(overrides)
        return record

    return _make
