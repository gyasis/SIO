"""Metrics package — learning velocity, correction decay, adaptation speed."""

from sio.core.metrics.velocity import compute_velocity_snapshot, get_velocity_trends

__all__ = ["compute_velocity_snapshot", "get_velocity_trends"]
