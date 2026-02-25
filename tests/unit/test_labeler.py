"""T028 [US2] Unit tests for sio.core.feedback.labeler — inline feedback labeling."""

from __future__ import annotations

from sio.core.db.queries import get_invocation_by_id, insert_invocation
from sio.core.feedback.labeler import label_latest


def _insert_many(conn, factory, records):
    """Insert multiple invocations, returning their IDs."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestLabelLatestSatisfied:
    """label_latest with '++' should set user_satisfied=1."""

    def test_label_latest_satisfied(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(session_id="sess-01"))
        result = label_latest(tmp_db, session_id="sess-01", signal="++", note=None)
        assert result is True
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_satisfied"] == 1
        assert row["labeled_by"] == "inline"
        assert row["labeled_at"] is not None


class TestLabelLatestUnsatisfied:
    """label_latest with '--' should set user_satisfied=0."""

    def test_label_latest_unsatisfied(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(session_id="sess-02"))
        result = label_latest(tmp_db, session_id="sess-02", signal="--", note=None)
        assert result is True
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_satisfied"] == 0
        assert row["labeled_by"] == "inline"


class TestWithNote:
    """label_latest should store the optional note."""

    def test_with_note(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(session_id="sess-03"))
        label_latest(tmp_db, session_id="sess-03", signal="++", note="worked perfectly")
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_note"] == "worked perfectly"

    def test_without_note_leaves_null(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(session_id="sess-04"))
        label_latest(tmp_db, session_id="sess-04", signal="--", note=None)
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_note"] is None


class TestRelabelOverwrites:
    """Labeling the same invocation twice should overwrite with the second label."""

    def test_relabel_overwrites(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(session_id="sess-05"))

        label_latest(tmp_db, session_id="sess-05", signal="++", note="first")
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_satisfied"] == 1
        assert row["user_note"] == "first"

        label_latest(tmp_db, session_id="sess-05", signal="--", note="changed my mind")
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_satisfied"] == 0
        assert row["user_note"] == "changed my mind"


class TestInvalidSessionReturnsFalse:
    """label_latest should return False when session_id has no invocations."""

    def test_invalid_session_returns_false(self, tmp_db, sample_invocation):
        # Insert into a different session so DB is not empty
        insert_invocation(tmp_db, sample_invocation(session_id="sess-other"))
        result = label_latest(tmp_db, session_id="nonexistent-session", signal="++", note=None)
        assert result is False

    def test_empty_db_returns_false(self, tmp_db):
        result = label_latest(tmp_db, session_id="no-data", signal="--", note=None)
        assert result is False


class TestLabelLatestPicksMostRecent:
    """When multiple invocations exist for a session, label_latest targets the newest."""

    def test_labels_most_recent_invocation(self, tmp_db, sample_invocation):
        id_old = insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-multi",
                timestamp="2026-01-01T00:00:00+00:00",
            ),
        )
        id_new = insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-multi",
                timestamp="2026-01-02T00:00:00+00:00",
            ),
        )

        label_latest(tmp_db, session_id="sess-multi", signal="++", note=None)

        old_row = get_invocation_by_id(tmp_db, id_old)
        new_row = get_invocation_by_id(tmp_db, id_new)
        assert old_row["user_satisfied"] is None  # untouched
        assert new_row["user_satisfied"] == 1  # labeled
