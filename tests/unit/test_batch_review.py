"""T029 [US2] Unit tests for sio.core.feedback.batch_review — batch label review."""

from __future__ import annotations

from sio.core.db.queries import get_invocation_by_id, insert_invocation
from sio.core.feedback.batch_review import apply_label, get_reviewable


def _insert_many(conn, factory, records):
    """Insert multiple invocations, returning their IDs."""
    ids = []
    for overrides in records:
        row_id = insert_invocation(conn, factory(**overrides))
        ids.append(row_id)
    return ids


class TestGetReviewableReturnsUnlabeled:
    """get_reviewable should only return invocations where user_satisfied IS NULL."""

    def test_get_reviewable_returns_unlabeled(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": None},
            {"platform": "claude-code", "user_satisfied": None},
            {"platform": "claude-code", "user_satisfied": 1},
            {"platform": "claude-code", "user_satisfied": 0},
        ])
        results = get_reviewable(tmp_db, platform="claude-code")
        assert len(results) == 2
        assert all(r["user_satisfied"] is None for r in results)

    def test_respects_limit(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": None}
            for _ in range(10)
        ])
        results = get_reviewable(tmp_db, platform="claude-code", limit=3)
        assert len(results) == 3

    def test_filters_by_session_id(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "session_id": "sess-A", "user_satisfied": None},
            {"platform": "claude-code", "session_id": "sess-A", "user_satisfied": None},
            {"platform": "claude-code", "session_id": "sess-B", "user_satisfied": None},
        ])
        results = get_reviewable(tmp_db, platform="claude-code", session_id="sess-A")
        assert len(results) == 2
        assert all(r["session_id"] == "sess-A" for r in results)

    def test_empty_when_all_labeled(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": 1},
            {"platform": "claude-code", "user_satisfied": 0},
        ])
        results = get_reviewable(tmp_db, platform="claude-code")
        assert results == []


class TestSortedByTimestamp:
    """get_reviewable should return results ordered by timestamp (oldest first for review)."""

    def test_sorted_by_timestamp(self, tmp_db, sample_invocation):
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": None, "timestamp": "2026-01-03T00:00:00+00:00"},
            {"platform": "claude-code", "user_satisfied": None, "timestamp": "2026-01-01T00:00:00+00:00"},
            {"platform": "claude-code", "user_satisfied": None, "timestamp": "2026-01-02T00:00:00+00:00"},
        ])
        results = get_reviewable(tmp_db, platform="claude-code")
        timestamps = [r["timestamp"] for r in results]
        assert timestamps == sorted(timestamps)


class TestApplyLabelUpdatesRecord:
    """apply_label should update the invocation with satisfaction and metadata."""

    def test_apply_label_updates_record(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(
            platform="claude-code", user_satisfied=None,
        ))
        result = apply_label(tmp_db, invocation_id=row_id, signal="++", note="looks good")
        assert result is True
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_satisfied"] == 1
        assert row["user_note"] == "looks good"
        assert row["labeled_by"] is not None
        assert row["labeled_at"] is not None

    def test_apply_label_unsatisfied(self, tmp_db, sample_invocation):
        row_id = insert_invocation(tmp_db, sample_invocation(
            platform="claude-code", user_satisfied=None,
        ))
        result = apply_label(tmp_db, invocation_id=row_id, signal="--", note="wrong output")
        assert result is True
        row = get_invocation_by_id(tmp_db, row_id)
        assert row["user_satisfied"] == 0

    def test_apply_label_missing_id_returns_false(self, tmp_db):
        result = apply_label(tmp_db, invocation_id=99999, signal="++", note=None)
        assert result is False


class TestSkewWarning:
    """FR-026: warn if >90% of labeled invocations are one class."""

    def test_skew_warning_when_imbalanced(self, tmp_db, sample_invocation):
        # Insert 10 satisfied + 1 unsatisfied = 91% satisfied => should trigger warning
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": 1}
            for _ in range(10)
        ])
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": 0},
        ])
        # get_reviewable on the full platform should include skew metadata
        # Since all are labeled, we check with unlabeled ones present too
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": None}
            for _ in range(3)
        ])
        results = get_reviewable(tmp_db, platform="claude-code")
        # The result or its metadata should indicate skew
        # Implementation may attach skew_warning as a key in the result dict
        # or the function may return a wrapper with metadata.
        # We test that the skew information is present somehow.
        assert len(results) == 3  # only unlabeled returned
        # Check if any result dict has skew_warning key, OR if first result has it
        has_skew = any("skew_warning" in r for r in results)
        assert has_skew is True, "Expected skew_warning key in results when >90% one class"

    def test_no_skew_warning_when_balanced(self, tmp_db, sample_invocation):
        # 5 satisfied + 5 unsatisfied = 50% => no warning
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": 1}
            for _ in range(5)
        ])
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": 0}
            for _ in range(5)
        ])
        _insert_many(tmp_db, sample_invocation, [
            {"platform": "claude-code", "user_satisfied": None}
            for _ in range(3)
        ])
        results = get_reviewable(tmp_db, platform="claude-code")
        has_skew = any(r.get("skew_warning") for r in results)
        assert has_skew is False, "No skew_warning expected when labels are balanced"
