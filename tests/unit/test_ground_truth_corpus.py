"""Tests for sio.ground_truth.corpus — T038."""

from __future__ import annotations

import pytest

from sio.core.db.queries import insert_ground_truth, update_ground_truth_label
from sio.core.db.schema import init_db


@pytest.fixture
def mem_db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _insert_gt(conn, label="pending", source="agent", **overrides) -> int:
    defaults = {
        "pattern_id": "test-pattern-001",
        "error_examples_json": '[{"error_text": "timeout"}]',
        "error_type": "tool_failure",
        "pattern_summary": "Tool times out",
        "target_surface": "claude_md_rule",
        "rule_title": "Fix timeout",
        "prevention_instructions": "Add timeout param",
        "rationale": "Prevents timeouts",
        "source": source,
    }
    defaults.update(overrides)
    row_id = insert_ground_truth(conn, **defaults)
    if label != "pending":
        update_ground_truth_label(conn, row_id, label=label, source=source)
    return row_id


class TestLoadTrainingCorpus:
    def test_returns_dspy_examples(self, mem_db):
        """Should return list of dspy.Example objects."""
        _insert_gt(mem_db, label="positive", source="approved")

        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)

        assert len(corpus) == 1
        import dspy
        assert isinstance(corpus[0], dspy.Example)

    def test_with_inputs_set_correctly(self, mem_db):
        """with_inputs should mark error_examples, error_type, pattern_summary."""
        _insert_gt(mem_db, label="positive", source="approved")

        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)
        ex = corpus[0]

        # Access input fields
        assert ex.error_examples is not None
        assert ex.error_type is not None
        assert ex.pattern_summary is not None

        # Access output fields
        assert ex.target_surface is not None
        assert ex.rule_title is not None
        assert ex.prevention_instructions is not None
        assert ex.rationale is not None

    def test_only_positive_rows_included(self, mem_db):
        """Only positive-labeled rows should appear in corpus."""
        _insert_gt(mem_db, label="positive", source="approved",
                    rule_title="Good rule")
        _insert_gt(mem_db, label="negative", source="rejected",
                    rule_title="Bad rule")
        _insert_gt(mem_db, label="pending", source="agent",
                    rule_title="Pending rule")

        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)

        assert len(corpus) == 1
        assert corpus[0].rule_title == "Good rule"

    def test_negative_rows_excluded(self, mem_db):
        """Negative-labeled rows must not appear."""
        _insert_gt(mem_db, label="negative", source="rejected")

        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)
        assert len(corpus) == 0

    def test_pending_rows_excluded(self, mem_db):
        """Pending rows must not appear."""
        _insert_gt(mem_db, label="pending", source="agent")

        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)
        assert len(corpus) == 0

    def test_empty_db_returns_empty(self, mem_db):
        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)
        assert corpus == []

    def test_multiple_positive_rows(self, mem_db):
        """All positive rows should be included."""
        for i in range(5):
            _insert_gt(
                mem_db, label="positive", source="approved",
                rule_title=f"Rule {i}",
                pattern_id=f"pattern-{i}",
            )

        from sio.ground_truth.corpus import load_training_corpus

        corpus = load_training_corpus(mem_db)
        assert len(corpus) == 5
