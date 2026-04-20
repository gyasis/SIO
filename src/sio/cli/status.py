"""sio.cli.status — Hook health reader for sio status command.

Per contracts/hook-heartbeat.md §4.

Public API
----------
    hook_health_rows() -> list[tuple[str, str, str]]
        Returns [(hook_name, state, detail), ...] where
        state ∈ {healthy, warn, error, never-seen}.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEALTH_FILE: Path = Path.home() / ".sio" / "hook_health.json"

WARN_STALE: timedelta = timedelta(hours=1)
ERROR_STALE: timedelta = timedelta(hours=6)
EXPECTED_HOOKS: tuple[str, ...] = ("post_tool_use", "stop", "pre_compact")


def hook_health_rows() -> list[tuple[str, str, str]]:
    """Return hook health rows for display in sio status.

    Returns
    -------
    list[tuple[str, str, str]]
        List of ``(hook_name, state, detail)`` tuples where ``state`` is one of:
        ``"healthy"``, ``"warn"``, ``"error"``, ``"never-seen"``.
    """
    if not HEALTH_FILE.exists():
        return [(h, "never-seen", "no heartbeat file") for h in EXPECTED_HOOKS]

    try:
        data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [(h, "error", "unreadable heartbeat file") for h in EXPECTED_HOOKS]

    rows: list[tuple[str, str, str]] = []
    now = datetime.now(timezone.utc)

    for hook in EXPECTED_HOOKS:
        h = data.get("hooks", {}).get(hook)
        if not h or not h.get("last_success"):
            rows.append((hook, "never-seen", "never fired"))
            continue

        try:
            last = datetime.fromisoformat(h["last_success"])
        except (ValueError, TypeError):
            rows.append((hook, "error", "invalid last_success timestamp"))
            continue

        age = now - last
        consec = h.get("consecutive_failures", 0)

        if consec >= 3:
            last_msg = h.get("last_error_message") or "unknown error"
            rows.append((hook, "error", f"{consec} consecutive failures; last error {last_msg}"))
        elif age > ERROR_STALE:
            rows.append((hook, "error", f"stale {age}"))
        elif age > WARN_STALE:
            rows.append((hook, "warn", f"stale {age}"))
        elif consec > 0:
            rows.append((hook, "warn", f"{consec} recent failures"))
        else:
            rows.append((hook, "healthy", f"last success {age} ago"))

    return rows
