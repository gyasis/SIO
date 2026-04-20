"""Failing tests for _heartbeat.py — T019 (TDD red).

Tests assert (per contracts/hook-heartbeat.md §6):
  1. record_success increments total_invocations, sets last_success, resets consecutive_failures
  2. record_failure increments consecutive_failures, records last_error_message
  3. Three consecutive failures → consecutive_failures == 3
  4. Success after failures → consecutive_failures resets to 0
  5. Atomic JSON write: simulate crash via monkeypatch — previous valid JSON still readable
  6. schema_version: 1 present in output

Run to confirm RED before implementing _heartbeat.py:
    uv run pytest tests/unit/hooks/test_heartbeat.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _import_heartbeat():
    from sio.adapters.claude_code.hooks import _heartbeat  # noqa: PLC0415
    return _heartbeat


def _import_record_success():
    from sio.adapters.claude_code.hooks._heartbeat import record_success  # noqa: PLC0415
    return record_success


def _import_record_failure():
    from sio.adapters.claude_code.hooks._heartbeat import record_failure  # noqa: PLC0415
    return record_failure


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def health_file(tmp_path: Path, monkeypatch) -> Path:
    """Point HEALTH_FILE to a tmp path for isolation."""
    hf = tmp_path / "hook_health.json"
    hb = _import_heartbeat()
    monkeypatch.setattr(hb, "HEALTH_FILE", hf)
    return hf


# ---------------------------------------------------------------------------
# 1. record_success — increments counter, sets last_success, resets failures
# ---------------------------------------------------------------------------

def test_record_success_increments_total_invocations(health_file: Path):
    """record_success increments total_invocations on each call."""
    record_success = _import_record_success()

    record_success("post_tool_use", session_id="s1")
    data = json.loads(health_file.read_text())
    assert data["hooks"]["post_tool_use"]["total_invocations"] == 1

    record_success("post_tool_use", session_id="s2")
    data = json.loads(health_file.read_text())
    assert data["hooks"]["post_tool_use"]["total_invocations"] == 2


def test_record_success_sets_last_success(health_file: Path):
    """record_success sets last_success to a non-None ISO string."""
    record_success = _import_record_success()

    record_success("post_tool_use", session_id="abc")
    data = json.loads(health_file.read_text())
    h = data["hooks"]["post_tool_use"]
    assert h["last_success"] is not None
    assert "T" in h["last_success"], f"last_success looks non-ISO: {h['last_success']}"


def test_record_success_resets_consecutive_failures(health_file: Path):
    """record_success resets consecutive_failures to 0."""
    record_failure = _import_record_failure()
    record_success = _import_record_success()

    # First cause some failures
    record_failure("post_tool_use", Exception("oops"))
    record_failure("post_tool_use", Exception("again"))

    data = json.loads(health_file.read_text())
    assert data["hooks"]["post_tool_use"]["consecutive_failures"] == 2

    # Now a success should reset it
    record_success("post_tool_use", session_id="s1")
    data = json.loads(health_file.read_text())
    assert data["hooks"]["post_tool_use"]["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# 2. record_failure — increments consecutive_failures, records error message
# ---------------------------------------------------------------------------

def test_record_failure_increments_consecutive_failures(health_file: Path):
    """record_failure increments consecutive_failures on each call."""
    record_failure = _import_record_failure()

    record_failure("post_tool_use", ValueError("bad"))
    data = json.loads(health_file.read_text())
    assert data["hooks"]["post_tool_use"]["consecutive_failures"] == 1

    record_failure("post_tool_use", RuntimeError("worse"))
    data = json.loads(health_file.read_text())
    assert data["hooks"]["post_tool_use"]["consecutive_failures"] == 2


def test_record_failure_records_error_message(health_file: Path):
    """record_failure stores 'ExceptionType: message' as last_error_message."""
    record_failure = _import_record_failure()

    record_failure("post_tool_use", Exception("boom"))
    data = json.loads(health_file.read_text())
    h = data["hooks"]["post_tool_use"]
    assert h["last_error_message"] == "Exception: boom", (
        f"Unexpected last_error_message: {h['last_error_message']!r}"
    )


def test_record_failure_sets_last_error_timestamp(health_file: Path):
    """record_failure sets last_error to a non-None ISO timestamp."""
    record_failure = _import_record_failure()

    record_failure("stop", RuntimeError("crash"))
    data = json.loads(health_file.read_text())
    h = data["hooks"]["stop"]
    assert h["last_error"] is not None
    assert "T" in h["last_error"]


# ---------------------------------------------------------------------------
# 3. Three consecutive failures → consecutive_failures == 3
# ---------------------------------------------------------------------------

def test_three_consecutive_failures(health_file: Path):
    """After 3 consecutive record_failure calls, consecutive_failures == 3."""
    record_failure = _import_record_failure()

    for i in range(3):
        record_failure("pre_compact", RuntimeError(f"fail {i}"))

    data = json.loads(health_file.read_text())
    assert data["hooks"]["pre_compact"]["consecutive_failures"] == 3


# ---------------------------------------------------------------------------
# 4. Success after failures → consecutive_failures resets to 0
# ---------------------------------------------------------------------------

def test_success_after_failures_resets_counter(health_file: Path):
    """Success after failures resets consecutive_failures to 0 exactly."""
    record_failure = _import_record_failure()
    record_success = _import_record_success()

    for _ in range(5):
        record_failure("stop", OSError("disk full"))

    record_success("stop", session_id="recovery")
    data = json.loads(health_file.read_text())
    assert data["hooks"]["stop"]["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# 5. total_invocations monotonic across success + failure calls
# ---------------------------------------------------------------------------

def test_total_invocations_monotonic(health_file: Path):
    """total_invocations increments on both success and failure calls."""
    record_success = _import_record_success()
    record_failure = _import_record_failure()

    record_success("post_tool_use", session_id="a")
    record_failure("post_tool_use", Exception("b"))
    record_success("post_tool_use", session_id="c")

    data = json.loads(health_file.read_text())
    assert data["hooks"]["post_tool_use"]["total_invocations"] == 3


# ---------------------------------------------------------------------------
# 6. schema_version: 1 in output JSON
# ---------------------------------------------------------------------------

def test_schema_version_in_output(health_file: Path):
    """The output JSON must contain schema_version == 1."""
    record_success = _import_record_success()

    record_success("post_tool_use", session_id="x")
    data = json.loads(health_file.read_text())
    assert data.get("schema_version") == 1, (
        f"Expected schema_version=1, got: {data.get('schema_version')!r}"
    )


# ---------------------------------------------------------------------------
# 7. updated_at is set at top level
# ---------------------------------------------------------------------------

def test_updated_at_present(health_file: Path):
    """The output JSON must contain a top-level updated_at field."""
    record_success = _import_record_success()

    record_success("post_tool_use", session_id="y")
    data = json.loads(health_file.read_text())
    assert "updated_at" in data, "Top-level 'updated_at' missing from hook_health.json"


# ---------------------------------------------------------------------------
# 8. Atomic write: crash during _update leaves previous valid JSON intact
# ---------------------------------------------------------------------------

def test_atomic_write_crash_leaves_previous_valid_json(
    health_file: Path, monkeypatch
):
    """If os.replace crashes mid-write, the previous valid JSON is still readable."""
    record_success = _import_record_success()

    # Establish a valid baseline
    record_success("post_tool_use", session_id="baseline")
    original_data = json.loads(health_file.read_text())
    assert original_data["hooks"]["post_tool_use"]["total_invocations"] == 1

    # Monkeypatch os.replace to raise an exception (simulating crash)
    def crashing_replace(src, dst):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", crashing_replace)

    # The second call should fail silently (heartbeat failures must be swallowed)
    # or raise — either way the previous valid JSON must still be readable
    try:
        record_success("post_tool_use", session_id="crash_attempt")
    except Exception:
        pass  # Heartbeat may or may not swallow the error

    # Previous valid JSON must still be readable and correct
    still_valid = json.loads(health_file.read_text())
    assert still_valid["hooks"]["post_tool_use"]["total_invocations"] == 1, (
        "Previous valid JSON was corrupted by the crash"
    )


# ---------------------------------------------------------------------------
# 9. Multiple hook types tracked independently
# ---------------------------------------------------------------------------

def test_multiple_hook_types_tracked_independently(health_file: Path):
    """Different hook names are tracked in separate hook entries."""
    record_success = _import_record_success()
    record_failure = _import_record_failure()

    record_success("post_tool_use", session_id="a")
    record_failure("stop", Exception("stop error"))
    record_success("pre_compact", session_id="b")

    data = json.loads(health_file.read_text())
    hooks = data["hooks"]

    assert "post_tool_use" in hooks
    assert "stop" in hooks
    assert "pre_compact" in hooks

    assert hooks["post_tool_use"]["consecutive_failures"] == 0
    assert hooks["stop"]["consecutive_failures"] == 1
    assert hooks["pre_compact"]["consecutive_failures"] == 0
