"""Unit tests for sio.core.db.queries — the query layer over behavior_invocations."""

from __future__ import annotations

import pytest

from sio.core.db.queries import (
    count_by_pattern,
    count_by_platform,
    get_by_session,
    get_by_skill,
    get_invocation_by_id,
    get_labeled_for_optimizer,
    get_skill_health,
    get_unlabeled,
    insert_invocation,
    update_satisfaction,
)


def _insert_many(conn, factory, records):
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestInsertInvocation:
    def test_insert_returns_positive_int(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation())
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_insert_sequential_ids(self, tmp_db, sample_invocation):
        id1 = insert_invocation(tmp_db, sample_invocation())
        id2 = insert_invocation(tmp_db, sample_invocation())
        assert id2 > id1


class TestGetInvocationById:
    def test_retrieves_inserted_record(self, tmp_db, sample_invocation):
        record = sample_invocation(session_id="sess-abc", platform="claude-code", tool_name="Bash", user_message="run ls")
        row_id = insert_invocation(tmp_db, record)
        result = get_invocation_by_id(tmp_db, row_id)
        assert result is not None
        assert result["session_id"] == "sess-abc"
        assert result["actual_action"] == "Bash"

    def test_returns_none_for_missing_id(self, tmp_db):
        assert get_invocation_by_id(tmp_db, 99999) is None


class TestGetUnlabeled:
    def test_returns_only_unlabeled(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": None},
            {"platform": "claude-code", "user_satisfied": None},
            {"platform": "claude-code", "user_satisfied": 1},
        ])
        unlabeled = get_unlabeled(tmp_db, platform="claude-code")
        assert len(unlabeled) == 2

    def test_respects_limit(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [{"platform": "claude-code", "user_satisfied": None} for _ in range(5)])
        results = get_unlabeled(tmp_db, platform="claude-code", limit=2)
        assert len(results) == 2


class TestGetBySkill:
    def test_filters_by_actual_action(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [{"tool_name": "Read"}, {"tool_name": "Read"}, {"tool_name": "Bash"}])
        results = get_by_skill(tmp_db, skill_name="Read")
        assert len(results) == 2
        assert all(r["actual_action"] == "Read" for r in results)


class TestGetBySession:
    def test_filters_by_session_id(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [{"session_id": "sess-001"}, {"session_id": "sess-001"}, {"session_id": "sess-002"}])
        results = get_by_session(tmp_db, session_id="sess-001")
        assert len(results) == 2

    def test_empty_for_unknown_session(self, tmp_db, sample_invocation):
        insert_invocation(tmp_db, sample_invocation(session_id="sess-001"))
        assert get_by_session(tmp_db, session_id="nonexistent") == []


class TestUpdateSatisfaction:
    def test_updates_satisfaction_fields(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(user_satisfied=None))
        success = update_satisfaction(tmp_db, id=row_id, satisfied=1, note="Great", labeled_by="human")
        assert success is True
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_satisfied"] == 1
        assert row["user_note"] == "Great"
        assert row["labeled_by"] == "human"
        assert row["labeled_at"] is not None

    def test_returns_false_for_missing_id(self, tmp_db):
        assert update_satisfaction(tmp_db, id=99999, satisfied=1, note=None, labeled_by="human") is False


class TestCountByPlatform:
    def test_returns_correct_counts(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code"}, {"platform": "claude-code"}, {"platform": "cursor"},
        ])
        counts = count_by_platform(tmp_db)
        assert counts["claude-code"] == 2
        assert counts["cursor"] == 1

    def test_empty_db_returns_empty_dict(self, tmp_db):
        assert count_by_platform(tmp_db) == {}


class TestGetSkillHealth:
    def test_returns_aggregated_metrics(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"tool_name": "Read", "platform": "claude-code", "user_satisfied": 1},
            {"tool_name": "Read", "platform": "claude-code", "user_satisfied": 0},
            {"tool_name": "Read", "platform": "claude-code", "user_satisfied": None},
        ])
        health = get_skill_health(tmp_db, platform="claude-code")
        assert len(health) >= 1
        read_h = [h for h in health if h["skill_name"] == "Read"][0]
        assert read_h["total_invocations"] == 3
        assert read_h["satisfied_count"] == 1
        assert read_h["unsatisfied_count"] == 1
        assert read_h["unlabeled_count"] == 1


class TestGetLabeledForOptimizer:
    def test_returns_labeled_above_threshold(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"tool_name": "Read", "platform": "claude-code", "user_satisfied": 1, "labeled_by": "human"}
            for _ in range(12)
        ])
        results = get_labeled_for_optimizer(tmp_db, skill="Read", platform="claude-code", min_examples=10)
        assert len(results) == 12

    def test_returns_empty_below_threshold(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"tool_name": "Read", "platform": "claude-code", "user_satisfied": 1, "labeled_by": "human"}
            for _ in range(5)
        ])
        assert get_labeled_for_optimizer(tmp_db, skill="Read", platform="claude-code", min_examples=10) == []


class TestCountByPattern:
    def test_counts_matching_patterns(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"behavior_type": "skill", "correct_outcome": 0},
            {"behavior_type": "skill", "correct_outcome": 0},
            {"behavior_type": "skill", "correct_outcome": 1},
        ])
        count = count_by_pattern(tmp_db, behavior_type="skill", failure_mode="incorrect_outcome")
        assert count == 2

    def test_empty_db_returns_zero(self, tmp_db):
        assert count_by_pattern(tmp_db, behavior_type="skill", failure_mode="incorrect_outcome") == 0
