"""systemd **user** timer for off-session briefing-store refresh.

Why a systemd user timer and not a bare crontab.  On a laptop, ``crontab @daily``
silently *skips* a run when the machine sleeps through the scheduled time.  It never
catches up.  A user timer with ``Persistent=true`` runs the missed job
shortly after the next boot or wake.  So the worker always returns to a fresh
briefing.  ``Nice=19`` plus idle IO throttle the service, and a user-idle gate
holds it back, so the off-session refresh never competes with an active session.

``sio init`` installs it, as does ``sio schedule install-briefing``, so a fresh
machine wires itself up.  This is portable, not a manual per-machine step.  On a
platform without ``systemctl --user`` the installer degrades gracefully: it
returns ``available=False``, and the store still works.  Whatever other trigger
runs ``sio briefing --refresh`` then keeps it fresh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
_SERVICE = "sio-briefing-refresh.service"
_TIMER = "sio-briefing-refresh.timer"


# States of `systemctl --user is-system-running` that mean the user manager is alive
# and will accept units. `degraded` is the common one: it reports that at least one unit
# failed, not that systemd is absent, and it exits 1.
_LIVE_SYSTEMD_STATES = frozenset(
    {"running", "degraded", "starting", "initializing", "maintenance"}
)


def available() -> bool:
    """True when a systemd user instance is usable on this machine.

    The previous check read `"running" in output` and so answered False for
    `degraded` -- which its own comment listed as alive, because the word
    `degraded` does not contain `running`. On razer, with one failed user unit,
    `sio schedule status` therefore printed "Briefing timer: systemd --user
    unavailable" while `systemctl --user list-timers` showed three live SIO timers.
    That reads as "this machine cannot schedule" and sent the reader looking for a
    platform limit that was not there.
    """
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    if r.returncode == 0:
        return True
    state = (r.stdout + r.stderr).strip().lower()
    return any(s in state for s in _LIVE_SYSTEMD_STATES)


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
