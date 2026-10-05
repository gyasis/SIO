"""Harness adapters — one module per supported AI coding agent harness.

A harness is the runtime environment a user runs their AI coding agent in
(Claude Code, Cursor, Windsurf, OpenCode, Hermes, etc.). Each harness has
its own conventions for where skills, rules, and hook scripts live and how
they get registered.

The adapter layer abstracts those differences so `sio init` can stage SIO's
bundled assets (skills, rules, hook scripts) into whichever harness the user
runs, without forking the bootstrap logic per harness.

Implemented: Claude Code (skills + rules + hooks), and pi / codex / opencode
(skills only — none of the three has a rules dir SIO may write to, and their
hook or plugin systems do not take Claude Code's telemetry hooks; the
unsupported asset types are reported in the install output, never silently
dropped). Cursor / Windsurf are stubs that report "not yet implemented" so
the architecture is visible and the work to add a new harness is bounded to
writing one adapter module.
"""

from __future__ import annotations

from sio.harnesses.base import (
    HarnessAdapter,
    HarnessNotInstalledError,
    InstallReport,
    StatusReport,
)
from sio.harnesses.claude_code import ClaudeCodeAdapter
from sio.harnesses.codex import CodexAdapter
from sio.harnesses.cursor import CursorAdapter
from sio.harnesses.opencode import OpenCodeAdapter
from sio.harnesses.pi import PiAdapter
from sio.harnesses.windsurf import WindsurfAdapter

#: Ordered registry — first entry is the default when auto-detecting.
ALL_ADAPTERS: list[type[HarnessAdapter]] = [
    ClaudeCodeAdapter,
    PiAdapter,
    CodexAdapter,
    OpenCodeAdapter,
    CursorAdapter,
    WindsurfAdapter,
]


def get_adapter(name: str) -> HarnessAdapter:
    """Look up an adapter instance by harness name (e.g. 'claude-code')."""
    for cls in ALL_ADAPTERS:
        if cls.name == name:
            return cls()
    known = ", ".join(c.name for c in ALL_ADAPTERS)
    raise ValueError(f"Unknown harness {name!r}. Known: {known}")


def detect_adapters() -> list[HarnessAdapter]:
    """Return adapter instances for every harness detected on this system."""
    return [cls() for cls in ALL_ADAPTERS if cls().detect()]


__all__ = [
    "ALL_ADAPTERS",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "HarnessAdapter",
    "HarnessNotInstalledError",
    "InstallReport",
    "OpenCodeAdapter",
    "PiAdapter",
    "StatusReport",
    "WindsurfAdapter",
    "detect_adapters",
    "get_adapter",
]
