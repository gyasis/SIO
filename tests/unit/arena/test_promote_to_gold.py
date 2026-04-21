"""T039 [US1] — Failing tests for promote_to_gold (TDD red).

Tests per FR-006 + data-model.md §3.2:
  1. Qualified invocation (user_satisfied=1 AND correct_outcome=1) → gold_standards row
  2. Unqualified invocation (user_satisfied=0 or correct_outcome=0) → no gold row
  3. Promoted row has dspy_example_json field populated with valid JSON
  4. promote_to_gold is idempotent: duplicate call doesn't create second gold row
  5. Promoted row has promoted_by='auto'

Run to confirm RED before T040:
    uv run pytest tests/unit/arena/test_promote_to_gold.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_sio_db(tmp_path):
    """In-memory SIO DB with full schema applied plus new columns from 004."""
    conn = init_db(":memory:")

    # Add the columns that T040 will officially add to gold_standards
    # (promoted_by and dspy_example_json per data-model.md §3.2)
    for col_def in [
        "promoted_by TEXT DEFAULT 'auto'",
        "dspy_example_json TEXT",
    ]:
        try:
            conn.execute(f"ALTER TABLE gold_standards ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass  # Column already exists

    yield conn
    conn.close()


def _insert_invocation(conn, *, user_satisfied: int, correct_outcome: int) -> int:
    """Insert a minimal behavior_invocations row and return its id."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO behavior_invocations
            (session_id, timestamp, platform, user_message, behavior_type,
             actual_action, user_satisfied, correct_outcome, activated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "test-session-001",
            now,
            "claude-code",
            "Please help with task X",
            "skill",
            "suggestion_generator",
            user_satisfied,
            correct_outcome,
            1,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPromoteToGold:
    """promote_to_gold behaviour per FR-006 + data-model.md §3.2."""

    def test_qualified_invocation_creates_gold_row(self, tmp_sio_db):
        """user_satisfied=1 AND correct_outcome=1 → gold row inserted."""
        invocation_id = _insert_invocation(tmp_sio_db, user_satisfied=1, correct_outcome=1)

        from sio.core.arena.gold_standards import promote_to_gold  # noqa: PLC0415

        gold_id = promote_to_gold(invocation_id, db_path=tmp_sio_db)

        assert gold_id is not None, "promote_to_gold should return a non-None gold ID"
        row = tmp_sio_db.execute(
            "SELECT * FROM gold_standards WHERE id = ?", (gold_id,)
        ).fetchone()
        assert row is not None, "gold_standards row must be inserted"
        assert row["invocation_id"] == invocation_id

    def test_unsatisfied_invocation_not_promoted(self, tmp_sio_db):
        """user_satisfied=0 → promote_to_gold returns None, no gold row."""
        invocation_id = _insert_invocation(tmp_sio_db, user_satisfied=0, correct_outcome=1)

        from sio.core.arena.gold_standards import promote_to_gold  # noqa: PLC0415

        gold_id = promote_to_gold(invocation_id, db_path=tmp_sio_db)

        assert gold_id is None, "Unsatisfied invocation must not be promoted"
        count = tmp_sio_db.execute(
            "SELECT COUNT(*) FROM gold_standards WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()[0]
        assert count == 0, "No gold_standards row should be inserted"

    def test_incorrect_outcome_not_promoted(self, tmp_sio_db):
        """correct_outcome=0 → promote_to_gold returns None, no gold row."""
        invocation_id = _insert_invocation(tmp_sio_db, user_satisfied=1, correct_outcome=0)

        from sio.core.arena.gold_standards import promote_to_gold  # noqa: PLC0415

        gold_id = promote_to_gold(invocation_id, db_path=tmp_sio_db)

        assert gold_id is None, "Incorrect-outcome invocation must not be promoted"
        count = tmp_sio_db.execute(
            "SELECT COUNT(*) FROM gold_standards WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()[0]
        assert count == 0

    def test_promoted_row_has_dspy_example_json(self, tmp_sio_db):
        """Promoted row must have dspy_example_json populated with valid JSON."""
        invocation_id = _insert_invocation(tmp_sio_db, user_satisfied=1, correct_outcome=1)

        from sio.core.arena.gold_standards import promote_to_gold  # noqa: PLC0415

        gold_id = promote_to_gold(invocation_id, db_path=tmp_sio_db)

        assert gold_id is not None
        row = tmp_sio_db.execute(
            "SELECT dspy_example_json FROM gold_standards WHERE id = ?", (gold_id,)
        ).fetchone()
        assert row is not None
        assert row["dspy_example_json"] is not None, "dspy_example_json field must be populated"
        # Must be valid JSON
        parsed = json.loads(row["dspy_example_json"])
        assert isinstance(parsed, dict), "dspy_example_json must parse to a dict"

    def test_promoted_row_has_promoted_by_auto(self, tmp_sio_db):
        """Promoted row must have promoted_by='auto'."""
        invocation_id = _insert_invocation(tmp_sio_db, user_satisfied=1, correct_outcome=1)

        from sio.core.arena.gold_standards import promote_to_gold  # noqa: PLC0415

        gold_id = promote_to_gold(invocation_id, db_path=tmp_sio_db)

        assert gold_id is not None
        row = tmp_sio_db.execute(
            "SELECT promoted_by FROM gold_standards WHERE id = ?", (gold_id,)
        ).fetchone()
        assert row is not None
        assert row["promoted_by"] == "auto", (
            f"promoted_by must be 'auto', got {row['promoted_by']!r}"
        )

    def test_promote_to_gold_idempotent(self, tmp_sio_db):
        """Calling promote_to_gold twice on same invocation creates only 1 gold row."""
        invocation_id = _insert_invocation(tmp_sio_db, user_satisfied=1, correct_outcome=1)

        from sio.core.arena.gold_standards import promote_to_gold  # noqa: PLC0415

        gold_id_1 = promote_to_gold(invocation_id, db_path=tmp_sio_db)
        gold_id_2 = promote_to_gold(invocation_id, db_path=tmp_sio_db)

        assert gold_id_1 is not None
        # Second call should either return None or the same ID — no duplicate row
        count = tmp_sio_db.execute(
            "SELECT COUNT(*) FROM gold_standards WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()[0]
        assert count == 1, (
            f"Expected exactly 1 gold row after duplicate promote calls, got {count}"
        )
