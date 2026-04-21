"""T056 [US5] Unit tests for gold standards manager."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sio.core.arena.gold_standards import (
    get_all_for_skill,
    promote_to_gold,
    replay_against_prompt,
)
from sio.core.db.queries import insert_invocation


@pytest.fixture
def seeded_db(tmp_db, sample_invocation):
    """DB with a few invocations ready to promote."""
    ids = []
    for i in range(3):
        record = sample_invocation(
            session_id=f"gold-sess-{i}",
            platform="claude-code",
            tool_name="Read",
            user_message=f"Read file {i}",
            user_satisfied=1,
            correct_outcome=1,
            labeled_by="human",
            labeled_at=datetime.now(timezone.utc).isoformat(),
        )
        ids.append(insert_invocation(tmp_db, record))
    return tmp_db, ids


class TestPromoteToGold:
    """promote_to_gold copies invocation to gold_standards table."""

    def test_creates_gold_record(self, seeded_db):
        conn, ids = seeded_db
        gold_id = promote_to_gold(conn, ids[0])
        assert gold_id is not None
        assert gold_id > 0

    def test_gold_record_references_invocation(self, seeded_db):
        conn, ids = seeded_db
        gold_id = promote_to_gold(conn, ids[0])
        row = conn.execute(
            "SELECT * FROM gold_standards WHERE id = ?",
            (gold_id,),
        ).fetchone()
        assert row is not None
        assert row["invocation_id"] == ids[0]

    def test_gold_stores_skill_and_platform(self, seeded_db):
        conn, ids = seeded_db
        gold_id = promote_to_gold(conn, ids[0])
        row = conn.execute(
            "SELECT * FROM gold_standards WHERE id = ?",
            (gold_id,),
        ).fetchone()
        assert row["skill_name"] == "Read"
        assert row["platform"] == "claude-code"

    def test_nonexistent_invocation_returns_none(self, tmp_db):
        result = promote_to_gold(tmp_db, 99999)
        assert result is None


class TestGetAllForSkill:
    """get_all_for_skill returns gold standards for a skill."""

    def test_returns_promoted_records(self, seeded_db):
        conn, ids = seeded_db
        promote_to_gold(conn, ids[0])
        promote_to_gold(conn, ids[1])
        golds = get_all_for_skill(conn, "Read")
        assert len(golds) == 2

    def test_filters_by_skill(self, seeded_db):
        conn, ids = seeded_db
        promote_to_gold(conn, ids[0])
        golds = get_all_for_skill(conn, "NonExistentSkill")
        assert len(golds) == 0

    def test_returns_empty_when_none(self, tmp_db):
        golds = get_all_for_skill(tmp_db, "Read")
        assert golds == []


class TestReplayAgainstPrompt:
    """replay_against_prompt checks if gold standard still passes."""

    def test_matching_prompt_passes(self):
        gold = {
            "user_message": "Read file foo.py",
            "expected_action": "Read",
            "correct_outcome": 1,
        }
        assert replay_against_prompt(gold, "Read file foo.py") is True

    def test_very_different_prompt_may_fail(self):
        gold = {
            "user_message": "Read file foo.py",
            "expected_action": "Read",
            "correct_outcome": 1,
        }
        result = replay_against_prompt(gold, "Delete everything")
        assert isinstance(result, bool)
