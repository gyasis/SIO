"""Tests for sio.review.reviewer — human review of suggestions."""

import sqlite3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_suggestions(conn: sqlite3.Connection, count: int = 3) -> list[int]:
    """Insert *count* pending suggestions and return their IDs."""
    ids = []
    for i in range(1, count + 1):
        cur = conn.execute(
            "INSERT INTO suggestions "
            "(pattern_id, dataset_id, description, confidence, proposed_change, "
            " target_file, change_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))",
            (1, 1, f"desc-{i}", 0.5 + i * 0.1, f"change-{i}", "CLAUDE.md", "claude_md_rule"),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _seed_pattern_and_dataset(conn: sqlite3.Connection) -> tuple[int, int]:
    """Insert a minimal pattern+dataset pair and return (pattern_id, dataset_id)."""
    cur = conn.execute(
        "INSERT INTO patterns "
        "(pattern_id, description, tool_name, error_count, session_count, "
        " first_seen, last_seen, rank_score, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?, datetime('now'), datetime('now'))",
        ("pat-test", "test pattern", "Bash", 5, 2, 3.5),
    )
    pid = cur.lastrowid
    cur2 = conn.execute(
        "INSERT INTO datasets "
        "(pattern_id, file_path, positive_count, negative_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
        (pid, "/tmp/ds.json", 10, 5),
    )
    did = cur2.lastrowid
    conn.commit()
    return pid, did


# =========================================================================
# TestReviewPending
# =========================================================================


class TestReviewPending:
    """review_pending() loads suggestions with status='pending'."""

    def test_returns_empty_when_no_suggestions(self, v2_db):
        from sio.review.reviewer import review_pending

        assert review_pending(v2_db) == []

    def test_returns_only_pending(self, v2_db):
        from sio.review.reviewer import review_pending

        ids = _seed_suggestions(v2_db, 3)
        # Approve one
        v2_db.execute(
            "UPDATE suggestions SET status = 'approved' WHERE id = ?",
            (ids[0],),
        )
        v2_db.commit()
        result = review_pending(v2_db)
        assert len(result) == 2
        assert all(r["status"] == "pending" for r in result)

    def test_result_has_expected_keys(self, v2_db):
        from sio.review.reviewer import review_pending

        _seed_suggestions(v2_db, 1)
        result = review_pending(v2_db)
        assert len(result) == 1
        r = result[0]
        for key in (
            "id",
            "description",
            "confidence",
            "proposed_change",
            "target_file",
            "change_type",
            "status",
        ):
            assert key in r

    def test_ordered_by_confidence_descending(self, v2_db):
        from sio.review.reviewer import review_pending

        _seed_suggestions(v2_db, 3)
        result = review_pending(v2_db)
        confidences = [r["confidence"] for r in result]
        assert confidences == sorted(confidences, reverse=True)


# =========================================================================
# TestApprove
# =========================================================================


class TestApprove:
    """approve() transitions status to 'approved'."""

    def test_approve_changes_status(self, v2_db):
        from sio.review.reviewer import approve

        ids = _seed_suggestions(v2_db, 1)
        result = approve(v2_db, ids[0])
        assert result is True
        row = v2_db.execute("SELECT status FROM suggestions WHERE id = ?", (ids[0],)).fetchone()
        assert row[0] == "approved"

    def test_approve_with_note(self, v2_db):
        from sio.review.reviewer import approve

        ids = _seed_suggestions(v2_db, 1)
        approve(v2_db, ids[0], note="looks good")
        row = v2_db.execute("SELECT user_note FROM suggestions WHERE id = ?", (ids[0],)).fetchone()
        assert row[0] == "looks good"

    def test_approve_sets_reviewed_at(self, v2_db):
        from sio.review.reviewer import approve

        ids = _seed_suggestions(v2_db, 1)
        approve(v2_db, ids[0])
        row = v2_db.execute(
            "SELECT reviewed_at FROM suggestions WHERE id = ?", (ids[0],)
        ).fetchone()
        assert row[0] is not None

    def test_approve_nonexistent_returns_false(self, v2_db):
        from sio.review.reviewer import approve

        result = approve(v2_db, 9999)
        assert result is False

    def test_approve_already_rejected_still_works(self, v2_db):
        from sio.review.reviewer import approve

        ids = _seed_suggestions(v2_db, 1)
        v2_db.execute(
            "UPDATE suggestions SET status = 'rejected' WHERE id = ?",
            (ids[0],),
        )
        v2_db.commit()
        result = approve(v2_db, ids[0])
        assert result is True
        row = v2_db.execute("SELECT status FROM suggestions WHERE id = ?", (ids[0],)).fetchone()
        assert row[0] == "approved"


# =========================================================================
# TestReject
# =========================================================================


class TestReject:
    """reject() transitions status to 'rejected'."""

    def test_reject_changes_status(self, v2_db):
        from sio.review.reviewer import reject

        ids = _seed_suggestions(v2_db, 1)
        result = reject(v2_db, ids[0])
        assert result is True
        row = v2_db.execute("SELECT status FROM suggestions WHERE id = ?", (ids[0],)).fetchone()
        assert row[0] == "rejected"

    def test_reject_with_note(self, v2_db):
        from sio.review.reviewer import reject

        ids = _seed_suggestions(v2_db, 1)
        reject(v2_db, ids[0], note="not useful")
        row = v2_db.execute("SELECT user_note FROM suggestions WHERE id = ?", (ids[0],)).fetchone()
        assert row[0] == "not useful"

    def test_reject_nonexistent_returns_false(self, v2_db):
        from sio.review.reviewer import reject

        result = reject(v2_db, 9999)
        assert result is False

    def test_reject_sets_reviewed_at(self, v2_db):
        from sio.review.reviewer import reject

        ids = _seed_suggestions(v2_db, 1)
        reject(v2_db, ids[0])
        row = v2_db.execute(
            "SELECT reviewed_at FROM suggestions WHERE id = ?", (ids[0],)
        ).fetchone()
        assert row[0] is not None


# =========================================================================
# TestDefer
# =========================================================================


class TestDefer:
    """defer() leaves status as 'pending' but records a note."""

    def test_defer_keeps_pending(self, v2_db):
        from sio.review.reviewer import defer

        ids = _seed_suggestions(v2_db, 1)
        result = defer(v2_db, ids[0])
        assert result is True
        row = v2_db.execute("SELECT status FROM suggestions WHERE id = ?", (ids[0],)).fetchone()
        assert row[0] == "pending"

    def test_defer_with_note(self, v2_db):
        from sio.review.reviewer import defer

        ids = _seed_suggestions(v2_db, 1)
        defer(v2_db, ids[0], note="revisit later")
        row = v2_db.execute("SELECT user_note FROM suggestions WHERE id = ?", (ids[0],)).fetchone()
        assert row[0] == "revisit later"

    def test_defer_nonexistent_returns_false(self, v2_db):
        from sio.review.reviewer import defer

        result = defer(v2_db, 9999)
        assert result is False


# =========================================================================
# TestStatePersistence
# =========================================================================


class TestStatePersistence:
    """State persists across separate calls."""

    def test_approve_then_review_excludes_approved(self, v2_db):
        from sio.review.reviewer import approve, review_pending

        ids = _seed_suggestions(v2_db, 3)
        approve(v2_db, ids[0])
        pending = review_pending(v2_db)
        approved_ids = [r["id"] for r in pending]
        assert ids[0] not in approved_ids
        assert len(pending) == 2

    def test_reject_then_review_excludes_rejected(self, v2_db):
        from sio.review.reviewer import reject, review_pending

        ids = _seed_suggestions(v2_db, 3)
        reject(v2_db, ids[1])
        pending = review_pending(v2_db)
        assert len(pending) == 2
        assert ids[1] not in [r["id"] for r in pending]

    def test_multiple_operations(self, v2_db):
        from sio.review.reviewer import approve, defer, reject, review_pending

        ids = _seed_suggestions(v2_db, 5)
        approve(v2_db, ids[0])
        reject(v2_db, ids[1])
        defer(v2_db, ids[2], note="later")
        pending = review_pending(v2_db)
        # Deferred stays pending
        assert len(pending) == 3  # ids[2], ids[3], ids[4]


# =========================================================================
# TestGetSuggestionById
# =========================================================================


class TestGetSuggestionById:
    """get_suggestion() retrieves a single suggestion by ID."""

    def test_returns_suggestion(self, v2_db):
        from sio.review.reviewer import get_suggestion

        ids = _seed_suggestions(v2_db, 1)
        result = get_suggestion(v2_db, ids[0])
        assert result is not None
        assert result["id"] == ids[0]

    def test_returns_none_for_missing(self, v2_db):
        from sio.review.reviewer import get_suggestion

        result = get_suggestion(v2_db, 9999)
        assert result is None
