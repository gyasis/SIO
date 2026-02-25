"""Tests for sio.applier.writer — apply approved changes to config files."""

import os
import sqlite3

import pytest

from sio.core.db.schema import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_approved_suggestion(conn: sqlite3.Connection, **overrides) -> int:
    """Insert one approved suggestion and return its ID."""
    defaults = {
        "pattern_id": 1,
        "dataset_id": 1,
        "description": "test suggestion",
        "confidence": 0.85,
        "proposed_change": "## Rule: Test\n\nAlways test before committing.",
        "target_file": "CLAUDE.md",
        "change_type": "claude_md_rule",
        "status": "approved",
    }
    defaults.update(overrides)
    cur = conn.execute(
        "INSERT INTO suggestions "
        "(pattern_id, dataset_id, description, confidence, proposed_change, "
        " target_file, change_type, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (defaults["pattern_id"], defaults["dataset_id"], defaults["description"],
         defaults["confidence"], defaults["proposed_change"], defaults["target_file"],
         defaults["change_type"], defaults["status"]),
    )
    conn.commit()
    return cur.lastrowid


# =========================================================================
# TestApplyToCLAUDEmd
# =========================================================================

class TestApplyToCLAUDEmd:
    """Writer appends to CLAUDE.md (never overwrites existing content)."""

    def test_appends_to_existing_file(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing Rules\n\nDo not delete.\n")
        sid = _seed_approved_suggestion(
            v2_db, target_file=str(target),
            proposed_change="## New Rule\n\nNew content.",
        )
        result = apply_change(v2_db, sid)
        content = target.read_text()
        assert "# Existing Rules" in content
        assert "Do not delete." in content
        assert "## New Rule" in content
        assert result["success"] is True

    def test_creates_file_if_missing(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        sid = _seed_approved_suggestion(
            v2_db, target_file=str(target),
            proposed_change="## First Rule\n\nContent.",
        )
        result = apply_change(v2_db, sid)
        assert target.exists()
        assert "## First Rule" in target.read_text()

    def test_stores_diff_before(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        original = "# Original\n"
        target.write_text(original)
        sid = _seed_approved_suggestion(v2_db, target_file=str(target))
        result = apply_change(v2_db, sid)
        assert result["diff_before"] == original

    def test_stores_diff_after(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Original\n")
        sid = _seed_approved_suggestion(v2_db, target_file=str(target))
        result = apply_change(v2_db, sid)
        assert result["diff_after"] == target.read_text()

    def test_records_applied_change_in_db(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("")
        sid = _seed_approved_suggestion(v2_db, target_file=str(target))
        result = apply_change(v2_db, sid)
        row = v2_db.execute(
            "SELECT * FROM applied_changes WHERE suggestion_id = ?", (sid,)
        ).fetchone()
        assert row is not None
        assert dict(row)["target_file"] == str(target)

    def test_updates_suggestion_status_to_applied(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("")
        sid = _seed_approved_suggestion(v2_db, target_file=str(target))
        apply_change(v2_db, sid)
        row = v2_db.execute(
            "SELECT status FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert row[0] == "applied"


# =========================================================================
# TestApplyNotApproved
# =========================================================================

class TestApplyNotApproved:
    """Writer refuses to apply non-approved suggestions."""

    def test_pending_suggestion_fails(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("")
        sid = _seed_approved_suggestion(v2_db, target_file=str(target), status="pending")
        # Override status back to pending
        v2_db.execute("UPDATE suggestions SET status = 'pending' WHERE id = ?", (sid,))
        v2_db.commit()
        result = apply_change(v2_db, sid)
        assert result["success"] is False
        assert "not approved" in result.get("reason", "").lower()

    def test_rejected_suggestion_fails(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("")
        sid = _seed_approved_suggestion(v2_db, target_file=str(target))
        v2_db.execute("UPDATE suggestions SET status = 'rejected' WHERE id = ?", (sid,))
        v2_db.commit()
        result = apply_change(v2_db, sid)
        assert result["success"] is False

    def test_nonexistent_suggestion_fails(self, v2_db):
        from sio.applier.writer import apply_change
        result = apply_change(v2_db, 9999)
        assert result["success"] is False


# =========================================================================
# TestApplyReturnsChangeId
# =========================================================================

class TestApplyReturnsChangeId:
    """apply_change returns the applied_changes row ID."""

    def test_returns_change_id(self, v2_db, tmp_path):
        from sio.applier.writer import apply_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("")
        sid = _seed_approved_suggestion(v2_db, target_file=str(target))
        result = apply_change(v2_db, sid)
        assert "change_id" in result
        assert isinstance(result["change_id"], int)
