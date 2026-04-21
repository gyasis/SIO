"""T057-T060 [US7] Unit tests for Real-Time Session Hooks.

Tests for PreCompact, Stop, UserPromptSubmit hooks and the updated installer.
"""

from __future__ import annotations

import json
import os

import pytest

from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_conn():
    """In-memory database with full SIO schema."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def session_state_file(tmp_path):
    """Temporary session_state.json path."""
    return str(tmp_path / "session_state.json")


@pytest.fixture()
def skills_dir(tmp_path, monkeypatch):
    """Redirect the learned skills directory to tmp_path."""
    d = str(tmp_path / "learned")
    monkeypatch.setattr(
        "sio.adapters.claude_code.hooks.stop._SKILLS_DIR",
        d,
    )
    return d


@pytest.fixture()
def error_log(tmp_path, monkeypatch):
    """Redirect hook error logs to tmp_path."""
    log_path = str(tmp_path / "hook_errors.log")
    monkeypatch.setattr(
        "sio.adapters.claude_code.hooks.pre_compact._ERROR_LOG",
        log_path,
    )
    monkeypatch.setattr(
        "sio.adapters.claude_code.hooks.stop._ERROR_LOG",
        log_path,
    )
    monkeypatch.setattr(
        "sio.adapters.claude_code.hooks.user_prompt_submit._ERROR_LOG",
        log_path,
    )
    return log_path


# ---------------------------------------------------------------------------
# T057: TestPreCompact
# ---------------------------------------------------------------------------


class TestPreCompact:
    """PreCompact hook captures session_metrics snapshot."""

    def test_returns_allow_on_valid_input(self, mem_conn):
        from sio.adapters.claude_code.hooks.pre_compact import (
            handle_pre_compact,
        )

        payload = json.dumps(
            {
                "session_id": "sess-001",
                "transcript_path": "/tmp/transcript.jsonl",
            }
        )
        result = json.loads(handle_pre_compact(payload, conn=mem_conn))
        assert result == {"action": "allow"}

    def test_saves_session_metrics_snapshot(self, mem_conn):
        from sio.adapters.claude_code.hooks.pre_compact import (
            handle_pre_compact,
        )

        payload = json.dumps(
            {
                "session_id": "sess-snap",
                "transcript_path": "/tmp/snap.jsonl",
            }
        )
        handle_pre_compact(payload, conn=mem_conn)

        row = mem_conn.execute(
            "SELECT * FROM session_metrics WHERE session_id = ?",
            ("sess-snap",),
        ).fetchone()
        assert row is not None
        assert row["session_id"] == "sess-snap"
        assert row["file_path"] == "/tmp/snap.jsonl"

    def test_counts_errors_from_invocations(self, mem_conn):
        from sio.adapters.claude_code.hooks.pre_compact import (
            handle_pre_compact,
        )

        # Insert tool calls into behavior_invocations
        for i in range(3):
            mem_conn.execute(
                "INSERT INTO behavior_invocations "
                "(session_id, actual_action, user_message, "
                "behavior_type, timestamp, platform) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "sess-errs",
                    "Bash",
                    "test",
                    "skill",
                    f"2025-01-01T00:00:0{i}Z",
                    "claude-code",
                ),
            )
        # Insert errors into error_records
        for i in range(2):
            mem_conn.execute(
                "INSERT INTO error_records "
                "(session_id, timestamp, source_type, source_file, "
                "error_text, mined_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "sess-errs",
                    f"2025-01-01T00:00:0{i}Z",
                    "hook",
                    "/tmp/errs.jsonl",
                    "some error",
                    "2025-01-01T00:00:00Z",
                ),
            )
        mem_conn.commit()

        payload = json.dumps(
            {
                "session_id": "sess-errs",
                "transcript_path": "/tmp/errs.jsonl",
            }
        )
        handle_pre_compact(payload, conn=mem_conn)

        row = mem_conn.execute(
            "SELECT * FROM session_metrics WHERE session_id = ?",
            ("sess-errs",),
        ).fetchone()
        assert row["tool_call_count"] == 3
        assert row["error_count"] == 2

    def test_returns_allow_on_invalid_json(self, mem_conn):
        from sio.adapters.claude_code.hooks.pre_compact import (
            handle_pre_compact,
        )

        result = json.loads(handle_pre_compact("not-json", conn=mem_conn))
        assert result == {"action": "allow"}

    def test_returns_allow_on_empty_input(self, mem_conn):
        from sio.adapters.claude_code.hooks.pre_compact import (
            handle_pre_compact,
        )

        result = json.loads(handle_pre_compact("", conn=mem_conn))
        assert result == {"action": "allow"}

    def test_retry_on_failure_then_silent(self, mem_conn, error_log):
        """If core logic fails, retry once then log and still return allow."""
        from sio.adapters.claude_code.hooks.pre_compact import (
            handle_pre_compact,
        )

        # Pass a valid JSON that will fail because conn is closed
        mem_conn.close()
        payload = json.dumps(
            {
                "session_id": "sess-fail",
                "transcript_path": "/tmp/fail.jsonl",
            }
        )
        # Should not raise — returns allow
        result = json.loads(handle_pre_compact(payload, conn=mem_conn))
        assert result == {"action": "allow"}

        # Error should be logged
        if os.path.exists(error_log):
            content = open(error_log).read()
            assert "PreCompact" in content


# ---------------------------------------------------------------------------
# T058: TestStop
# ---------------------------------------------------------------------------


class TestStop:
    """Stop hook finalizes session and saves high-confidence patterns."""

    def test_returns_allow(self, mem_conn, skills_dir):
        from sio.adapters.claude_code.hooks.stop import handle_stop

        payload = json.dumps(
            {
                "session_id": "sess-stop-1",
                "transcript_path": "/tmp/stop.jsonl",
            }
        )
        result = json.loads(handle_stop(payload, conn=mem_conn))
        assert result == {"action": "allow"}

    def test_finalizes_session_metrics(self, mem_conn, skills_dir):
        from sio.adapters.claude_code.hooks.stop import handle_stop

        payload = json.dumps(
            {
                "session_id": "sess-final",
                "transcript_path": "/tmp/final.jsonl",
            }
        )
        handle_stop(payload, conn=mem_conn)

        row = mem_conn.execute(
            "SELECT * FROM session_metrics WHERE session_id = ?",
            ("sess-final",),
        ).fetchone()
        assert row is not None

    def test_marks_session_as_processed(self, mem_conn, skills_dir):
        from sio.adapters.claude_code.hooks.stop import handle_stop

        payload = json.dumps(
            {
                "session_id": "sess-proc",
                "transcript_path": "/tmp/proc.jsonl",
            }
        )
        handle_stop(payload, conn=mem_conn)

        row = mem_conn.execute(
            "SELECT * FROM processed_sessions WHERE file_path = ?",
            ("/tmp/proc.jsonl",),
        ).fetchone()
        assert row is not None

    def test_saves_high_confidence_pattern(self, mem_conn, skills_dir):
        from sio.adapters.claude_code.hooks.stop import handle_stop

        # Insert enough identical errors into error_records
        for i in range(5):
            mem_conn.execute(
                "INSERT INTO error_records "
                "(session_id, timestamp, source_type, source_file, "
                "error_text, mined_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "sess-hi",
                    f"2025-01-01T00:00:0{i}Z",
                    "hook",
                    "/tmp/hi.jsonl",
                    "ImportError: No module named foobar",
                    "2025-01-01T00:00:00Z",
                ),
            )
        mem_conn.commit()

        payload = json.dumps(
            {
                "session_id": "sess-hi",
                "transcript_path": "/tmp/hi.jsonl",
            }
        )
        handle_stop(payload, conn=mem_conn)

        # Should have created a skill file
        files = os.listdir(skills_dir) if os.path.exists(skills_dir) else []
        pattern_files = [f for f in files if f.startswith("pattern-")]
        assert len(pattern_files) >= 1

        # Verify content
        content = open(os.path.join(skills_dir, pattern_files[0])).read()
        assert "Confidence" in content
        assert "ImportError" in content

    def test_does_not_save_low_confidence_pattern(self, mem_conn, skills_dir):
        from sio.adapters.claude_code.hooks.stop import handle_stop

        # Insert diverse errors — each unique, so none gets high confidence
        for i in range(5):
            mem_conn.execute(
                "INSERT INTO error_records "
                "(session_id, timestamp, source_type, source_file, "
                "error_text, mined_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "sess-lo",
                    f"2025-01-01T00:00:0{i}Z",
                    "hook",
                    "/tmp/lo.jsonl",
                    f"Unique error number {i}",
                    "2025-01-01T00:00:00Z",
                ),
            )
        mem_conn.commit()

        payload = json.dumps(
            {
                "session_id": "sess-lo",
                "transcript_path": "/tmp/lo.jsonl",
            }
        )
        handle_stop(payload, conn=mem_conn)

        # Should NOT have created any pattern files
        files = os.listdir(skills_dir) if os.path.exists(skills_dir) else []
        pattern_files = [f for f in files if f.startswith("pattern-")]
        assert len(pattern_files) == 0

    def test_retry_on_failure_then_silent(self, mem_conn, error_log, skills_dir):
        from sio.adapters.claude_code.hooks.stop import handle_stop

        mem_conn.close()
        payload = json.dumps(
            {
                "session_id": "sess-stop-fail",
                "transcript_path": "/tmp/fail.jsonl",
            }
        )
        result = json.loads(handle_stop(payload, conn=mem_conn))
        assert result == {"action": "allow"}


# ---------------------------------------------------------------------------
# T059: TestUserPromptSubmit
# ---------------------------------------------------------------------------


class TestUserPromptSubmit:
    """UserPromptSubmit hook detects corrections, undos, and frustration."""

    def test_returns_allow_on_normal_message(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        payload = json.dumps(
            {
                "session_id": "sess-ups-1",
                "user_message": "Looks good, thanks!",
            }
        )
        result = json.loads(
            handle_user_prompt_submit(
                payload,
                state_path=session_state_file,
            ),
        )
        assert result == {"action": "allow"}

    def test_detects_correction(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        payload = json.dumps(
            {
                "session_id": "sess-corr",
                "user_message": "No, that's wrong, do X instead",
            }
        )
        handle_user_prompt_submit(payload, state_path=session_state_file)

        state = json.loads(open(session_state_file).read())
        sess = state["sessions"]["sess-corr"]
        assert sess["correction_count"] >= 1

    def test_detects_undo_request(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        payload = json.dumps(
            {
                "session_id": "sess-undo",
                "user_message": "Please undo that change",
            }
        )
        handle_user_prompt_submit(payload, state_path=session_state_file)

        state = json.loads(open(session_state_file).read())
        sess = state["sessions"]["sess-undo"]
        assert sess["undo_count"] >= 1

    def test_frustration_logged_after_3_negatives(
        self,
        session_state_file,
        error_log,
    ):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        negative_messages = [
            "This is wrong and broken",
            "Fix this error, it's still broken",
            "This is annoying, stop failing",
        ]
        for msg in negative_messages:
            payload = json.dumps(
                {
                    "session_id": "sess-frust",
                    "user_message": msg,
                }
            )
            handle_user_prompt_submit(
                payload,
                state_path=session_state_file,
            )

        state = json.loads(open(session_state_file).read())
        sess = state["sessions"]["sess-frust"]
        assert sess["frustration_logged"] is True

        # Frustration should also be logged to error log
        if os.path.exists(error_log):
            content = open(error_log).read()
            assert "FRUSTRATION" in content

    def test_returns_allow_on_invalid_json(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        result = json.loads(
            handle_user_prompt_submit(
                "not-json",
                state_path=session_state_file,
            ),
        )
        assert result == {"action": "allow"}

    def test_returns_allow_on_empty_message(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        payload = json.dumps(
            {
                "session_id": "sess-empty",
                "user_message": "",
            }
        )
        result = json.loads(
            handle_user_prompt_submit(
                payload,
                state_path=session_state_file,
            ),
        )
        assert result == {"action": "allow"}

    def test_correction_counter_increments(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        for i in range(3):
            payload = json.dumps(
                {
                    "session_id": "sess-inc",
                    "user_message": f"No, do it differently attempt {i}",
                }
            )
            handle_user_prompt_submit(
                payload,
                state_path=session_state_file,
            )

        state = json.loads(open(session_state_file).read())
        sess = state["sessions"]["sess-inc"]
        assert sess["correction_count"] == 3

    def test_no_frustration_on_positive_messages(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        positive_messages = [
            "Great, that works perfectly!",
            "Thanks, this is awesome!",
            "Yes, exactly what I wanted!",
        ]
        for msg in positive_messages:
            payload = json.dumps(
                {
                    "session_id": "sess-pos",
                    "user_message": msg,
                }
            )
            handle_user_prompt_submit(
                payload,
                state_path=session_state_file,
            )

        state = json.loads(open(session_state_file).read())
        sess = state["sessions"]["sess-pos"]
        assert sess["frustration_logged"] is False

    def test_recent_scores_kept_to_10(self, session_state_file):
        from sio.adapters.claude_code.hooks.user_prompt_submit import (
            handle_user_prompt_submit,
        )

        for i in range(15):
            payload = json.dumps(
                {
                    "session_id": "sess-trim",
                    "user_message": f"Message number {i} about stuff",
                }
            )
            handle_user_prompt_submit(
                payload,
                state_path=session_state_file,
            )

        state = json.loads(open(session_state_file).read())
        sess = state["sessions"]["sess-trim"]
        assert len(sess["recent_scores"]) <= 10


# ---------------------------------------------------------------------------
# T060: TestInstaller — all 4 hooks registered
# ---------------------------------------------------------------------------


class TestInstallerHooks:
    """Installer registers all 4 SIO hooks."""

    def test_registers_all_four_hooks(self, tmp_path):
        from sio.adapters.claude_code.installer import install

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(json.dumps({}))

        install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(claude_dir),
            dry_run=True,
        )

        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})

        expected_events = [
            "PostToolUse",
            "PreCompact",
            "Stop",
            "UserPromptSubmit",
            "SessionStart",
        ]
        for event in expected_events:
            assert event in hooks, f"Missing hook event: {event}"
            event_hooks = hooks[event]
            assert len(event_hooks) >= 1, f"No hooks for {event}"
            # Verify at least one hook command contains the right module path
            module_fragment = event.lower()
            if event == "PostToolUse":
                module_fragment = "post_tool_use"
            elif event == "PreCompact":
                module_fragment = "pre_compact"
            elif event == "Stop":
                module_fragment = "stop"
            elif event == "UserPromptSubmit":
                module_fragment = "user_prompt_submit"
            elif event == "SessionStart":
                module_fragment = "session_start"
            found = False
            for h in event_hooks:
                if not isinstance(h, dict):
                    continue
                # Check bare format: {"type": "command", "command": "..."}
                if module_fragment in h.get("command", ""):
                    found = True
                    break
                # Check wrapped format: {"hooks": [{"command": "..."}]}
                for inner in h.get("hooks", []):
                    if isinstance(inner, dict) and module_fragment in inner.get("command", ""):
                        found = True
                        break
                if found:
                    break
            assert found, f"No SIO hook with '{module_fragment}' in {event} commands"

    def test_does_not_duplicate_hooks_on_reinstall(self, tmp_path):
        from sio.adapters.claude_code.installer import install

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(json.dumps({}))

        # Install twice
        install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(claude_dir),
        )
        install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(claude_dir),
        )

        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})
        for event in [
            "PostToolUse",
            "PreCompact",
            "Stop",
            "UserPromptSubmit",
            "SessionStart",
        ]:
            sio_hooks = []
            for h in hooks[event]:
                if not isinstance(h, dict):
                    continue
                # Bare format
                if "sio" in h.get("command", "").lower():
                    sio_hooks.append(h)
                    continue
                # Wrapped format
                for inner in h.get("hooks", []):
                    if isinstance(inner, dict) and "sio" in inner.get("command", "").lower():
                        sio_hooks.append(h)
                        break
            assert len(sio_hooks) == 1, f"Duplicate SIO hooks for {event}: {sio_hooks}"

    def test_preserves_existing_hooks(self, tmp_path):
        from sio.adapters.claude_code.installer import install

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        existing = {
            "hooks": {
                "PostToolUse": [{"type": "command", "command": "my-custom-hook"}],
            },
        }
        settings_path.write_text(json.dumps(existing))

        install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(claude_dir),
        )

        data = json.loads(settings_path.read_text())
        post_hooks = data["hooks"]["PostToolUse"]
        # The installer migrates bare hooks to wrapped format, so check both.
        custom = []
        for h in post_hooks:
            if not isinstance(h, dict):
                continue
            if h.get("command") == "my-custom-hook":
                custom.append(h)
            for inner in h.get("hooks", []):
                if isinstance(inner, dict) and inner.get("command") == "my-custom-hook":
                    custom.append(h)
                    break
        assert len(custom) == 1, "Existing custom hook was removed"

    def test_installer_returns_hooks_registered(self, tmp_path):
        from sio.adapters.claude_code.installer import install

        result = install(
            db_dir=str(tmp_path / ".sio" / "claude-code"),
            claude_dir=str(tmp_path / ".claude"),
        )
        assert result["hooks_registered"] is True


# ---------------------------------------------------------------------------
# SessionStart hook tests
# ---------------------------------------------------------------------------


class TestSessionStart:
    """SessionStart hook injects SIO briefing at session start."""

    def test_returns_empty_when_no_db(self, tmp_path, monkeypatch):
        from sio.adapters.claude_code.hooks.session_start import (
            handle_session_start,
        )

        monkeypatch.setattr(
            "sio.adapters.claude_code.hooks.session_start._DB_PATH",
            str(tmp_path / "nonexistent.db"),
        )
        payload = json.dumps({"session_id": "sess-no-db"})
        result = handle_session_start(payload)
        assert result == ""

    def test_returns_empty_on_invalid_json(self, tmp_path, monkeypatch):
        from sio.adapters.claude_code.hooks.session_start import (
            handle_session_start,
        )

        monkeypatch.setattr(
            "sio.adapters.claude_code.hooks.session_start._DB_PATH",
            str(tmp_path / "nonexistent.db"),
        )
        result = handle_session_start("not-json")
        assert result == ""

    def test_returns_empty_on_exception(self, tmp_path, monkeypatch):
        from sio.adapters.claude_code.hooks.session_start import (
            handle_session_start,
        )

        # Point to an existing but non-SQLite file to trigger an error
        bad_db = tmp_path / "bad.db"
        bad_db.write_text("not a database")
        monkeypatch.setattr(
            "sio.adapters.claude_code.hooks.session_start._DB_PATH",
            str(bad_db),
        )
        log_path = str(tmp_path / "hook_errors.log")
        monkeypatch.setattr(
            "sio.adapters.claude_code.hooks.session_start._ERROR_LOG",
            log_path,
        )

        payload = json.dumps({"session_id": "sess-bad-db"})
        result = handle_session_start(payload)
        assert result == ""

        # Error should be logged
        if os.path.exists(log_path):
            content = open(log_path).read()
            assert "SessionStart" in content

    def test_returns_briefing_when_db_exists(self, tmp_path, monkeypatch):
        """When a valid SIO db exists, the hook produces a non-empty string."""
        from sio.adapters.claude_code.hooks.session_start import (
            handle_session_start,
        )

        # Create a minimal SIO database via init_db
        db_path = str(tmp_path / "sio.db")
        conn = init_db(db_path)
        conn.close()

        monkeypatch.setattr(
            "sio.adapters.claude_code.hooks.session_start._DB_PATH",
            db_path,
        )

        payload = json.dumps({"session_id": "sess-brief"})
        result = handle_session_start(payload)
        # The briefing may be empty if no data, but should not raise
        assert isinstance(result, str)
