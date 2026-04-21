"""T092 [US6] — sio status hook health integration tests.

Per contracts/hook-heartbeat.md §6, SC-009.

Tests the hook_health_rows() reader against various hook_health.json states,
and validates that sio status CLI completes in < 2s.

States tested:
- healthy: last_success recent, consecutive_failures=0
- warn: last_success 2h ago
- error: consecutive_failures=3
- never-seen: no heartbeat file / no entry for hook
"""

from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso(delta: timedelta | None = None) -> str:
    """Return current UTC ISO string, optionally offset by delta."""
    now = datetime.now(timezone.utc)
    if delta:
        now = now + delta
    return now.isoformat()


def _write_health_file(path: Path, hooks: dict) -> None:
    """Write a hook_health.json fixture."""
    data = {
        "schema_version": 1,
        "updated_at": _utc_iso(),
        "hooks": hooks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# T092-1: Healthy state
# ---------------------------------------------------------------------------


def test_hook_health_healthy_state():
    """Recent last_success + no failures must report 'healthy' for each hook."""
    from sio.cli.status import EXPECTED_HOOKS, hook_health_rows  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        health_file = Path(tmpdir) / ".sio" / "hook_health.json"
        hooks = {}
        for hook in EXPECTED_HOOKS:
            hooks[hook] = {
                "last_success": _utc_iso(timedelta(minutes=-5)),  # 5 min ago
                "last_error": None,
                "last_error_message": None,
                "consecutive_failures": 0,
                "total_invocations": 100,
                "last_session_id": "abc123",
            }
        _write_health_file(health_file, hooks)

        with patch("sio.cli.status.HEALTH_FILE", health_file):
            rows = hook_health_rows()

    assert len(rows) == len(EXPECTED_HOOKS), f"Expected {len(EXPECTED_HOOKS)} rows"
    for hook_name, state, detail in rows:
        assert state == "healthy", (
            f"Hook {hook_name!r} should be 'healthy' but got {state!r}: {detail}"
        )


# ---------------------------------------------------------------------------
# T092-2: Warn state (last_success 2h ago)
# ---------------------------------------------------------------------------


def test_hook_health_warn_state():
    """last_success 2h ago must report 'warn' state for each hook."""
    from sio.cli.status import EXPECTED_HOOKS, hook_health_rows  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        health_file = Path(tmpdir) / ".sio" / "hook_health.json"
        hooks = {}
        for hook in EXPECTED_HOOKS:
            hooks[hook] = {
                "last_success": _utc_iso(timedelta(hours=-2)),  # 2h ago
                "last_error": None,
                "last_error_message": None,
                "consecutive_failures": 0,
                "total_invocations": 50,
                "last_session_id": None,
            }
        _write_health_file(health_file, hooks)

        with patch("sio.cli.status.HEALTH_FILE", health_file):
            rows = hook_health_rows()

    for hook_name, state, detail in rows:
        assert state == "warn", (
            f"Hook {hook_name!r} should be 'warn' (stale 2h) but got {state!r}: {detail}"
        )
        assert "stale" in detail.lower(), (
            f"Detail should mention 'stale' for warn state, got {detail!r}"
        )


# ---------------------------------------------------------------------------
# T092-3: Error state (consecutive_failures=3)
# ---------------------------------------------------------------------------


def test_hook_health_error_state():
    """consecutive_failures=3 must report 'error' with last_error_message."""
    from sio.cli.status import EXPECTED_HOOKS, hook_health_rows  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        health_file = Path(tmpdir) / ".sio" / "hook_health.json"
        hooks = {}
        for hook in EXPECTED_HOOKS:
            hooks[hook] = {
                "last_success": _utc_iso(timedelta(minutes=-10)),
                "last_error": _utc_iso(timedelta(minutes=-2)),
                "last_error_message": "ValueError: DB locked",
                "consecutive_failures": 3,
                "total_invocations": 200,
                "last_session_id": None,
            }
        _write_health_file(health_file, hooks)

        with patch("sio.cli.status.HEALTH_FILE", health_file):
            rows = hook_health_rows()

    for hook_name, state, detail in rows:
        assert state == "error", (
            f"Hook {hook_name!r} should be 'error' but got {state!r}: {detail}"
        )
        assert "consecutive failures" in detail.lower() or "ValueError" in detail, (
            f"Error detail must include failure context, got {detail!r}"
        )


# ---------------------------------------------------------------------------
# T092-4: Never-seen state (no heartbeat file)
# ---------------------------------------------------------------------------


def test_hook_health_never_seen_no_file():
    """Missing heartbeat file must report 'never-seen' for all expected hooks."""
    from sio.cli.status import EXPECTED_HOOKS, hook_health_rows  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        missing_file = Path(tmpdir) / ".sio" / "hook_health.json"
        # Do NOT create the file

        with patch("sio.cli.status.HEALTH_FILE", missing_file):
            rows = hook_health_rows()

    assert len(rows) == len(EXPECTED_HOOKS)
    for hook_name, state, detail in rows:
        assert state == "never-seen", (
            f"Hook {hook_name!r} should be 'never-seen' with no file, got {state!r}"
        )


# ---------------------------------------------------------------------------
# T092-5: Section coverage via hook_health_rows output format
# ---------------------------------------------------------------------------


def test_hook_health_rows_returns_expected_hooks():
    """hook_health_rows must return a row for each of the 3 expected hooks."""
    from sio.cli.status import EXPECTED_HOOKS, hook_health_rows  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        missing = Path(tmpdir) / "nofile"
        with patch("sio.cli.status.HEALTH_FILE", missing):
            rows = hook_health_rows()

    hook_names = {r[0] for r in rows}
    for hook in EXPECTED_HOOKS:
        assert hook in hook_names, f"Expected hook {hook!r} in hook_health_rows output"


# ---------------------------------------------------------------------------
# T092-6: Latency < 2s (SC-009)
# ---------------------------------------------------------------------------


def test_hook_health_rows_latency():
    """hook_health_rows() must complete in < 2s (SC-009)."""
    from sio.cli.status import EXPECTED_HOOKS, hook_health_rows  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        health_file = Path(tmpdir) / ".sio" / "hook_health.json"
        hooks = {
            hook: {
                "last_success": _utc_iso(timedelta(minutes=-1)),
                "last_error": None,
                "last_error_message": None,
                "consecutive_failures": 0,
                "total_invocations": 10,
                "last_session_id": None,
            }
            for hook in EXPECTED_HOOKS
        }
        _write_health_file(health_file, hooks)

        with patch("sio.cli.status.HEALTH_FILE", health_file):
            start = time.monotonic()
            hook_health_rows()
            elapsed = time.monotonic() - start

    assert elapsed < 2.0, (
        f"hook_health_rows() took {elapsed:.3f}s — must complete in < 2s (SC-009)"
    )
