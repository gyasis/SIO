"""Hook heartbeat — records per-hook health to ~/.sio/hook_health.json.

Implements the write contract from contracts/hook-heartbeat.md §3.

Every Claude Code hook entrypoint calls either ``record_success()`` or
``record_failure()`` to publish a heartbeat. The heartbeat file is written
atomically via temp-file + ``os.replace`` so that a kill mid-write never
leaves a corrupt JSON file.

Heartbeat write failures are silently swallowed (logged to stderr) so that
a broken health file never blocks the hook from completing its real work.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sio.core.util.time import utc_now_iso

HEALTH_FILE: Path = Path.home() / ".sio" / "hook_health.json"


def record_success(hook_name: str, session_id: str | None = None) -> None:
    """Record a successful hook invocation.

    Increments ``total_invocations``, sets ``last_success`` to the current
    UTC timestamp, and resets ``consecutive_failures`` to 0.

    Args:
        hook_name: Logical hook identifier (e.g., ``"post_tool_use"``).
        session_id: Optional Claude Code session UUID for traceability.
    """
    _update(hook_name, success=True, session_id=session_id, error=None)


def record_failure(hook_name: str, error: BaseException) -> None:
    """Record a hook invocation that raised an exception.

    Increments ``consecutive_failures`` and records the error type + message
    as ``last_error_message`` in the format ``"ExceptionType: message"``.

    Args:
        hook_name: Logical hook identifier.
        error: The exception that was caught.
    """
    _update(hook_name, success=False, session_id=None, error=error)


def _update(
    hook_name: str,
    *,
    success: bool,
    session_id: str | None,
    error: BaseException | None,
) -> None:
    """Internal: read → update → atomically write hook_health.json.

    All exceptions from the write path are swallowed and logged to stderr so
    that a heartbeat failure never crashes the hook itself.
    """
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now_iso()

    try:
        # Load existing data or start fresh
        try:
            data = (
                json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
                if HEALTH_FILE.exists()
                else {"schema_version": 1, "hooks": {}}
            )
        except Exception:
            data = {"schema_version": 1, "hooks": {}}

        # Ensure schema_version is present (idempotent on existing files)
        data.setdefault("schema_version", 1)

        # Initialise hook entry if first-seen
        h = data["hooks"].setdefault(
            hook_name,
            {
                "last_success": None,
                "last_error": None,
                "last_error_message": None,
                "consecutive_failures": 0,
                "total_invocations": 0,
                "last_session_id": None,
            },
        )

        # Always increment invocation counter (monotonic)
        h["total_invocations"] = h.get("total_invocations", 0) + 1

        if success:
            h["last_success"] = now
            h["consecutive_failures"] = 0
            if session_id:
                h["last_session_id"] = session_id
        else:
            h["last_error"] = now
            h["last_error_message"] = f"{type(error).__name__}: {error}"
            h["consecutive_failures"] = h.get("consecutive_failures", 0) + 1

        # Top-level updated_at
        data["updated_at"] = now

        # Atomic write: tmp file + os.replace
        tmp = HEALTH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, HEALTH_FILE)

    except Exception as write_exc:  # noqa: BLE001
        # Heartbeat write failures must NOT propagate — log to stderr only.
        print(
            f"[sio:heartbeat] WARNING: failed to write {HEALTH_FILE}: {write_exc}",
            file=sys.stderr,
        )
