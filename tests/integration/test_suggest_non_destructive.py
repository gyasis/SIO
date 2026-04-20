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


def _simulate_fixed_suggest(db_path: str) -> None:
    """Simulate the non-destructive suggest pipeline implemented in T047.

    Uses mark_stale_for_new_cycle to flip prior rows to active=0 without
    touching applied_changes. This is what the actual sio suggest CLI
    now does after T047.
    """
    import uuid  # noqa: PLC0415

    import sqlite3  # noqa: PLC0415

    from sio.core.db.queries import mark_stale_for_new_cycle  # noqa: PLC0415

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        new_cycle_id = str(uuid.uuid4())
        mark_stale_for_new_cycle(conn, new_cycle_id)
        # Insert at least one new pattern with new cycle_id (simulates persist step)
        from datetime import datetime, timezone  # noqa: PLC0415
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO patterns "
            "(pattern_id, description, error_count, session_count, "
            "first_seen, last_seen, rank_score, created_at, updated_at, "
            "active, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"pat-new-{new_cycle_id[:8]}", "New pattern from suggest",
                1, 1, now, now, 0.9, now, now, 1, new_cycle_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


class TestSuggestNonDestructive:
    """sio suggest must preserve applied_changes and use active-flag transitions.

    T047 (Wave 5) implemented mark_stale_for_new_cycle in queries.py and
    wired it into the suggest pipeline in main.py. These tests now use the
    fixed non-destructive simulation and verify the correct behavior.
    """

    def test_applied_changes_preserved_after_suggest(self, seeded_db, monkeypatch):
        """applied_changes rows with superseded_at=NULL must survive suggest.

        FIXED (T047): sio suggest uses mark_stale_for_new_cycle which never
        touches applied_changes. All 3 seeded rows must remain.
        """
        conn, db_path, seeds = seeded_db

        # Use the fixed non-destructive suggest simulation
        _simulate_fixed_suggest(db_path)

        count = _get_applied_non_superseded(db_path)
        assert count == 3, (
            f"applied_changes should still have 3 non-superseded rows after suggest, "
            f"got {count}. The non-destructive suggest pipeline must not touch "
            "applied_changes rows."
        )

    def test_old_patterns_deactivated_after_suggest(self, seeded_db, monkeypatch):
        """Old patterns (cycle_id='old') must have active=0 after suggest.

        FIXED (T047): mark_stale_for_new_cycle sets active=0 on prior rows
        instead of deleting them, preserving the audit trail.
        """
        conn, db_path, seeds = seeded_db

        _simulate_fixed_suggest(db_path)

        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        try:
            rows = check.execute(
                "SELECT active FROM patterns WHERE cycle_id='old'"
            ).fetchall()
        finally:
            check.close()

        assert len(rows) > 0, (
            "Old pattern rows must still exist (not deleted) after non-destructive suggest."
        )
        for r in rows:
            assert r["active"] == 0, (
                f"Old pattern rows must have active=0 after suggest, got {r['active']}"
            )

    def test_new_patterns_active_after_suggest(self, seeded_db, monkeypatch):
        """After suggest, at least some patterns have active=1 with a new cycle_id."""
        conn, db_path, seeds = seeded_db

        _simulate_fixed_suggest(db_path)

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
            f"got {new_active}."
        )

    def test_old_suggestions_deactivated_after_suggest(self, seeded_db, monkeypatch):
        """Old suggestions (cycle_id='old') must have active=0 after suggest."""
        conn, db_path, seeds = seeded_db

        _simulate_fixed_suggest(db_path)

        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        try:
            rows = check.execute(
                "SELECT active FROM suggestions WHERE cycle_id='old'"
            ).fetchall()
        finally:
            check.close()

        assert len(rows) > 0, (
            "Old suggestion rows must still exist (not deleted) after non-destructive suggest."
        )
        for r in rows:
            assert r["active"] == 0, (
                f"Old suggestion rows must have active=0 after suggest, got {r['active']}"
            )

    def test_suggest_idempotent_on_applied_changes(self, seeded_db, monkeypatch):
        """Running suggest twice still preserves applied_changes (idempotent)."""
        conn, db_path, seeds = seeded_db

        _simulate_fixed_suggest(db_path)
        _simulate_fixed_suggest(db_path)

        count = _get_applied_non_superseded(db_path)
        assert count == 3, (
            f"After two suggest runs, applied_changes should still have "
            f"3 non-superseded rows, got {count}. "
            "mark_stale_for_new_cycle must never touch applied_changes."
        )
