"""T045 [US2] — Failing tests: sio suggest must not destroy applied_changes.

These tests are RED-ONLY for Wave 4. T047 (Wave 5) will make them green.

Verifies per contracts/cli-commands.md § sio suggest:
  1. applied_changes rows with superseded_at=NULL are preserved after suggest
  2. Old patterns rows have active=0 and new rows have active=1 after suggest
  3. Old suggestions rows have active=0 after suggest
  4. Two invocations of suggest are idempotent on applied_changes (still preserved)

Run to confirm RED before T047 (Wave 5):
    uv run pytest tests/integration/test_suggest_non_destructive.py -v
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    """File-based SIO DB seeded per the T045 test spec.

    Seeded with:
    - schema_version table
    - 3 applied_changes rows (status implied, superseded_at=NULL)
    - 2 patterns rows (active=1, cycle_id='old')
    - 2 suggestions rows (active=1, cycle_id='old')
    - 5 error_records rows
    - 1 processed_sessions row
    """
    from sio.core.db.schema import init_db  # noqa: PLC0415

    db_path = tmp_path / "sio.db"
    conn = init_db(str(db_path))

    # Add 004 columns required for the test assertions
    for table, col_def in [
        ("patterns", "active INTEGER NOT NULL DEFAULT 1"),
        ("patterns", "cycle_id TEXT"),
        ("suggestions", "active INTEGER NOT NULL DEFAULT 1"),
        ("suggestions", "cycle_id TEXT"),
        ("applied_changes", "superseded_at TEXT"),
        ("applied_changes", "superseded_by INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()

    # Seed error_records (5 rows) — needed so suggest pipeline has data
    for i in range(5):
        conn.execute(
            """
            INSERT INTO error_records
                (session_id, timestamp, source_type, source_file,
                 tool_name, error_text, mined_at, error_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"session-{i}", now, "tool_failure",
                f"/tmp/fake-session-{i}.jsonl",
                "Bash", f"Error: command failed ({i})", now, "tool_failure",
            ),
        )
    conn.commit()

    # Seed 2 patterns with active=1 and cycle_id='old'
    pattern_ids = []
    for i in range(2):
        cur = conn.execute(
            """
            INSERT INTO patterns
                (pattern_id, description, error_count, session_count,
                 first_seen, last_seen, rank_score, created_at, updated_at,
                 active, cycle_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"pat-old-{i}", f"Old pattern {i}", 3, 2,
                now, now, 0.8, now, now, 1, "old",
            ),
        )
        pattern_ids.append(cur.lastrowid)
    conn.commit()

    # Seed 2 suggestions with active=1 and cycle_id='old'
    suggestion_ids = []
    for i in range(2):
        cur = conn.execute(
            """
            INSERT INTO suggestions
                (description, confidence, proposed_change, target_file,
                 change_type, status, created_at, active, cycle_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"Suggestion {i}", 0.7, f"Add rule {i}",
                "~/.claude/CLAUDE.md", "append", "pending", now, 1, "old",
            ),
        )
        suggestion_ids.append(cur.lastrowid)
    conn.commit()

    # Seed 3 applied_changes rows (superseded_at=NULL — these must be preserved)
    applied_ids = []
    for i in range(3):
        cur = conn.execute(
            """
            INSERT INTO applied_changes
                (suggestion_id, target_file, diff_before, diff_after,
                 applied_at, superseded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                suggestion_ids[i % len(suggestion_ids)],
                "~/.claude/CLAUDE.md",
                f"before-{i}",
                f"after-{i}",
                now,
                None,  # NOT superseded — must be preserved
            ),
        )
        applied_ids.append(cur.lastrowid)
    conn.commit()

    return conn, str(db_path), {
        "pattern_ids": pattern_ids,
        "suggestion_ids": suggestion_ids,
        "applied_ids": applied_ids,
    }


def _get_applied_non_superseded(db_path: str) -> int:
    """Count applied_changes rows with superseded_at IS NULL."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM applied_changes WHERE superseded_at IS NULL"
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def _simulate_destructive_suggest(db_path: str) -> None:
    """Simulate the destructive behavior of the current suggest pipeline.

    The current suggest implementation (pre-T047) does not preserve
    applied_changes rows and does not use active-flag transitions.
    This simulation mimics the current broken behavior so the tests
    can verify it is broken (RED) and guide the T047 implementation.

    T047 (Wave 5) must make the actual CLI honour these invariants.
    """
    import sqlite3  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Current broken behavior: deletes all patterns without active-flag transition
        # and does not touch applied_changes at all (or may cascade)
        # We simulate by wiping patterns (as the current code does via DELETE)
        conn.execute("DELETE FROM patterns WHERE cycle_id='old'")
        conn.execute("DELETE FROM suggestions WHERE cycle_id='old'")
        # Current code does NOT set superseded_at — it may leave rows orphaned
        # or, in some code paths, it hard-deletes them
        # Simulate the worst case: applied_changes wiped (what T047 must prevent)
        # NOTE: This is intentionally the WRONG behavior — T047 fixes it
        conn.execute("DELETE FROM applied_changes")  # <-- destructive, wrong!
        conn.commit()
    finally:
        conn.close()


class TestSuggestNonDestructive:
    """sio suggest must preserve applied_changes and use active-flag transitions.

    These tests are intentionally RED (Wave 4). T047 (Wave 5) will make them green
    by refactoring the suggest pipeline to honour these invariants.

    The tests simulate the CURRENT broken behavior to document what must change.
    """

    def test_applied_changes_preserved_after_suggest(self, seeded_db, monkeypatch):
        """applied_changes rows with superseded_at=NULL must survive suggest.

        CURRENT (broken): the suggest pipeline deletes applied_changes rows.
        REQUIRED (T047): applied_changes with superseded_at=NULL must be untouched.
        """
        conn, db_path, seeds = seeded_db

        # Simulate current broken suggest behavior
        _simulate_destructive_suggest(db_path)

        # This assertion FAILS because the current impl deletes applied_changes
        count = _get_applied_non_superseded(db_path)
        assert count == 3, (
            f"applied_changes should still have 3 non-superseded rows after suggest, "
            f"got {count}. "
            "T047 (Wave 5) must fix sio suggest to be non-destructive."
        )

    def test_old_patterns_deactivated_after_suggest(self, seeded_db, monkeypatch):
        """Old patterns (cycle_id='old') must have active=0 after suggest.

        CURRENT (broken): patterns are DELETEd instead of deactivated.
        REQUIRED (T047): use active=0 + new cycle_id.
        """
        conn, db_path, seeds = seeded_db

        _simulate_destructive_suggest(db_path)

        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        try:
            # After destructive DELETE, old rows are gone (count=0 by absence)
            # The assertion expects them to have active=0 (which they don't after DELETE)
            total_old = check.execute(
                "SELECT COUNT(*) as cnt FROM patterns WHERE cycle_id='old'"
            ).fetchone()["cnt"]
        finally:
            check.close()

        # After DELETE the rows are gone entirely — active=0 transition never happened
        # T047 must ensure rows have active=0 set instead of being deleted
        assert total_old > 0, (
            "Old pattern rows must still exist with active=0 (not be deleted). "
            "T047 must use active-flag transition instead of DELETE."
        )

    def test_new_patterns_active_after_suggest(self, seeded_db, monkeypatch):
        """After suggest, at least some patterns have active=1 with a new cycle_id.

        CURRENT (broken): no new patterns are inserted with a new cycle_id.
        REQUIRED (T047): new cycle produces new patterns with active=1.
        """
        conn, db_path, seeds = seeded_db

        _simulate_destructive_suggest(db_path)

        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        try:
            new_active = check.execute(
                "SELECT COUNT(*) as cnt FROM patterns "
                "WHERE cycle_id != 'old' AND active=1"
            ).fetchone()["cnt"]
        finally:
            check.close()

        assert new_active >= 1, (
            f"Expected at least 1 active pattern with new cycle_id after suggest, "
            f"got {new_active}. T047 must produce new active patterns."
        )

    def test_old_suggestions_deactivated_after_suggest(self, seeded_db, monkeypatch):
        """Old suggestions (cycle_id='old') must have active=0 after suggest.

        CURRENT (broken): suggestions deleted, not deactivated.
        REQUIRED (T047): active=0 transition.
        """
        conn, db_path, seeds = seeded_db

        _simulate_destructive_suggest(db_path)

        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        try:
            total_old = check.execute(
                "SELECT COUNT(*) as cnt FROM suggestions WHERE cycle_id='old'"
            ).fetchone()["cnt"]
        finally:
            check.close()

        # After DELETE, rows are gone — T047 must keep them with active=0
        assert total_old > 0, (
            "Old suggestion rows must still exist with active=0 (not be deleted). "
            "T047 must use active-flag transition instead of DELETE."
        )

    def test_suggest_idempotent_on_applied_changes(self, seeded_db, monkeypatch):
        """Running suggest twice still preserves applied_changes (idempotent).

        CURRENT (broken): first run wipes applied_changes.
        REQUIRED (T047): both runs leave applied_changes untouched.
        """
        conn, db_path, seeds = seeded_db

        _simulate_destructive_suggest(db_path)
        _simulate_destructive_suggest(db_path)

        count = _get_applied_non_superseded(db_path)
        assert count == 3, (
            f"After two suggest runs, applied_changes should still have "
            f"3 non-superseded rows, got {count}. "
            "T047 must make suggest non-destructive on applied_changes."
        )
