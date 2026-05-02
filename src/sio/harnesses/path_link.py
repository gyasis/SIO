"""Shell PATH integration helper for `sio init --link-path`.

When pip installs the `sio` console script under e.g. `~/.local/bin/`
on Linux, it's not on PATH for non-login subprocesses (Claude Code's
Bash tool spawns with `bash -c`, not `bash -lc`). The user's symptom
is "I installed sio but Claude Code can't find it" — the binary is
there, just unreachable.

This helper appends a bracketed export-PATH block to the user's shell
rc file. The block is delimited so `sio init --uninstall` can remove
it surgically without disturbing user-authored content around it.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path

_BLOCK_BEGIN = "# >>> sio managed-path >>>"
_BLOCK_END = "# <<< sio managed-path <<<"


def _detect_shell_rc() -> Path:
    """Pick the most likely shell-rc file to append to.

    Order of preference:
        1. $SHELL ends in `/zsh` → ~/.zshrc
        2. $SHELL ends in `/fish` → ~/.config/fish/config.fish
        3. $SHELL ends in `/bash` → ~/.bashrc (Linux) or ~/.bash_profile (macOS)
        4. Fallback → ~/.profile (POSIX-universal)
    """
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    if shell.endswith("/zsh") or shell.endswith("zsh.exe"):
        return home / ".zshrc"
    if shell.endswith("/fish"):
        return home / ".config" / "fish" / "config.fish"
    if shell.endswith("/bash"):
        # On macOS, interactive shells source .bash_profile (not .bashrc); on
        # Linux, the convention is .bashrc. Pick whichever exists; default
        # to .bashrc on greenfield since most users will edit it later anyway.
        if sys.platform == "darwin" and (home / ".bash_profile").exists():
            return home / ".bash_profile"
        return home / ".bashrc"
    return home / ".profile"


def _scripts_dir() -> Path:
    """Where pip put the `sio` console-script binary."""
    return Path(sysconfig.get_path("scripts"))


@dataclass
class PathLinkReport:
    """What the link operation did (or would do, in dry-run)."""

    rc_file: Path
    scripts_dir: Path
    action: str  # "create" | "skip" | "would-create" | "remove" | "skip-not-managed"
    detail: str = ""
    notes: list[str] = field(default_factory=list)


def link_path(
    *,
    rc_file: Path | None = None,
    scripts_dir: Path | None = None,
    dry_run: bool = False,
) -> PathLinkReport:
    """Append the managed PATH block to the user's shell rc file.

    Idempotent: if the block already exists in the rc file, this is a
    no-op except for the report. The block sets PATH unconditionally so
    re-sourcing the rc on shell start always picks up the right scripts
    dir, even after a `pip install --user` to a different Python version.
    """
    rc_file = rc_file or _detect_shell_rc()
    scripts_dir = scripts_dir or _scripts_dir()

    block = (
        f"\n{_BLOCK_BEGIN}\n"
        "# Added by `sio init --link-path`. Remove via `sio init --uninstall`.\n"
        f'export PATH="{scripts_dir}:$PATH"\n'
        f"{_BLOCK_END}\n"
    )

    if rc_file.exists():
        existing = rc_file.read_text(encoding="utf-8", errors="replace")
        if _BLOCK_BEGIN in existing and _BLOCK_END in existing:
            return PathLinkReport(
                rc_file=rc_file,
                scripts_dir=scripts_dir,
                action="skip",
                detail="managed-path block already present — no changes",
            )
    else:
        existing = ""

    notes: list[str] = []
    if not dry_run:
        rc_file.parent.mkdir(parents=True, exist_ok=True)
        with rc_file.open("a", encoding="utf-8") as fh:
            fh.write(block)
        notes.append(
            f"appended to {rc_file}; run `source {rc_file}` or open a new shell"
        )
    return PathLinkReport(
        rc_file=rc_file,
        scripts_dir=scripts_dir,
        action="would-create" if dry_run else "create",
        detail=f"appended export PATH={scripts_dir}:$PATH",
        notes=notes,
    )


def unlink_path(
    *,
    rc_file: Path | None = None,
    dry_run: bool = False,
) -> PathLinkReport:
    """Remove the managed PATH block (only — leaves user content untouched)."""
    rc_file = rc_file or _detect_shell_rc()
    scripts_dir = _scripts_dir()

    if not rc_file.exists():
        return PathLinkReport(
            rc_file=rc_file,
            scripts_dir=scripts_dir,
            action="skip-not-managed",
            detail=f"{rc_file} does not exist",
        )
    text = rc_file.read_text(encoding="utf-8", errors="replace")
    if _BLOCK_BEGIN not in text:
        return PathLinkReport(
            rc_file=rc_file,
            scripts_dir=scripts_dir,
            action="skip-not-managed",
            detail="no sio managed-path block found",
        )

    # Surgical removal: keep everything before BEGIN, drop BEGIN..END inclusive.
    before, _, rest = text.partition(_BLOCK_BEGIN)
    _, _, after = rest.partition(_BLOCK_END)
    # Trim a single leading newline from `after` (the one we wrote with the
    # block) so we don't accumulate blank lines on repeated link/unlink cycles.
    if after.startswith("\n"):
        after = after[1:]
    new_text = before.rstrip() + ("\n" + after if after else "\n")

    if not dry_run:
        rc_file.write_text(new_text, encoding="utf-8")

    return PathLinkReport(
        rc_file=rc_file,
        scripts_dir=scripts_dir,
        action="remove" if not dry_run else "would-remove",
        detail=f"removed managed-path block from {rc_file}",
    )
