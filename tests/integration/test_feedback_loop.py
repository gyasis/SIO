"""T031 [US2] Integration test — full feedback loop from logging to labeling to health."""

from __future__ import annotations

import pytest

from sio.core.db.queries import (
    get_invocation_by_id,
    get_skill_health,
    insert_invocation,
)
from sio.core.feedback.labeler import label_latest


def _insert_many(conn, factory, records):
    """Insert multiple invocations, returning their IDs."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestLogThenLabel:
    """Insert invocations, label them, and verify health aggregates."""

    def test_log_then_label(self, tmp_db, sample_invocation):
        # Step 1: Insert 10 invocations across unique sessions so label_latest
        # targets each one independently.
        session_ids = [f"sess-{i:03d}" for i in range(10)]
        inv_ids = []
        for i, sid in enumerate(session_ids):
            row_id = insert_invocation(
                tmp_db,
                sample_invocation(
                    session_id=sid,
                    platform="claude-code",
                    tool_name="Read",
                    timestamp=f"2026-01-01T{i:02d}:00:00+00:00",
                ),
            )
            inv_ids.append(row_id)

        # Step 2: Label first 5 as satisfied (++), last 5 as unsatisfied (--)
        for sid in session_ids[:5]:
            result = label_latest(tmp_db, session_id=sid, signal="++", note=None)
            assert result is True, f"label_latest failed for {sid}"

        for sid in session_ids[5:]:
            result = label_latest(tmp_db, session_id=sid, signal="--", note=None)
            assert result is True, f"label_latest failed for {sid}"

        # Step 3: Verify individual records
        for rid in inv_ids[:5]:
            row = get_invocation_by_id(tmp_db, rid)
            assert row["user_satisfied"] == 1, f"Invocation {rid} should be satisfied"

        for rid in inv_ids[5:]:
            row = get_invocation_by_id(tmp_db, rid)
            assert row["user_satisfied"] == 0, f"Invocation {rid} should be unsatisfied"

        # Step 4: Verify health aggregate shows 50% satisfaction
        health = get_skill_health(tmp_db, platform="claude-code", skill="Read")
        assert len(health) == 1
        read_health = health[0]
        assert read_health["total_invocations"] == 10
        assert read_health["satisfied_count"] == 5
        assert read_health["unsatisfied_count"] == 5
        assert read_health["unlabeled_count"] == 0
        assert read_health["satisfaction_rate"] == pytest.approx(0.5)

    def test_partial_labeling_shows_unlabeled(self, tmp_db, sample_invocation):
        """When only some invocations are labeled, health should reflect unlabeled count."""
        session_ids = [f"partial-{i:03d}" for i in range(6)]
        for i, sid in enumerate(session_ids):
            insert_invocation(
                tmp_db,
                sample_invocation(
                    session_id=sid,
                    platform="claude-code",
                    tool_name="Bash",
                    timestamp=f"2026-02-01T{i:02d}:00:00+00:00",
                ),
            )

        # Label only first 2
        label_latest(tmp_db, session_id="partial-000", signal="++", note=None)
        label_latest(tmp_db, session_id="partial-001", signal="--", note=None)

        health = get_skill_health(tmp_db, platform="claude-code", skill="Bash")
        assert len(health) == 1
        bash_health = health[0]
        assert bash_health["total_invocations"] == 6
        assert bash_health["satisfied_count"] == 1
        assert bash_health["unsatisfied_count"] == 1
        assert bash_health["unlabeled_count"] == 4
        assert bash_health["satisfaction_rate"] == pytest.approx(0.5)

    def test_label_then_relabel_reflected_in_health(self, tmp_db, sample_invocation):
        """Relabeling should be reflected in health aggregates."""
        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="relabel-sess",
                platform="claude-code",
                tool_name="Edit",
            ),
        )

        # First label: satisfied
        label_latest(tmp_db, session_id="relabel-sess", signal="++", note=None)
        health = get_skill_health(tmp_db, platform="claude-code", skill="Edit")
        assert health[0]["satisfied_count"] == 1
        assert health[0]["unsatisfied_count"] == 0

        # Relabel: unsatisfied
        label_latest(tmp_db, session_id="relabel-sess", signal="--", note="actually bad")
        health = get_skill_health(tmp_db, platform="claude-code", skill="Edit")
        assert health[0]["satisfied_count"] == 0
        assert health[0]["unsatisfied_count"] == 1
