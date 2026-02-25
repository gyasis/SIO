"""Unit tests for sio.core.telemetry.passive_signals — T039 [US3].

Tests passive signal detection: corrections, undos, and re-invocations.
These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from sio.core.db.queries import insert_invocation
from sio.core.telemetry.passive_signals import (
    detect_correction,
    detect_undo,
    detect_re_invocation,
)


class TestDetectCorrection:
    """detect_correction(message) -> bool based on leading correction markers."""

    def test_correction_detection_no_comma(self):
        assert detect_correction("No, use the other file") is True

    def test_correction_detection_actually(self):
        assert detect_correction("Actually, I meant the src directory") is True

    def test_correction_detection_instead(self):
        assert detect_correction("Instead, run the tests first") is True

    def test_correction_detection_wait(self):
        assert detect_correction("Wait, that's the wrong branch") is True

    def test_correction_detection_stop(self):
        assert detect_correction("Stop, don't commit yet") is True

    def test_correction_no_false_positive(self):
        """Words like 'Notice' should not trigger correction detection."""
        assert detect_correction("Notice the pattern") is False

    def test_correction_no_false_positive_normal_message(self):
        assert detect_correction("Read the file foo.py") is False

    def test_correction_empty_string(self):
        assert detect_correction("") is False


class TestDetectUndo:
    """detect_undo(session_id, timestamp, conn) -> bool when git checkout/revert within 30s."""

    def test_undo_detection_within_30s(self, tmp_db, sample_invocation):
        """git checkout/revert within 30s of a previous tool invocation => True."""
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(seconds=20)

        # Insert a prior invocation 20s ago
        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-undo-1",
                tool_name="Bash",
                user_message="make some changes",
                timestamp=earlier.isoformat(),
            ),
        )

        # Insert the undo invocation (git checkout)
        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-undo-1",
                tool_name="Bash",
                user_message="git checkout -- .",
                timestamp=now.isoformat(),
            ),
        )

        assert detect_undo("sess-undo-1", now.isoformat(), tmp_db) is True

    def test_undo_no_detection_after_30s(self, tmp_db, sample_invocation):
        """git revert more than 30s after previous tool => False."""
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(seconds=60)

        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-undo-2",
                tool_name="Bash",
                user_message="make some changes",
                timestamp=earlier.isoformat(),
            ),
        )

        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-undo-2",
                tool_name="Bash",
                user_message="git revert HEAD",
                timestamp=now.isoformat(),
            ),
        )

        assert detect_undo("sess-undo-2", now.isoformat(), tmp_db) is False

    def test_undo_no_detection_non_git_command(self, tmp_db, sample_invocation):
        """Non-git commands should not be detected as undo."""
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(seconds=10)

        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-undo-3",
                tool_name="Read",
                user_message="read file",
                timestamp=earlier.isoformat(),
            ),
        )

        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-undo-3",
                tool_name="Read",
                user_message="read another file",
                timestamp=now.isoformat(),
            ),
        )

        assert detect_undo("sess-undo-3", now.isoformat(), tmp_db) is False


class TestDetectReInvocation:
    """detect_re_invocation(session_id, intent, conn) -> bool when same intent invoked differently."""

    def test_re_invocation_detection(self, tmp_db, sample_invocation):
        """Same intent invoked with a different tool => True."""
        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-re-1",
                tool_name="Read",
                user_message="show me the contents of foo.py",
            ),
        )
        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-re-1",
                tool_name="Bash",
                user_message="cat foo.py",
            ),
        )

        # The intent "read foo.py" was accomplished via two different tools
        assert (
            detect_re_invocation("sess-re-1", "read foo.py", tmp_db) is True
        )

    def test_no_false_positive_sequential_calls(self, tmp_db, sample_invocation):
        """Sequential calls with different intents should not trigger."""
        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-re-2",
                tool_name="Read",
                user_message="read file A",
            ),
        )
        insert_invocation(
            tmp_db,
            sample_invocation(
                session_id="sess-re-2",
                tool_name="Read",
                user_message="read file B",
            ),
        )

        assert (
            detect_re_invocation("sess-re-2", "write file C", tmp_db) is False
        )
