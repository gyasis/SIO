"""`degraded` from `systemctl --user is-system-running` means alive, not absent.

A user manager with one failed unit reports `degraded` and exits 1. The old check
looked for the substring "running", so it called that machine unable to schedule while
three SIO timers were running on it.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from sio.scheduler import systemd_briefing


def _completed(returncode: int, stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["systemctl", "--user", "is-system-running"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


@pytest.mark.parametrize(
    "returncode,stdout,expected",
    [
        (0, "running\n", True),
        (1, "degraded\n", True),  # the regression: one failed unit, systemd is fine
        (1, "starting\n", True),
        (1, "initializing\n", True),
        (1, "maintenance\n", True),
        (1, "offline\n", False),
        (1, "unknown\n", False),
        (1, "", False),
    ],
)
def test_state_decides_availability(returncode, stdout, expected):
    with patch.object(systemd_briefing.shutil, "which", return_value="/bin/systemctl"):
        with patch.object(
            systemd_briefing.subprocess, "run", return_value=_completed(returncode, stdout)
        ):
            assert systemd_briefing.available() is expected


def test_no_systemctl_binary():
    with patch.object(systemd_briefing.shutil, "which", return_value=None):
        assert systemd_briefing.available() is False


def test_probe_failure_is_not_available():
    with patch.object(systemd_briefing.shutil, "which", return_value="/bin/systemctl"):
        with patch.object(
            systemd_briefing.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("systemctl", 5),
        ):
            assert systemd_briefing.available() is False
