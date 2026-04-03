"""Unit tests for flow-to-skill promotion.

Tests cover:
- ``promote_flow_to_skill()`` with real flow data in DB
- ``get_promotable_flows()`` filtering by session count and success rate
- Edge cases: missing flow, insufficient data
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from sio.clustering.grader import promote_flow_to_skill
from sio.core.db.schema import init_db
from sio.mining.flow_pipeline import get_promotable_flows

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _ts(days_ago: float = 0) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def db():
    """In-memory SIO database with schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _insert_flow_event(
    db,
    *,
    flow_hash: str = "testhash001",
    sequence: str = "Read \u2192 Edit \u2192 Bash",
    session_id: str = "sess-001",
    was_successful: int = 1,
    ngram_size: int = 3,
    duration_seconds: float = 15.0,
    days_ago: float = 1,
) -> int:
    cur = db.execute(
        "INSERT INTO flow_events "
        "(session_id, flow_hash, sequence, ngram_size, was_successful, "
        "duration_seconds, source_file, timestamp, mined_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            flow_hash,
            sequence,
            ngram_size,
            was_successful,
            duration_seconds,
            "test.jsonl",
            _ts(days_ago),
            _NOW.isoformat(),
        ),
    )
    db.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Tests: promote_flow_to_skill
# ---------------------------------------------------------------------------


class TestPromoteFlowToSkill:
    def test_missing_flow_returns_none(self, db) -> None:
        """A nonexistent flow_hash should return None."""
        result = promote_flow_to_skill(db, "nonexistent")
        assert result is None

    def test_single_event_insufficient_data(self, db) -> None:
        """A flow with only 1 event should return None (insufficient)."""
        _insert_flow_event(db, flow_hash="single", session_id="s1")
        result = promote_flow_to_skill(db, "single")
        assert result is None

    def test_successful_promotion(self, db, tmp_path) -> None:
        """A flow with multiple events should produce a skill file."""
        # Insert multiple flow events
        for i in range(5):
            _insert_flow_event(
                db,
                flow_hash="promote001",
                sequence="Read \u2192 Edit \u2192 Bash",
                session_id=f"sess-{i}",
                was_successful=1,
                days_ago=i * 0.5,
            )

        skills_dir = str(tmp_path / "skills")
        os.makedirs(skills_dir, exist_ok=True)

        import sio.clustering.grader as grader_mod

        original_expanduser = grader_mod.os.path.expanduser

        def _mock_expanduser(path):
            if "~/.claude/skills" in path:
                return skills_dir
            return original_expanduser(path)

        grader_mod.os.path.expanduser = _mock_expanduser
        try:
            result = promote_flow_to_skill(db, "promote001")
        finally:
            grader_mod.os.path.expanduser = original_expanduser

        assert result is not None
        assert os.path.exists(result)

    def test_promotion_writes_valid_markdown(self, db, tmp_path) -> None:
        """The generated skill file should contain valid Markdown content."""
        for i in range(3):
            _insert_flow_event(
                db,
                flow_hash="md001",
                sequence="Grep \u2192 Read",
                session_id=f"sess-md-{i}",
                was_successful=1,
            )

        # Override the skill output directory
        skills_dir = str(tmp_path / "skills")
        os.makedirs(skills_dir, exist_ok=True)

        # Monkey-patch os.path.expanduser within grader module
        import sio.clustering.grader as grader_mod

        original_expanduser = grader_mod.os.path.expanduser

        def _mock_expanduser(path):
            if "~/.claude/skills" in path:
                return skills_dir
            return original_expanduser(path)

        grader_mod.os.path.expanduser = _mock_expanduser
        try:
            result = promote_flow_to_skill(db, "md001")
        finally:
            grader_mod.os.path.expanduser = original_expanduser

        assert result is not None
        assert os.path.exists(result)

        content = open(result, encoding="utf-8").read()
        assert "# Skill:" in content
        assert "## When to Use" in content or "## When to use" in content
        assert "## Steps" in content
        assert "Grep" in content
        assert "Read" in content

    def test_promotion_aggregates_sessions(self, db, tmp_path) -> None:
        """Promotion should count distinct sessions in the skill metadata."""
        for i in range(6):
            _insert_flow_event(
                db,
                flow_hash="agg001",
                sequence="Edit \u2192 Bash",
                session_id=f"sess-agg-{i}",
                was_successful=1 if i < 5 else 0,  # 5/6 successful
            )

        skills_dir = str(tmp_path / "skills")
        os.makedirs(skills_dir, exist_ok=True)

        import sio.clustering.grader as grader_mod

        original_expanduser = grader_mod.os.path.expanduser

        def _mock_expanduser(path):
            if "~/.claude/skills" in path:
                return skills_dir
            return original_expanduser(path)

        grader_mod.os.path.expanduser = _mock_expanduser
        try:
            result = promote_flow_to_skill(db, "agg001")
        finally:
            grader_mod.os.path.expanduser = original_expanduser

        assert result is not None
        content = open(result, encoding="utf-8").read()
        # Should reference the number of sessions
        assert "6" in content or "sessions" in content.lower()


