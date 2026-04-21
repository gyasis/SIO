"""T030 [US2] Unit tests for sio.core.feedback.pattern_flag — skill pattern flagging."""

from __future__ import annotations

from sio.core.db.queries import insert_invocation
from sio.core.feedback.pattern_flag import flag_pattern


def _insert_many(conn, factory, records):
    """Insert multiple invocations, returning their IDs."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestFlagPatternMarksSkill:
    """flag_pattern should mark a skill as a priority optimization candidate."""

    def test_flag_pattern_marks_skill(self, tmp_db, sample_invocation):
        # Insert some invocations for the skill so the DB is not empty
        _insert_many(
            tmp_db,
            sample_invocation,
            [
                {"tool_name": "Read", "platform": "claude-code"},
                {"tool_name": "Read", "platform": "claude-code"},
            ],
        )
        result = flag_pattern(tmp_db, skill_name="Read", note="frequent false triggers")
        assert result is True

    def test_flag_pattern_returns_true_on_success(self, tmp_db):
        result = flag_pattern(tmp_db, skill_name="Bash", note="needs optimization")
        assert result is True


class TestFlagRequiresNote:
    """flag_pattern should return False if note is empty or None."""

    def test_empty_note_returns_false(self, tmp_db):
        result = flag_pattern(tmp_db, skill_name="Read", note="")
        assert result is False

    def test_none_note_returns_false(self, tmp_db):
        result = flag_pattern(tmp_db, skill_name="Read", note=None)
        assert result is False

    def test_whitespace_only_note_returns_false(self, tmp_db):
        result = flag_pattern(tmp_db, skill_name="Read", note="   ")
        assert result is False


class TestFlaggedSkillQueryable:
    """Flagged skills should be retrievable from the database."""

    def test_flagged_skill_queryable(self, tmp_db):
        flag_pattern(tmp_db, skill_name="Read", note="too many false positives")
        flag_pattern(tmp_db, skill_name="Bash", note="slow execution")

        # Query the flagged skills directly from the DB
        # The implementation should store flags in a queryable way
        rows = tmp_db.execute(
            "SELECT * FROM pattern_flags WHERE skill_name = ?", ("Read",)
        ).fetchall()
        assert len(rows) >= 1

    def test_flag_stores_note(self, tmp_db):
        flag_pattern(tmp_db, skill_name="Grep", note="wrong regex patterns")
        rows = tmp_db.execute(
            "SELECT * FROM pattern_flags WHERE skill_name = ?", ("Grep",)
        ).fetchall()
        assert len(rows) >= 1
        row = dict(rows[0])
        assert row["note"] == "wrong regex patterns"

    def test_multiple_flags_per_skill(self, tmp_db):
        flag_pattern(tmp_db, skill_name="Read", note="issue one")
        flag_pattern(tmp_db, skill_name="Read", note="issue two")
        rows = tmp_db.execute(
            "SELECT * FROM pattern_flags WHERE skill_name = ?", ("Read",)
        ).fetchall()
        assert len(rows) == 2
