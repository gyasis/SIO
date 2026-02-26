"""Tests for sio.ground_truth.reviewer — T037."""

from __future__ import annotations

import pytest

from sio.core.db.queries import insert_ground_truth
from sio.core.db.schema import init_db
from sio.ground_truth.reviewer import approve, edit, reject


@pytest.fixture
def mem_db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _insert_sample(conn, **overrides) -> int:
    """Insert a sample ground truth row and return its ID."""
    defaults = {
        "pattern_id": "test-pattern-001",
        "error_examples_json": '[{"error_text": "timeout"}]',
        "error_type": "tool_failure",
        "pattern_summary": "Bash times out",
        "target_surface": "claude_md_rule",
        "rule_title": "Fix timeout",
        "prevention_instructions": "Add timeout param",
        "rationale": "Prevents timeouts",
        "source": "agent",
    }
    defaults.update(overrides)
    return insert_ground_truth(conn, **defaults)


class TestApprove:
    def test_approve_sets_positive_approved(self, mem_db):
        gt_id = _insert_sample(mem_db)

        result = approve(mem_db, gt_id)

        assert result is True
        row = dict(mem_db.execute(
            "SELECT label, source FROM ground_truth WHERE id = ?", (gt_id,)
        ).fetchone())
        assert row["label"] == "positive"
        assert row["source"] == "approved"

    def test_approve_with_note(self, mem_db):
        gt_id = _insert_sample(mem_db)

        approve(mem_db, gt_id, note="Looks good")

        row = dict(mem_db.execute(
            "SELECT user_note FROM ground_truth WHERE id = ?", (gt_id,)
        ).fetchone())
        assert row["user_note"] == "Looks good"

    def test_approve_sets_reviewed_at(self, mem_db):
        gt_id = _insert_sample(mem_db)

        approve(mem_db, gt_id)

        row = dict(mem_db.execute(
            "SELECT reviewed_at FROM ground_truth WHERE id = ?", (gt_id,)
        ).fetchone())
        assert row["reviewed_at"] is not None

    def test_approve_nonexistent_returns_false(self, mem_db):
        result = approve(mem_db, 9999)
        assert result is False


class TestReject:
    def test_reject_sets_negative_rejected(self, mem_db):
        gt_id = _insert_sample(mem_db)

        result = reject(mem_db, gt_id, note="Not useful")

        assert result is True
        row = dict(mem_db.execute(
            "SELECT label, source, user_note FROM ground_truth WHERE id = ?", (gt_id,)
        ).fetchone())
        assert row["label"] == "negative"
        assert row["source"] == "rejected"
        assert row["user_note"] == "Not useful"

    def test_reject_nonexistent_returns_false(self, mem_db):
        result = reject(mem_db, 9999)
        assert result is False


class TestEdit:
    def test_edit_creates_new_row(self, mem_db):
        gt_id = _insert_sample(mem_db)

        new_id = edit(mem_db, gt_id, {"rule_title": "Better title"})

        assert new_id != gt_id
        # Original row is unchanged
        original = dict(mem_db.execute(
            "SELECT rule_title, label, source FROM ground_truth WHERE id = ?", (gt_id,)
        ).fetchone())
        assert original["rule_title"] == "Fix timeout"

    def test_edit_new_row_has_edited_positive(self, mem_db):
        gt_id = _insert_sample(mem_db)

        new_id = edit(mem_db, gt_id, {"rule_title": "Better title"})

        row = dict(mem_db.execute(
            "SELECT label, source, rule_title FROM ground_truth WHERE id = ?", (new_id,)
        ).fetchone())
        assert row["label"] == "positive"
        assert row["source"] == "edited"
        assert row["rule_title"] == "Better title"

    def test_edit_preserves_unedited_fields(self, mem_db):
        gt_id = _insert_sample(mem_db)

        new_id = edit(mem_db, gt_id, {"rule_title": "New title"})

        row = dict(mem_db.execute(
            "SELECT pattern_id, error_type, rationale FROM ground_truth WHERE id = ?",
            (new_id,),
        ).fetchone())
        assert row["pattern_id"] == "test-pattern-001"
        assert row["error_type"] == "tool_failure"
        assert row["rationale"] == "Prevents timeouts"

    def test_edit_nonexistent_raises(self, mem_db):
        with pytest.raises(ValueError, match="not found"):
            edit(mem_db, 9999, {"rule_title": "New"})

    def test_edit_multiple_fields(self, mem_db):
        gt_id = _insert_sample(mem_db)

        new_id = edit(mem_db, gt_id, {
            "rule_title": "Updated title",
            "prevention_instructions": "Updated instructions",
            "rationale": "Updated rationale",
        })

        row = dict(mem_db.execute(
            "SELECT rule_title, prevention_instructions, rationale "
            "FROM ground_truth WHERE id = ?",
            (new_id,),
        ).fetchone())
        assert row["rule_title"] == "Updated title"
        assert row["prevention_instructions"] == "Updated instructions"
        assert row["rationale"] == "Updated rationale"