# ---------------------------------------------------------------------------
# Tests: get_promotable_flows
# ---------------------------------------------------------------------------


class TestGetPromotableFlows:
    def test_empty_db_returns_empty(self, db) -> None:
        result = get_promotable_flows(db)
        assert result == []

    def test_filters_by_min_sessions(self, db) -> None:
        """Flows with fewer than min_sessions should be excluded."""
        # Insert flow with only 3 sessions (below default min_sessions=5)
        for i in range(3):
            _insert_flow_event(
                db,
                flow_hash="few-sessions",
                session_id=f"sess-{i}",
                was_successful=1,
            )

        result = get_promotable_flows(db, min_sessions=5)
        assert len(result) == 0

    def test_meets_min_sessions(self, db) -> None:
        """Flows with enough sessions and high success rate should be returned."""
        for i in range(6):
            _insert_flow_event(
                db,
                flow_hash="enough-sessions",
                session_id=f"sess-{i}",
                was_successful=1,
            )

        result = get_promotable_flows(db, min_sessions=5)
        assert len(result) == 1
        assert result[0]["flow_hash"] == "enough-sessions"
        assert result[0]["session_count"] >= 5
        assert result[0]["success_rate"] > 70.0

    def test_filters_low_success_rate(self, db) -> None:
        """Flows with success_rate <= 70% should be excluded."""
        # Insert 10 events across 5 sessions, but only 5 successful (50%)
        for i in range(10):
            _insert_flow_event(
                db,
                flow_hash="low-success",
                session_id=f"sess-{i % 5}",
                was_successful=1 if i < 5 else 0,
            )

        result = get_promotable_flows(db, min_sessions=5)
        assert len(result) == 0

    def test_custom_min_sessions(self, db) -> None:
        """Custom min_sessions threshold works correctly."""
        for i in range(3):
            _insert_flow_event(
                db,
                flow_hash="custom-min",
                session_id=f"sess-{i}",
                was_successful=1,
            )

        # min_sessions=2 should include this flow
        result = get_promotable_flows(db, min_sessions=2)
        assert len(result) == 1
        assert result[0]["flow_hash"] == "custom-min"

    def test_returns_expected_fields(self, db) -> None:
        """Each promotable flow dict should have all expected fields."""
        for i in range(5):
            _insert_flow_event(
                db,
                flow_hash="fields-test",
                session_id=f"sess-{i}",
                was_successful=1,
            )

        result = get_promotable_flows(db, min_sessions=5)
        assert len(result) == 1

        flow = result[0]
        expected_keys = {
            "flow_hash", "sequence", "ngram_size", "count",
            "success_count", "success_rate", "avg_duration",
            "session_count", "last_seen",
        }
        assert expected_keys.issubset(flow.keys())

    def test_multiple_promotable_flows(self, db) -> None:
        """Multiple qualifying flows should all be returned."""
        for i in range(5):
            _insert_flow_event(
                db,
                flow_hash="flow-a",
                sequence="Read \u2192 Edit",
                session_id=f"sess-a-{i}",
                was_successful=1,
            )
        for i in range(5):
            _insert_flow_event(
                db,
                flow_hash="flow-b",
                sequence="Grep \u2192 Read \u2192 Edit",
                session_id=f"sess-b-{i}",
                was_successful=1,
            )

        result = get_promotable_flows(db, min_sessions=5)
        assert len(result) == 2
        hashes = {f["flow_hash"] for f in result}
        assert "flow-a" in hashes
        assert "flow-b" in hashes
