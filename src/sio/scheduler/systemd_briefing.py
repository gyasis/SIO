"""systemd **user** timer for off-session briefing-store refresh.

Why a systemd user timer (not bare crontab): on a laptop, ``crontab @daily``
silently *skips* a run when the machine is asleep/off at the scheduled time and
never catches up.  A user timer with ``Persistent=true`` runs the missed job
shortly after the next boot/wake — so the worker always returns to a fresh
briefing.  The service is throttled (``Nice=19`` + idle IO) and gated on user
idle, so the off-session refresh never competes with an active session.

Installed by ``sio init`` (and ``sio schedule install-briefing``) so a fresh
machine is wired up automatically — this is portable, not a manual per-machine
step.  On platforms without ``systemctl --user`` the installer degrades
gracefully (returns ``available=False``; the store still works, just refreshed
by whatever other trigger runs ``sio briefing --refresh``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
_SERVICE = "sio-briefing-refresh.service"
_TIMER = "sio-briefing-refresh.timer"


def available() -> bool:
    """True when a systemd user instance is usable on this machine."""
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # "running"/"degraded"/"starting" all mean the user manager is alive.
        return r.returncode == 0 or "running" in (r.stdout + r.stderr).lower()
    except Exception:
        return False


def _service_unit(python: str, interval_hours: int) -> str:
    # Absolute interpreter so the unit never depends on PATH/venv activation.
    exec_start = f"{python} -m sio briefing --refresh --if-idle"
    return f"""[Unit]
Description=SIO briefing store refresh (off-session)
Documentation=https://github.com/gyasis/SIO

[Service]
Type=oneshot
Nice=19
IOSchedulingClass=idle
Environment=SIO_BRIEFING_TTL={interval_hours * 3600}
ExecStart={exec_start}
"""


def _timer_unit(interval_hours: int) -> str:
    # OnCalendar + Persistent=true is the laptop-correct combo: a run missed
    # while the machine was asleep/off is executed shortly after the next boot
    # (Persistent catch-up only applies to realtime/OnCalendar timers, NOT to
    # monotonic OnUnitInactiveState).  OnBootSec adds a fresh-boot refresh.
    return f"""[Unit]
Description=SIO briefing store refresh (off-session, catch-up on wake)

[Timer]
OnBootSec=3min
OnCalendar=*-*-* 0/{interval_hours}:00:00
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
"""


def install(*, interval_hours: int = 6, python: str | None = None) -> dict:
    """Write + enable the briefing-refresh user timer (idempotent).

    Returns a report dict: ``installed``, ``available``, ``unit_dir``,
    ``timer``, ``interval_hours``, and (on skip) ``reason``.
    """
    if not available():
        return {
            "installed": False,
            "available": False,
            "reason": "systemd --user not available on this machine",
        }

    python = python or sys.executable
    os.makedirs(_UNIT_DIR, exist_ok=True)

    with open(os.path.join(_UNIT_DIR, _SERVICE), "w") as f:
        f.write(_service_unit(python, interval_hours))
    with open(os.path.join(_UNIT_DIR, _TIMER), "w") as f:
        f.write(_timer_unit(interval_hours))

    def _sc(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=15,
        )

    _sc("daemon-reload")
    enable = _sc("enable", "--now", _TIMER)

    return {
        "installed": enable.returncode == 0,
        "available": True,
        "unit_dir": _UNIT_DIR,
        "timer": _TIMER,
        "interval_hours": interval_hours,
        "detail": (enable.stderr or enable.stdout).strip() or "enabled",
    }


def uninstall() -> dict:
    """Disable + remove the briefing-refresh user units (idempotent)."""
    if available():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", _TIMER],
            capture_output=True,
            text=True,
            timeout=15,
        )
    removed = []
    for unit in (_TIMER, _SERVICE):
        p = os.path.join(_UNIT_DIR, unit)
        try:
            os.unlink(p)
            removed.append(unit)
        except OSError:
            pass
    if available():
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    return {"removed": removed}


def status() -> dict:
    """Return installed/enabled state of the briefing timer."""
    if not available():
        return {"available": False}
    r = subprocess.run(
        ["systemctl", "--user", "is-enabled", _TIMER],
        capture_output=True,
        text=True,
        timeout=10,
    )
    installed = os.path.exists(os.path.join(_UNIT_DIR, _TIMER))
    return {
        "available": True,
        "installed": installed,
        "enabled": r.stdout.strip() == "enabled",
    }
