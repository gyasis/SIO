"""T036 [US4] Unit tests for sio.scheduler.cron — passive background analysis scheduler.

Tests cover:
- install_schedule() -> dict
    Installs daily (midnight) and weekly (Sunday) cron entries for the SIO
    analysis pipeline.  Returns a status dict.
- uninstall_schedule() -> dict
    Removes all SIO-managed cron entries.  Returns a status dict.
- get_status() -> dict
    Inspects the current crontab and reports installed/enabled state.

These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from sio.scheduler.cron import get_status, install_schedule, uninstall_schedule

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Standard cron field pattern: minute hour dom month dow command
_CRON_LINE_RE = re.compile(
    r"^"
    r"(@\w+|\*|[0-9,\-/]+)"      # minute (or @reboot/@daily shorthand)
    r"(?:\s+\*|[0-9,\-/]+)*"     # remaining time fields (optional when @-form)
    r"\s+"
    r".+"                          # command (at least one non-space char)
    r"$"
)

# A stricter pattern that verifies the five standard cron fields.
_FIVE_FIELD_CRON_RE = re.compile(
    r"^"
    r"(?:\*|[0-9]{1,2}(?:,[0-9]{1,2})*(?:-[0-9]{1,2})?(?:/[0-9]{1,2})?)"  # minute
    r"\s+"
    r"(?:\*|[0-9]{1,2}(?:,[0-9]{1,2})*(?:-[0-9]{1,2})?(?:/[0-9]{1,2})?)"  # hour
    r"\s+"
    r"(?:\*|[0-9]{1,2}(?:,[0-9]{1,2})*)"                                   # dom
    r"\s+"
    r"(?:\*|[0-9]{1,2}(?:,[0-9]{1,2})*)"                                   # month
    r"\s+"
    r"(?:\*|[0-7](?:,[0-7])*)"                                              # dow
    r"\s+"
    r".+"                                                                    # command
    r"$"
)

# Cron midnight shorthand for daily schedule: "0 0 * * *" or "@daily"
_MIDNIGHT_RE = re.compile(r"^(0\s+0\s+\*\s+\*\s+\*|@daily|@midnight)\b")

# Cron Sunday shorthand: "0 0 * * 0" or "@weekly"
_SUNDAY_RE = re.compile(r"^(0\s+0\s+\*\s+\*\s+0|@weekly)\b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_cron_line(line: str) -> bool:
    """Return True if *line* is a syntactically valid cron entry."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    # Accept @-form shorthand (e.g. @daily, @weekly, @reboot).
    if stripped.startswith("@"):
        parts = stripped.split(None, 1)
        return len(parts) == 2 and len(parts[1]) > 0
    # Standard five-field form.
    return bool(_FIVE_FIELD_CRON_RE.match(stripped))


