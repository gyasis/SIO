"""Tests for sio.applier.rollback — revert applied changes."""

import sqlite3

import pytest

from sio.core.db.schema import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_applied_change(
    conn: sqlite3.Connection,
    target_file: str,
    diff_before: str = "# Original\n",
    diff_after: str = "# Original\n\n## New Rule\nContent.\n",
) -> tuple[int, int]:
    """Insert suggestion + applied_change. Returns (suggestion_id, change_id)."""
    cur = conn.execute(
        "INSERT INTO suggestions "
        "(pattern_id, dataset_id, description, confidence, proposed_change, "
        " target_file, change_type, status, created_at) "
        "VALUES (1, 1, 'test', 0.8, 'rule', ?, 'claude_md_rule', 'applied', datetime('now'))",
        (target_file,),
    )
    sid = cur.lastrowid
    cur2 = conn.execute(
        "INSERT INTO applied_changes "
        "(suggestion_id, target_file, diff_before, diff_after, applied_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (sid, target_file, diff_before, diff_after),
    )
    conn.commit()
    return sid, cur2.lastrowid


# =========================================================================
# TestRollback
# =========================================================================

class TestRollback:
    """rollback_change() restores file to diff_before state."""

    def test_restores_file_content(self, v2_db, tmp_path):
        from sio.applier.rollback import rollback_change
        target = tmp_path / "CLAUDE.md"
        original = "# Original\n"
        modified = "# Original\n\n## Added Rule\n"
        target.write_text(modified)
        _, cid = _seed_applied_change(
            v2_db, str(target), diff_before=original, diff_after=modified,
        )
        result = rollback_change(v2_db, cid)
        assert result["success"] is True
        assert target.read_text() == original

    def test_marks_rolled_back_at(self, v2_db, tmp_path):
        from sio.applier.rollback import rollback_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("modified")
        _, cid = _seed_applied_change(v2_db, str(target))
        rollback_change(v2_db, cid)
        row = v2_db.execute(
            "SELECT rolled_back_at FROM applied_changes WHERE id = ?", (cid,)
        ).fetchone()
        assert row[0] is not None

    def test_updates_suggestion_status(self, v2_db, tmp_path):
        from sio.applier.rollback import rollback_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("modified")
        sid, cid = _seed_applied_change(v2_db, str(target))
        rollback_change(v2_db, cid)
        row = v2_db.execute(
            "SELECT status FROM suggestions WHERE id = ?", (sid,)
        ).fetchone()
        assert row[0] == "rolled_back"

    def test_nonexistent_change_returns_error(self, v2_db):
        from sio.applier.rollback import rollback_change
        result = rollback_change(v2_db, 9999)
        assert result["success"] is False

    def test_already_rolled_back_returns_error(self, v2_db, tmp_path):
        from sio.applier.rollback import rollback_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("modified")
        _, cid = _seed_applied_change(v2_db, str(target))
        rollback_change(v2_db, cid)  # First rollback
        result = rollback_change(v2_db, cid)  # Second attempt
        assert result["success"] is False
        assert "already" in result.get("reason", "").lower()


# =========================================================================
# TestRollbackLogging
# =========================================================================

class TestRollbackLogging:
    """rollback_change() returns metadata about the rollback."""

    def test_returns_target_file(self, v2_db, tmp_path):
        from sio.applier.rollback import rollback_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("modified")
        _, cid = _seed_applied_change(v2_db, str(target))
        result = rollback_change(v2_db, cid)
        assert result["target_file"] == str(target)

    def test_returns_change_id(self, v2_db, tmp_path):
        from sio.applier.rollback import rollback_change
        target = tmp_path / "CLAUDE.md"
        target.write_text("modified")
        _, cid = _seed_applied_change(v2_db, str(target))
        result = rollback_change(v2_db, cid)
        assert result["change_id"] == cid
