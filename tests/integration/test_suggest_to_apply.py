"""Integration test: suggest → approve → apply → rollback pipeline."""

import sqlite3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_pattern_and_dataset(conn: sqlite3.Connection) -> tuple[int, int]:
    cur = conn.execute(
        "INSERT INTO patterns "
        "(pattern_id, description, tool_name, error_count, session_count, "
        " first_seen, last_seen, rank_score, created_at, updated_at) "
        "VALUES ('pat-int', 'integration test', 'Bash', 5, 2, "
        "datetime('now'), datetime('now'), 3.5, datetime('now'), datetime('now'))",
    )
    pid = cur.lastrowid
    cur2 = conn.execute(
        "INSERT INTO datasets "
        "(pattern_id, file_path, positive_count, negative_count, "
        " created_at, updated_at) "
        "VALUES (?, '/tmp/ds.json', 10, 5, datetime('now'), datetime('now'))",
        (pid,),
    )
    did = cur2.lastrowid
    conn.commit()
    return pid, did


def _seed_suggestion(conn, pattern_id, dataset_id, target_file):
    cur = conn.execute(
        "INSERT INTO suggestions "
        "(pattern_id, dataset_id, description, confidence, proposed_change, "
        " target_file, change_type, status, created_at) "
        "VALUES (?, ?, 'integration test', 0.85, "
        "'## Rule: Integration\\n\\nAlways test.', ?, "
        "'claude_md_rule', 'pending', datetime('now'))",
        (pattern_id, dataset_id, target_file),
    )
    conn.commit()
    return cur.lastrowid


# =========================================================================
# TestFullPipeline
# =========================================================================

class TestFullPipeline:
    """End-to-end: generate → approve → apply → verify → rollback → verify."""

    def test_full_cycle(self, v2_db, tmp_path):
        from sio.applier.rollback import rollback_change
        from sio.applier.writer import apply_change
        from sio.review.reviewer import approve, review_pending

        target = tmp_path / "CLAUDE.md"
        original_content = "# My CLAUDE.md\n\nExisting rules.\n"
        target.write_text(original_content)

        pid, did = _seed_pattern_and_dataset(v2_db)
        sid = _seed_suggestion(v2_db, pid, did, str(target))

        # Step 1: Review shows pending
        pending = review_pending(v2_db)
        assert len(pending) == 1
        assert pending[0]["id"] == sid

        # Step 2: Approve
        ok = approve(v2_db, sid)
        assert ok is True

        # Step 3: Apply
        result = apply_change(v2_db, sid)
        assert result["success"] is True
        changed_content = target.read_text()
        assert "# My CLAUDE.md" in changed_content  # original preserved
        assert "## Rule: Integration" in changed_content  # new content added

        # Step 4: Verify suggestion status is 'applied'
        row = v2_db.execute(
            "SELECT status FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert row[0] == "applied"

        # Step 5: Rollback
        change_id = result["change_id"]
        rb_result = rollback_change(v2_db, change_id)
        assert rb_result["success"] is True

        # Step 6: Verify file restored
        assert target.read_text() == original_content

        # Step 7: Verify suggestion status is 'rolled_back'
        row = v2_db.execute(
            "SELECT status FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert row[0] == "rolled_back"

    def test_apply_without_approve_fails(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change

        target = tmp_path / "CLAUDE.md"
        target.write_text("content")
        pid, did = _seed_pattern_and_dataset(v2_db)
        sid = _seed_suggestion(v2_db, pid, did, str(target))
        # Don't approve — try to apply directly
        result = apply_change(v2_db, sid)
        assert result["success"] is False

    def test_double_apply_fails(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        from sio.review.reviewer import approve

        target = tmp_path / "CLAUDE.md"
        target.write_text("content")
        pid, did = _seed_pattern_and_dataset(v2_db)
        sid = _seed_suggestion(v2_db, pid, did, str(target))
        approve(v2_db, sid)
        apply_change(v2_db, sid)  # First apply
        result = apply_change(v2_db, sid)  # Second apply
        assert result["success"] is False

    def test_review_after_apply_excludes_applied(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        from sio.review.reviewer import approve, review_pending

        target = tmp_path / "CLAUDE.md"
        target.write_text("content")
        pid, did = _seed_pattern_and_dataset(v2_db)
        sid = _seed_suggestion(v2_db, pid, did, str(target))
        approve(v2_db, sid)
        apply_change(v2_db, sid)
        pending = review_pending(v2_db)
        assert len(pending) == 0
