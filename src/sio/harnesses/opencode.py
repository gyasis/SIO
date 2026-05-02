"""OpenCode harness adapter — STUB.

OpenCode uses `~/.config/opencode/` for config. Adapter not yet
implemented; see `cursor.py` for the same stub pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from sio.harnesses.base import HarnessAdapter, InstallReport, StatusReport


class OpenCodeAdapter(HarnessAdapter):
    name: ClassVar[str] = "opencode"

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or (Path.home() / ".config" / "opencode")

    def detect(self) -> bool:
        return self.config_dir.exists()

    def install(self, *, dry_run: bool = False, force: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        report.errors.append(
            "opencode adapter not yet implemented — track progress at "
            "https://github.com/gyasis/SIO/issues (open one labeled 'harness:opencode')"
        )
        return report

    def uninstall(self, *, dry_run: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        report.errors.append("opencode adapter not yet implemented")
        return report

    def status(self) -> StatusReport:
        return StatusReport(
            harness=self.name,
            detected=self.detect(),
            config_dir=self.config_dir,
            notes=["opencode adapter not yet implemented (stub)"],
        )
