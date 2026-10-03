"""sio archive install: writes the scheduler files, idempotently, and fails loud."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sio.archive import schedule


@pytest.fixture()
def linux(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []
    monkeypatch.setattr(schedule.sys, "platform", "linux")
    monkeypatch.setattr(schedule, "sio_executable", lambda: "/opt/bin/sio")
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(schedule.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    return tmp_path, calls


def test_install_writes_units_with_absolute_exe(linux):
    home, calls = linux
    schedule.install(interval_minutes=30, home=home)
    unit_dir = home / ".config/systemd/user"
    assert "ExecStart=/opt/bin/sio archive sync" in (unit_dir / "sio-archive.service").read_text()
    assert "OnUnitActiveSec=30min" in (unit_dir / "sio-archive.timer").read_text()
    assert calls == [["systemctl", "--user", "daemon-reload"],
                     ["systemctl", "--user", "enable", "--now", "sio-archive.timer"]]


def test_second_install_rewrites_nothing(linux):
    home, calls = linux
    schedule.install(home=home)
    calls.clear()
    actions = schedule.install(home=home)
    assert all(a.startswith(("unchanged", "ran: systemctl --user enable")) for a in actions)
    assert calls == [["systemctl", "--user", "enable", "--now", "sio-archive.timer"]]


def test_dry_run_touches_nothing(linux):
    home, calls = linux
    actions = schedule.install(home=home, dry_run=True)
    assert not (home / ".config").exists()
    assert calls == []
    assert any(a.startswith("would write") for a in actions)


def test_no_scheduler_fails_loud(linux, monkeypatch):
    home, _ = linux
    monkeypatch.setattr(schedule.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="cron"):
        schedule.install(home=home)


def test_macos_writes_launchd_plist(linux, monkeypatch):
    home, calls = linux
    monkeypatch.setattr(schedule.sys, "platform", "darwin")
    schedule.install(interval_minutes=60, home=home)
    plist = (home / "Library/LaunchAgents/io.sio.archive.plist").read_text()
    assert "<string>/opt/bin/sio</string><string>archive</string><string>sync</string>" in plist
    assert "<integer>3600</integer>" in plist
    assert calls[-1][:2] == ["launchctl", "bootstrap"]
