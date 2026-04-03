"""Tests for sio.suggestions.consultant — session-start briefing builder."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from sio.core.config import SIOConfig
from sio.core.db.schema import init_db
from sio.suggestions.consultant import build_session_briefing


@pytest.fixture()
def db():
    """In-memory SIO database with schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def config():
    """Default SIO config."""
    return SIOConfig()


def _insert_error(db: sqlite3.Connection, **overrides) -> None:
    """Insert a minimal error_record row."""
    defaults = {
        "session_id": "sess-1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_type": "jsonl",
        "source_file": "/tmp/test.jsonl",
        "tool_name": "Bash",
        "error_text": "command failed",
        "error_type": "bash_error",
        "mined_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    db.execute(
        f"INSERT INTO error_records ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )
    db.commit()


def _insert_suggestion(
    db: sqlite3.Connection,
    *,
    status: str = "pending",
    confidence: float = 0.85,
    description: str = "Fix repeated bash errors",
) -> int:
    """Insert a minimal suggestion row, return its id."""
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO suggestions "
        "(description, confidence, proposed_change, target_file, "
        "change_type, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (description, confidence, "- Never run X", "CLAUDE.md",
         "append", status, now),
    )
    db.commit()
    return cur.lastrowid


def _insert_session_metric(
    db: sqlite3.Connection,
    session_id: str,
    error_count: int,
) -> None:
    """Insert a minimal session_metrics row."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO session_metrics "
        "(session_id, file_path, error_count, mined_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, "/tmp/s.jsonl", error_count, now),
    )
    db.commit()


def _insert_velocity_snapshot(
    db: sqlite3.Connection,
    error_type: str,
    error_rate: float,
    rule_applied: int = 1,
    created_at: str | None = None,
) -> None:
    """Insert a velocity_snapshots row."""
    now = created_at or datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO velocity_snapshots "
        "(error_type, session_id, error_rate, error_count_in_window, "
        "window_start, window_end, rule_applied, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (error_type, "sess-1", error_rate, 5,
         "2026-01-01T00:00:00", "2026-01-07T00:00:00",
         rule_applied, now),
    )
    db.commit()


class TestEmptyDB:
    """When the database has no data at all."""

    def test_empty_db_returns_all_clear(self, db, config):
        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)
        assert "All clear" in result

    def test_empty_db_no_crash(self, db, config):
        """Ensure no exceptions on a freshly initialized empty DB."""
        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)
        assert isinstance(result, str)
        assert len(result) > 0


class TestViolations:
    """When there are recent violations in the DB."""

    def test_violations_appear_in_briefing(self, db, config, tmp_path):
        """Errors matching a rule file should produce a violations section."""
        # Create a rule file with a NEVER rule
        rule_file = tmp_path / "CLAUDE.md"
        rule_file.write_text("- NEVER use SELECT *\n")

        # Insert an error that matches
        _insert_error(
            db,
            error_text="query used SELECT * FROM users",
            error_type="bad_query",
        )

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[str(rule_file)],
        ):
            result = build_session_briefing(db, config=config)

        assert "Violation" in result


class TestBudgetWarning:
    """When CLAUDE.md is near capacity."""

    def test_budget_warning_at_95_percent(self, db, config, tmp_path):
        """CLAUDE.md at 95% capacity should trigger a budget warning."""
        # config default cap is 100 lines; write 95 meaningful lines
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("\n".join(f"Rule line {i}" for i in range(95)))

        # Patch budget check to use our temp file
        with (
            patch(
                "sio.suggestions.consultant._get_rule_file_paths",
                return_value=[],
            ),
            patch(
                "sio.suggestions.consultant.Path.home",
                return_value=tmp_path,
            ),
            patch(
                "sio.suggestions.consultant.Path.cwd",
                return_value=tmp_path,
            ),
        ):
            result = build_session_briefing(db, config=config)

        assert "Budget" in result
        assert "95" in result or "capacity" in result.lower()

    def test_no_budget_warning_when_low(self, db, config, tmp_path):
        """CLAUDE.md at 20% capacity should not trigger a warning."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("\n".join(f"Rule line {i}" for i in range(20)))

        with (
            patch(
                "sio.suggestions.consultant._get_rule_file_paths",
                return_value=[],
            ),
            patch(
                "sio.suggestions.consultant.Path.home",
                return_value=tmp_path,
            ),
            patch(
                "sio.suggestions.consultant.Path.cwd",
                return_value=tmp_path,
            ),
        ):
            result = build_session_briefing(db, config=config)

        assert "Budget" not in result


class TestPendingSuggestions:
    """When there are pending high-confidence suggestions."""

    def test_pending_high_confidence_shown(self, db, config):
        _insert_suggestion(db, status="pending", confidence=0.9)

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)

        assert "Pending" in result
        assert "90%" in result

    def test_low_confidence_not_shown(self, db, config):
        _insert_suggestion(db, status="pending", confidence=0.3)

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)

        assert "Pending" not in result

    def test_applied_suggestions_not_shown(self, db, config):
        _insert_suggestion(db, status="applied", confidence=0.95)

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)

        assert "Pending" not in result


class TestSessionStats:
    """When session_metrics has error count data."""

    def test_session_trend_shown(self, db, config):
        for i, count in enumerate([10, 8, 6, 4, 2]):
            _insert_session_metric(db, f"sess-{i}", count)

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)

        assert "Trend" in result


class TestDecliningRules:
    """When velocity snapshots show error rate increasing after rule."""

    def test_declining_rule_shown(self, db, config):
        # Earlier snapshot: low rate
        earlier = (
            datetime.now(timezone.utc) - timedelta(days=5)
        ).isoformat()
        _insert_velocity_snapshot(
            db, "unused_import", 0.1, rule_applied=1, created_at=earlier,
        )
        # Later snapshot: higher rate
        later = datetime.now(timezone.utc).isoformat()
        _insert_velocity_snapshot(
            db, "unused_import", 0.5, rule_applied=1, created_at=later,
        )

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)

        assert "Declining" in result
        assert "unused_import" in result


class TestCleanDB:
    """When the DB has data but nothing actionable."""

    def test_clean_state_returns_all_clear(self, db, config):
        """Applied suggestions, no violations, low budget = all clear."""
        _insert_suggestion(db, status="applied", confidence=0.95)

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)

        assert "All clear" in result


class TestBriefnessConstraint:
    """Briefing output should stay compact."""

    def test_output_under_500_chars_typical(self, db, config):
        """With a few pending suggestions, output stays brief."""
        _insert_suggestion(db, status="pending", confidence=0.85)
        _insert_session_metric(db, "sess-1", 5)

        with patch(
            "sio.suggestions.consultant._get_rule_file_paths",
            return_value=[],
        ), patch(
            "sio.suggestions.consultant._section_budget",
            return_value=None,
        ):
            result = build_session_briefing(db, config=config)

        assert len(result) < 500, (
            f"Briefing is {len(result)} chars, expected <500"
        )
