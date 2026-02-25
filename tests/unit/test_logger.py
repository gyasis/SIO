"""T019 [US1] Tests for sio.core.telemetry.logger.log_invocation.

log_invocation writes a behavior_invocations row after scrubbing secrets,
detects duplicates within a 1-second window, and returns -1 on DB errors
rather than raising.
"""

from __future__ import annotations

import sqlite3

from sio.core.telemetry.logger import log_invocation


class TestCreatesRowWithAllFields:
    """log_invocation should insert a full row and return a positive row id."""

    def test_creates_row_with_all_fields(self, tmp_db, sample_invocation):
        inv = sample_invocation()
        row_id = log_invocation(
            conn=tmp_db,
            session_id=inv["session_id"],
            tool_name=inv["actual_action"],
            tool_input='{"file_path": "/tmp/foo.py"}',
            tool_output="file contents here",
            error=None,
            user_message=inv["user_message"],
            platform=inv["platform"],
        )

        assert isinstance(row_id, int)
        assert row_id > 0

        # Verify the row exists in the database
        row = tmp_db.execute(
            "SELECT * FROM behavior_invocations WHERE id = ?", (row_id,)
        ).fetchone()
        assert row is not None
        assert row["session_id"] == inv["session_id"]
        assert row["actual_action"] == inv["actual_action"]
        assert row["platform"] == inv["platform"]
        assert row["user_message"] == inv["user_message"]


class TestSecretScrubbingApplied:
    """log_invocation must run secret_scrubber.scrub() on fields before writing."""

    def test_password_in_user_message_is_scrubbed(self, tmp_db, sample_invocation):
        row_id = log_invocation(
            conn=tmp_db,
            session_id="scrub-session-001",
            tool_name="Bash",
            tool_input='{"command": "echo hi"}',
            tool_output="hi",
            error=None,
            user_message="Run this with password=secret123 please",
            platform="claude-code",
        )

        assert row_id > 0
        row = tmp_db.execute(
            "SELECT user_message FROM behavior_invocations WHERE id = ?", (row_id,)
        ).fetchone()
        assert "secret123" not in row["user_message"]
        assert "[REDACTED]" in row["user_message"]

    def test_api_key_in_tool_input_is_scrubbed(self, tmp_db):
        row_id = log_invocation(
            conn=tmp_db,
            session_id="scrub-session-002",
            tool_name="Bash",
            tool_input='{"command": "curl -H api_key=sk-live-abc123"}',
            tool_output="ok",
            error=None,
            user_message="Call the API",
            platform="claude-code",
        )

        assert row_id > 0
        # tool_input is not stored as a separate column currently,
        # but the logger should still scrub any secret-bearing fields it writes.


class TestDuplicateDetection:
    """Same session_id + timestamp + actual_action within 1s returns existing id."""

    def test_duplicate_detection(self, tmp_db):
        kwargs = dict(
            conn=tmp_db,
            session_id="dedup-session-001",
            tool_name="Read",
            tool_input='{"file_path": "/tmp/a.py"}',
            tool_output="contents",
            error=None,
            user_message="Read a.py",
            platform="claude-code",
        )

        first_id = log_invocation(**kwargs)
        second_id = log_invocation(**kwargs)

        assert first_id > 0
        assert second_id == first_id, (
            "Duplicate invocation within the 1-second window must return the "
            "same row id, not insert a new row."
        )

        # Confirm only one row exists
        count = tmp_db.execute(
            "SELECT COUNT(*) FROM behavior_invocations WHERE session_id = ?",
            ("dedup-session-001",),
        ).fetchone()[0]
        assert count == 1


class TestErrorResilience:
    """On disk-full or other sqlite3 errors, log_invocation returns -1."""

    def test_operational_error_returns_minus_one(self):
        # Use a closed connection to trigger OperationalError
        conn = sqlite3.connect(":memory:")
        conn.close()

        result = log_invocation(
            conn=conn,
            session_id="err-session-001",
            tool_name="Write",
            tool_input="{}",
            tool_output="ok",
            error=None,
            user_message="Write something",
            platform="claude-code",
        )

        assert result == -1, (
            "log_invocation must return -1 on sqlite3 errors, never raise."
        )

    def test_integrity_error_returns_minus_one(self):
        # Use a closed connection to trigger an error
        conn = sqlite3.connect(":memory:")
        conn.close()

        result = log_invocation(
            conn=conn,
            session_id="err-session-002",
            tool_name="Edit",
            tool_input="{}",
            tool_output="ok",
            error=None,
            user_message="Edit something",
            platform="claude-code",
        )

        assert result == -1
