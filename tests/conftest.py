"""Shared pytest fixtures for SIO test suite."""

from datetime import datetime, timezone

import pytest


@pytest.fixture
def tmp_db():
    """In-memory SQLite database with SIO schema applied."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def sample_invocation():
    """Factory for creating sample BehaviorInvocation dicts."""

    def _make(
        session_id="test-session-001",
        platform="claude-code",
        tool_name="Read",
        user_message="Read the file foo.py",
        tool_input='{"file_path": "/tmp/foo.py"}',
        tool_output="file contents here",
        error=None,
        behavior_type="skill",
        **overrides,
    ):
        record = {
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": platform,
            "user_message": user_message,
            "behavior_type": behavior_type,
            "actual_action": tool_name,
            "expected_action": None,
            "activated": 1,
            "correct_action": 1,
            "correct_outcome": 1,
            "user_satisfied": None,
            "user_note": None,
            "passive_signal": None,
            "history_file": None,
            "line_start": None,
            "line_end": None,
            "token_count": None,
            "latency_ms": None,
            "labeled_by": None,
            "labeled_at": None,
        }
        record.update(overrides)
        return record

    return _make


@pytest.fixture
def mock_platform_config():
    """Mock platform configuration for testing."""
    return {
        "platform": "claude-code",
        "db_path": ":memory:",
        "hooks_installed": 1,
        "skills_installed": 1,
        "config_updated": 1,
        "capability_tier": 1,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "last_verified": None,
    }
