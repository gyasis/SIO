"""Install the daily ``sio archive sync`` schedule (systemd --user on Linux, launchd on macOS).

The archive only protects history if it runs well inside the shortest harness
retention window (Claude Code: 30 days), so SIO installs its own schedule rather
than relying on the user to remember one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT = "sio-archive"
LAUNCHD_LABEL = "io.sio.archive"


def sio_executable() -> str:
    """Absolute path to ``sio``: schedulers do not read the shell PATH."""
    found = shutil.which("sio")
    if found:
        return str(Path(found).resolve())
    argv0 = Path(sys.argv[0])
    if argv0.name == "sio" and argv0.exists():
        return str(argv0.resolve())
    raise FileNotFoundError("cannot find the `sio` executable on PATH")


def systemd_files(exe: str, interval_minutes: int) -> dict[str, str]:
    return {
        f"{UNIT}.service": (
            "[Unit]\n"
            "Description=SIO session archive: copy every coding agent's raw session files\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart={exe} archive sync\n"
            "WorkingDirectory=%h\n"
            "TimeoutStartSec=1800\n"
        ),
        f"{UNIT}.timer": (
            "[Unit]\n"
            "Description=Run sio archive sync on a schedule\n\n"
            "[Timer]\n"
            "OnBootSec=5min\n"
            f"OnUnitActiveSec={interval_minutes}min\n"
            "Persistent=true\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
    }


def launchd_plist(exe: str, interval_minutes: int, log: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{exe}</string><string>archive</string><string>sync</string></array>
  <key>StartInterval</key><integer>{interval_minutes * 60}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _write(path: Path, text: str, dry_run: bool, actions: list[str]) -> bool:
    """Write ``text`` to ``path`` if it differs. Returns True when it changed."""
    if path.exists() and path.read_text() == text:
        actions.append(f"unchanged {path}")
        return False
    actions.append(f"{'would write' if dry_run else 'wrote'} {path}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return True


def _run(cmd: list[str], dry_run: bool, actions: list[str]) -> None:
    actions.append(("would run: " if dry_run else "ran: ") + " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True, capture_output=True, text=True)


DEFAULT_INTERVAL_MINUTES = 1440  # daily: well inside a 30-day retention window


def install(interval_minutes: int = DEFAULT_INTERVAL_MINUTES, dry_run: bool = False,
            home: Path | None = None) -> list[str]:
    """Install (or refresh) the schedule. Returns the actions taken, for display."""
    home = home or Path.home()
    exe = sio_executable()
    actions: list[str] = []
    if sys.platform == "darwin":
        from sio.core.paths import sio_home  # noqa: PLC0415

        plist = home / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        text = launchd_plist(exe, interval_minutes, sio_home() / "archive-sync.log")
        _write(plist, text, dry_run, actions)
        domain = f"gui/{os.getuid()}"
        if not dry_run:  # reload so an edited plist takes effect; "not loaded" is fine
            subprocess.run(["launchctl", "bootout", domain, str(plist)],
                           capture_output=True, text=True)
        _run(["launchctl", "bootstrap", domain, str(plist)], dry_run, actions)
        return actions
    if shutil.which("systemctl") is None:
        raise RuntimeError("no systemd or launchd found; schedule `sio archive sync` with cron")
    unit_dir = home / ".config/systemd/user"
    changed = False
    for name, text in systemd_files(exe, interval_minutes).items():
        changed |= _write(unit_dir / name, text, dry_run, actions)
    if changed or dry_run:
        _run(["systemctl", "--user", "daemon-reload"], dry_run, actions)
    _run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"], dry_run, actions)
    return actions
