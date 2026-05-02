"""Harness adapter contract — Protocol + report dataclasses.

A `HarnessAdapter` is responsible for staging SIO's bundled bootstrap content
(skills, rules, hook scripts) into a particular AI-coding-agent harness's
config directory. The same SIO assets get installed differently depending on
the harness's conventions; the adapter encapsulates those conventions.

Reports use simple dataclasses (not dicts) so the CLI can render structured
output and tests can assert on field values without parsing JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal, Protocol


class HarnessNotInstalledError(RuntimeError):
    """Raised when an adapter is asked to install but the harness isn't present."""


@dataclass
class FileChange:
    """One file-level operation an install / uninstall performed (or would perform)."""

    path: Path
    action: Literal[
        "create",
        "update",
        "skip",
        "backup",
        "remove",
        "would-create",
        "would-update",
        "would-remove",
    ]
    reason: str = ""


@dataclass
class InstallReport:
    """Summary of an install / uninstall run.

    `dry_run` records preview vs applied. `errors` captures non-fatal issues
    so partial installs can still complete and surface what was skipped.
    """

    harness: str
    dry_run: bool = False
    changes: list[FileChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, path: Path, action: str, reason: str = "") -> None:
        self.changes.append(FileChange(path=path, action=action, reason=reason))  # type: ignore[arg-type]

    @property
    def success(self) -> bool:
        return not self.errors


@dataclass
class StatusReport:
    """Snapshot of what's currently installed vs what the package ships."""

    harness: str
    detected: bool
    config_dir: Path
    installed_files: list[Path] = field(default_factory=list)
    missing_files: list[Path] = field(default_factory=list)
    drifted_files: list[Path] = field(default_factory=list)  # user-modified vs source
    notes: list[str] = field(default_factory=list)


class HarnessAdapter(Protocol):
    """Minimal contract every harness adapter must implement.

    Subclass-style implementation is fine; this Protocol exists so
    `sio.harnesses.__init__` can type-check the registry without forcing
    any particular base class.
    """

    name: ClassVar[str]  # e.g. "claude-code"
    config_dir: Path

    def detect(self) -> bool:
        """Return True if this harness is installed on the current system."""
        ...

    def install(self, *, dry_run: bool = False, force: bool = False) -> InstallReport:
        """Stage SIO's bootstrap assets into the harness's config directory.

        - `dry_run`: report what *would* change, write nothing.
        - `force`: overwrite user-modified files (default: skip + report drift).
        """
        ...

    def uninstall(self, *, dry_run: bool = False) -> InstallReport:
        """Remove SIO-managed assets. Leaves user-modified files alone."""
        ...

    def status(self) -> StatusReport:
        """Diff what SIO ships vs what's currently installed."""
        ...
