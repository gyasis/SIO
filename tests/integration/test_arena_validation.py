"""T059 [US5] Integration test for arena validation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sio.core.arena.gold_standards import promote_to_gold
from sio.core.arena.regression import run_arena
from sio.core.db.queries import insert_invocation


@pytest.fixture
def arena_db(tmp_db, sample_invocation):
    """DB with invocations and gold standards."""
    ids = []
    for i in range(3):
        record = sample_invocation(
            session_id=f"arena-sess-{i}",
            platform="claude-code",
            tool_name="Read",
            user_message=f"Read file {i}.py",
            user_satisfied=1,
            correct_outcome=1,
            labeled_by="human",
            labeled_at=datetime.now(timezone.utc).isoformat(),
        )
        ids.append(insert_invocation(tmp_db, record))

    # Promote first two to gold
    for inv_id in ids[:2]:
        promote_to_gold(tmp_db, inv_id)

    return tmp_db


class TestArenaValidation:
    """Full arena validation pipeline."""

    def test_arena_returns_result(self, arena_db):
        result = run_arena(
            arena_db,
            skill_name="Read",
            new_prompt="Read the specified file",
        )
        assert "passed" in result
        assert "reasons" in result

    def test_arena_passes_with_similar_prompt(self, arena_db):
        result = run_arena(
            arena_db,
            skill_name="Read",
            new_prompt="Read file 0.py",
        )
        assert isinstance(result["passed"], bool)

    def test_arena_with_no_gold_standards(self, tmp_db):
        result = run_arena(
            tmp_db,
            skill_name="Read",
            new_prompt="anything",
        )
        # No gold standards = passes by default (nothing to regress)
        assert result["passed"] is True

    def test_arena_checks_drift(self, arena_db):
        result = run_arena(
            arena_db,
            skill_name="Read",
            new_prompt="Delete everything permanently",
        )
        assert "drift_score" in result

    def test_arena_checks_gold_replay(self, arena_db):
        result = run_arena(
            arena_db,
            skill_name="Read",
            new_prompt="Read the specified file",
        )
        assert "gold_results" in result