def _extract_cron_lines(text: str) -> list[str]:
    """Return all non-comment, non-blank lines from *text*."""
    return [
        line
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_crontab(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate all tests from the real user crontab.

    Replaces subprocess.run with a lightweight mock that:
    - Returns an empty crontab on ``crontab -l``
    - Records crontab writes on ``crontab -`` (piped stdin)

    Individual tests that need richer behaviour override this via
    ``with patch(...)`` or monkeypatch inside the test body.
    """
    _state: dict[str, str] = {"crontab": ""}

    def _fake_run(
        cmd: list[str],
        *,
        input: bytes | None = None,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        **kwargs: Any,
    ) -> MagicMock:
        mock = MagicMock()
        mock.returncode = 0
        if "crontab" in cmd[0] and "-l" in cmd:
            # Reading the current crontab.
            mock.stdout = _state["crontab"]
            mock.stderr = ""
        elif "crontab" in cmd[0] and "-" in cmd:
            # Writing a new crontab via stdin.
            if isinstance(input, bytes):
                _state["crontab"] = input.decode("utf-8")
            elif isinstance(input, str):
                _state["crontab"] = input
        return mock

    monkeypatch.setattr("subprocess.run", _fake_run)


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestGeneratesValidCrontabEntries:
    """install_schedule must produce syntactically valid cron entries."""

    def test_install_returns_dict(self) -> None:
        result = install_schedule()
        assert isinstance(result, dict)

    def test_install_returns_installed_true(self) -> None:
        result = install_schedule()
        assert result.get("installed") is True

    def test_installed_cron_lines_are_syntactically_valid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written_lines: list[str] = []

        def _capturing_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            if "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        written_lines.append(stripped)
            return mock

        monkeypatch.setattr("subprocess.run", _capturing_run)

        install_schedule()

        assert len(written_lines) >= 1, "No cron entries were written at all"
        for line in written_lines:
            assert _is_valid_cron_line(line), (
                f"Cron line failed syntax check: {line!r}"
            )

    def test_install_result_has_entries_key(self) -> None:
        result = install_schedule()
        # The result should expose the entries that were written.
        assert "entries" in result or "daily_entry" in result or "weekly_entry" in result


class TestDailyAndWeeklySchedules:
    """Both a daily (midnight) and a weekly (Sunday) entry must be installed."""

    def test_daily_entry_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written_lines: list[str] = []

        def _capturing_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            if "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                written_lines.extend(
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
            return mock

        monkeypatch.setattr("subprocess.run", _capturing_run)
        install_schedule()

        assert any(_MIDNIGHT_RE.match(line) for line in written_lines), (
            f"No midnight/daily cron entry found in: {written_lines}"
        )

    def test_weekly_entry_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written_lines: list[str] = []

        def _capturing_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            if "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                written_lines.extend(
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
            return mock

        monkeypatch.setattr("subprocess.run", _capturing_run)
        install_schedule()

        assert any(_SUNDAY_RE.match(line) for line in written_lines), (
            f"No Sunday/weekly cron entry found in: {written_lines}"
        )

    def test_both_daily_and_weekly_entries_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written_lines: list[str] = []

        def _capturing_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            if "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                written_lines.extend(
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
            return mock

        monkeypatch.setattr("subprocess.run", _capturing_run)
        install_schedule()

        has_daily = any(_MIDNIGHT_RE.match(line) for line in written_lines)
        has_weekly = any(_SUNDAY_RE.match(line) for line in written_lines)
        assert has_daily, f"Daily entry missing. Written lines: {written_lines}"
        assert has_weekly, f"Weekly entry missing. Written lines: {written_lines}"

    def test_install_result_reports_daily_enabled(self) -> None:
        result = install_schedule()
        assert result.get("daily_enabled") is True

    def test_install_result_reports_weekly_enabled(self) -> None:
        result = install_schedule()
        assert result.get("weekly_enabled") is True

    def test_cron_command_references_sio(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each installed cron line must invoke the sio CLI or its module."""
        written_lines: list[str] = []

        def _capturing_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            mock.stderr = ""
            if "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                written_lines.extend(
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
            return mock

        monkeypatch.setattr("subprocess.run", _capturing_run)
        install_schedule()

        for line in written_lines:
            assert "sio" in line.lower(), (
                f"Cron entry does not reference sio: {line!r}"
            )


class TestIdempotentInstall:
    """Calling install_schedule twice must not duplicate cron entries."""

    def test_double_install_no_duplicate_daily(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state: dict[str, str] = {"crontab": ""}

        def _stateful_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "crontab" in cmd[0] and "-l" in cmd:
                mock.stdout = _state["crontab"]
            elif "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                _state["crontab"] = content
                mock.stdout = ""
            else:
                mock.stdout = ""
            return mock

        monkeypatch.setattr("subprocess.run", _stateful_run)

        install_schedule()
        install_schedule()

        lines = _extract_cron_lines(_state["crontab"])
        daily_count = sum(1 for line in lines if _MIDNIGHT_RE.match(line))
        assert daily_count == 1, (
            f"Expected exactly 1 daily entry, found {daily_count}. Lines: {lines}"
        )

    def test_double_install_no_duplicate_weekly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state: dict[str, str] = {"crontab": ""}

        def _stateful_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "crontab" in cmd[0] and "-l" in cmd:
                mock.stdout = _state["crontab"]
            elif "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                _state["crontab"] = content
                mock.stdout = ""
            else:
                mock.stdout = ""
            return mock

        monkeypatch.setattr("subprocess.run", _stateful_run)

        install_schedule()
        install_schedule()

        lines = _extract_cron_lines(_state["crontab"])
        weekly_count = sum(1 for line in lines if _SUNDAY_RE.match(line))
        assert weekly_count == 1, (
            f"Expected exactly 1 weekly entry, found {weekly_count}. Lines: {lines}"
        )

    def test_triple_install_still_idempotent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state: dict[str, str] = {"crontab": ""}

        def _stateful_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "crontab" in cmd[0] and "-l" in cmd:
                mock.stdout = _state["crontab"]
            elif "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                _state["crontab"] = content
                mock.stdout = ""
            else:
                mock.stdout = ""
            return mock

        monkeypatch.setattr("subprocess.run", _stateful_run)

        for _ in range(3):
            install_schedule()

        lines = _extract_cron_lines(_state["crontab"])
        daily_count = sum(1 for line in lines if _MIDNIGHT_RE.match(line))
        weekly_count = sum(1 for line in lines if _SUNDAY_RE.match(line))
        assert daily_count == 1
        assert weekly_count == 1

    def test_install_after_uninstall_reinstalls_cleanly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state: dict[str, str] = {"crontab": ""}

        def _stateful_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "crontab" in cmd[0] and "-l" in cmd:
                mock.stdout = _state["crontab"]
            elif "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                _state["crontab"] = content
                mock.stdout = ""
            else:
                mock.stdout = ""
            return mock

        monkeypatch.setattr("subprocess.run", _stateful_run)

        install_schedule()
        uninstall_schedule()
        install_schedule()

        lines = _extract_cron_lines(_state["crontab"])
        daily_count = sum(1 for line in lines if _MIDNIGHT_RE.match(line))
        weekly_count = sum(1 for line in lines if _SUNDAY_RE.match(line))
        assert daily_count == 1
        assert weekly_count == 1


class TestGetStatusReturnsDict:
    """get_status must return a dict with at least installed, daily_enabled,
    and weekly_enabled boolean fields."""

    def test_get_status_returns_dict(self) -> None:
        result = get_status()
        assert isinstance(result, dict)

    def test_get_status_has_installed_key(self) -> None:
        result = get_status()
        assert "installed" in result

    def test_get_status_installed_is_bool(self) -> None:
        result = get_status()
        assert isinstance(result.get("installed"), bool)

    def test_get_status_has_daily_enabled_key(self) -> None:
        result = get_status()
        assert "daily_enabled" in result

    def test_get_status_daily_enabled_is_bool(self) -> None:
        result = get_status()
        assert isinstance(result.get("daily_enabled"), bool)

    def test_get_status_has_weekly_enabled_key(self) -> None:
        result = get_status()
        assert "weekly_enabled" in result

    def test_get_status_weekly_enabled_is_bool(self) -> None:
        result = get_status()
        assert isinstance(result.get("weekly_enabled"), bool)

    def test_get_status_after_install_shows_installed_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state: dict[str, str] = {"crontab": ""}

        def _stateful_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "crontab" in cmd[0] and "-l" in cmd:
                mock.stdout = _state["crontab"]
            elif "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                _state["crontab"] = content
                mock.stdout = ""
            else:
                mock.stdout = ""
            return mock

        monkeypatch.setattr("subprocess.run", _stateful_run)

        install_schedule()
        status = get_status()

        assert status["installed"] is True

    def test_get_status_after_install_shows_daily_and_weekly_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state: dict[str, str] = {"crontab": ""}

        def _stateful_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "crontab" in cmd[0] and "-l" in cmd:
                mock.stdout = _state["crontab"]
            elif "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                _state["crontab"] = content
                mock.stdout = ""
            else:
                mock.stdout = ""
            return mock

        monkeypatch.setattr("subprocess.run", _stateful_run)

        install_schedule()
        status = get_status()

        assert status["daily_enabled"] is True
        assert status["weekly_enabled"] is True

    def test_get_status_after_uninstall_shows_installed_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _state: dict[str, str] = {"crontab": ""}

        def _stateful_run(
            cmd: list[str],
            *,
            input: bytes | str | None = None,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            **kwargs: Any,
        ) -> MagicMock:
            mock = MagicMock()
            mock.returncode = 0
            mock.stderr = ""
            if "crontab" in cmd[0] and "-l" in cmd:
                mock.stdout = _state["crontab"]
            elif "crontab" in cmd[0] and "-" in cmd and input is not None:
                content = input.decode("utf-8") if isinstance(input, bytes) else input
                _state["crontab"] = content
                mock.stdout = ""
            else:
                mock.stdout = ""
            return mock

        monkeypatch.setattr("subprocess.run", _stateful_run)

        install_schedule()
        uninstall_schedule()
        status = get_status()

        assert status["installed"] is False

    def test_get_status_fresh_crontab_shows_not_installed(self) -> None:
        # The autouse fixture gives us an empty crontab by default.
        status = get_status()
        assert status["installed"] is False
        assert status["daily_enabled"] is False
        assert status["weekly_enabled"] is False
