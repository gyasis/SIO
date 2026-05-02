"""Cursor harness adapter — STUB.

Cursor uses `~/.cursor/` for config (rules, mcp.json). Adapter not yet
implemented; this stub exists so the architecture is visible and the
work to add Cursor support is bounded to filling in this single file.

To implement: model after `sio.harnesses.claude_code.ClaudeCodeAdapter`,
mapping the bootstrap layout onto Cursor's conventions.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from sio.harnesses.base import HarnessAdapter, InstallReport, StatusReport


class CursorAdapter(HarnessAdapter):
    name: ClassVar[str] = "cursor"

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or (Path.home() / ".cursor")

    def detect(self) -> bool:
        return self.config_dir.exists()

    def install(self, *, dry_run: bool = False, force: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        report.errors.append(
            "cursor adapter not yet implemented — track progress at "
            "https://github.com/gyasis/SIO/issues (open one labeled 'harness:cursor')"
        )
        return report

    def uninstall(self, *, dry_run: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        report.errors.append("cursor adapter not yet implemented")
        return report

    def status(self) -> StatusReport:
        return StatusReport(
            harness=self.name,
            detected=self.detect(),
            config_dir=self.config_dir,
            notes=["cursor adapter not yet implemented (stub)"],
        )
