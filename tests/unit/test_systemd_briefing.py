"""Unit tests for the systemd user-timer unit generation (no real install)."""

from __future__ import annotations

from sio.scheduler import systemd_briefing as sb


class TestUnitContent:
    def test_service_unit_is_off_session_throttled(self):
        unit = sb._service_unit("/usr/bin/python3", 6)
        assert "Type=oneshot" in unit
        assert "Nice=19" in unit
        assert "IOSchedulingClass=idle" in unit
        # Runs the idle-gated refresh via `python -m sio`.
        assert "-m sio briefing --refresh --if-idle" in unit
        assert "/usr/bin/python3" in unit
        assert "SIO_BRIEFING_TTL=21600" in unit

    def test_timer_uses_oncalendar_and_persistent(self):
        unit = sb._timer_unit(6)
        # Persistent catch-up requires a realtime (OnCalendar) timer.
        assert "OnCalendar=*-*-* 0/6:00:00" in unit
        assert "Persistent=true" in unit
        assert "OnBootSec=3min" in unit
        assert "WantedBy=timers.target" in unit

    def test_interval_is_parameterised(self):
        assert "0/3:00:00" in sb._timer_unit(3)
        assert "SIO_BRIEFING_TTL=10800" in sb._service_unit("/x/py", 3)


class TestAvailability:
    def test_available_returns_bool(self):
        # Environment-dependent; just assert it never raises and returns a bool.
        assert isinstance(sb.available(), bool)

    def test_status_shape(self):
        st = sb.status()
        assert "available" in st
