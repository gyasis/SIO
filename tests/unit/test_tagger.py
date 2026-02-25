"""Tests for sio.review.tagger — AI-assisted and human tagging."""

import sqlite3

import pytest

from sio.core.db.schema import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_suggestion(conn: sqlite3.Connection, **overrides) -> int:
    """Insert one pending suggestion and return its ID."""
    defaults = {
        "pattern_id": 1,
        "dataset_id": 1,
        "description": "test suggestion",
        "confidence": 0.75,
        "proposed_change": "add a rule",
        "target_file": "CLAUDE.md",
        "change_type": "claude_md_rule",
        "status": "pending",
    }
    defaults.update(overrides)
    cur = conn.execute(
        "INSERT INTO suggestions "
        "(pattern_id, dataset_id, description, confidence, proposed_change, "
        " target_file, change_type, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (defaults["pattern_id"], defaults["dataset_id"], defaults["description"],
         defaults["confidence"], defaults["proposed_change"], defaults["target_file"],
         defaults["change_type"], defaults["status"]),
    )
    conn.commit()
    return cur.lastrowid


def _make_pattern(tool_name: str = "Bash", error_count: int = 5) -> dict:
    return {
        "id": 1,
        "pattern_id": "pat-test",
        "description": f"errors with {tool_name}",
        "tool_name": tool_name,
        "error_count": error_count,
        "session_count": 2,
        "rank_score": 3.5,
    }


def _make_dataset(positive: int = 10, negative: int = 5) -> dict:
    return {
        "id": 1,
        "pattern_id": "pat-test",
        "file_path": "/tmp/ds.json",
        "positive_count": positive,
        "negative_count": negative,
        "examples": [
            {"type": "positive", "message": "error msg 1"},
            {"type": "positive", "message": "error msg 2"},
            {"type": "negative", "message": "success msg 1"},
        ],
    }


# =========================================================================
# TestAiTag
# =========================================================================

class TestAiTag:
    """ai_tag() generates an explanation from pattern + dataset examples."""

    def test_returns_string(self):
        from sio.review.tagger import ai_tag
        result = ai_tag(_make_pattern(), _make_dataset())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_tool_name(self):
        from sio.review.tagger import ai_tag
        result = ai_tag(_make_pattern(tool_name="Read"), _make_dataset())
        assert "Read" in result

    def test_includes_error_count(self):
        from sio.review.tagger import ai_tag
        result = ai_tag(_make_pattern(error_count=12), _make_dataset())
        assert "12" in result

    def test_includes_example_count(self):
        from sio.review.tagger import ai_tag
        ds = _make_dataset(positive=8, negative=3)
        result = ai_tag(_make_pattern(), ds)
        # Should mention the number of examples
        assert "8" in result or "positive" in result.lower()

    def test_handles_empty_examples(self):
        from sio.review.tagger import ai_tag
        ds = _make_dataset()
        ds["examples"] = []
        result = ai_tag(_make_pattern(), ds)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_handles_missing_examples_key(self):
        from sio.review.tagger import ai_tag
        ds = _make_dataset()
        del ds["examples"]
        result = ai_tag(_make_pattern(), ds)
        assert isinstance(result, str)


# =========================================================================
# TestHumanTag
# =========================================================================

class TestHumanTag:
    """human_tag() records a user categorization on a suggestion."""

    def test_records_category(self, v2_db):
        from sio.review.tagger import human_tag
        sid = _seed_suggestion(v2_db)
        result = human_tag(v2_db, sid, category="useful")
        assert result is True
        row = v2_db.execute(
            "SELECT ai_explanation FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert "useful" in row[0]

    def test_records_category_with_note(self, v2_db):
        from sio.review.tagger import human_tag
        sid = _seed_suggestion(v2_db)
        human_tag(v2_db, sid, category="false-positive", note="not relevant")
        row = v2_db.execute(
            "SELECT ai_explanation, user_note FROM suggestions WHERE id = ?",
            (sid,),
        ).fetchone()
        assert "false-positive" in row[0]
        assert row[1] == "not relevant"

    def test_overwrites_previous_tag(self, v2_db):
        from sio.review.tagger import human_tag
        sid = _seed_suggestion(v2_db)
        human_tag(v2_db, sid, category="useful")
        human_tag(v2_db, sid, category="false-positive")
        row = v2_db.execute(
            "SELECT ai_explanation FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert "false-positive" in row[0]

    def test_nonexistent_returns_false(self, v2_db):
        from sio.review.tagger import human_tag
        result = human_tag(v2_db, 9999, category="useful")
        assert result is False

    def test_tag_persists_after_approve(self, v2_db):
        from sio.review.tagger import human_tag
        sid = _seed_suggestion(v2_db)
        human_tag(v2_db, sid, category="important")
        # Approve doesn't clear the tag
        v2_db.execute(
            "UPDATE suggestions SET status = 'approved' WHERE id = ?", (sid,)
        )
        v2_db.commit()
        row = v2_db.execute(
            "SELECT ai_explanation, status FROM suggestions WHERE id = ?",
            (sid,),
        ).fetchone()
        assert "important" in row[0]
        assert row[1] == "approved"


# =========================================================================
# TestAiTagOnSuggestion
# =========================================================================

class TestAiTagOnSuggestion:
    """ai_tag_suggestion() applies AI tag and stores it on the suggestion row."""

    def test_stores_ai_explanation(self, v2_db):
        from sio.review.tagger import ai_tag_suggestion
        sid = _seed_suggestion(v2_db)
        result = ai_tag_suggestion(
            v2_db, sid, _make_pattern(), _make_dataset()
        )
        assert result is True
        row = v2_db.execute(
            "SELECT ai_explanation FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert row[0] is not None
        assert len(row[0]) > 0

    def test_nonexistent_returns_false(self, v2_db):
        from sio.review.tagger import ai_tag_suggestion
        result = ai_tag_suggestion(
            v2_db, 9999, _make_pattern(), _make_dataset()
        )
        assert result is False

    def test_does_not_change_status(self, v2_db):
        from sio.review.tagger import ai_tag_suggestion
        sid = _seed_suggestion(v2_db)
        ai_tag_suggestion(v2_db, sid, _make_pattern(), _make_dataset())
        row = v2_db.execute(
            "SELECT status FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert row[0] == "pending"
