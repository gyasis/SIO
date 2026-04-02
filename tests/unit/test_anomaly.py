"""T070 [US8] Unit tests for MAD anomaly detection."""

from __future__ import annotations

import pytest

from sio.core.arena.anomaly import compute_mad, detect_anomalies
from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory database with schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _insert_sessions(db, metric_name: str, values: list[tuple[str, float]]):
    """Insert session_metrics rows with the given metric values."""
    for session_id, value in values:
        db.execute(
            f"INSERT INTO session_metrics "
            f"(session_id, file_path, {metric_name}, mined_at) "
            f"VALUES (?, ?, ?, '2026-04-01T00:00:00Z')",
            (session_id, f"/fake/{session_id}.jsonl", value),
        )
    db.commit()


# ---------------------------------------------------------------------------
# compute_mad
# ---------------------------------------------------------------------------


class TestComputeMad:

    def test_empty_input(self):
        median, mad = compute_mad([])
        assert median == 0.0
        assert mad == 0.0

    def test_single_value(self):
        median, mad = compute_mad([5.0])
        assert median == 5.0
        assert mad == 0.0

    def test_two_values(self):
        median, mad = compute_mad([1.0, 3.0])
        # median = 2.0, deviations = [1.0, 1.0], MAD = 1.0
        assert median == pytest.approx(2.0)
        assert mad == pytest.approx(1.0)

    def test_odd_count(self):
        median, mad = compute_mad([1.0, 2.0, 3.0, 4.0, 5.0])
        # median = 3.0, deviations = [0, 1, 1, 2, 2], sorted MAD = 1.0
        assert median == pytest.approx(3.0)
        assert mad == pytest.approx(1.0)

    def test_even_count(self):
        median, mad = compute_mad([1.0, 2.0, 3.0, 4.0])
        # median = 2.5, devs = [0.5, 0.5, 1.5, 1.5], sorted MAD = (0.5+1.5)/2 = 1.0
        assert median == pytest.approx(2.5)
        assert mad == pytest.approx(1.0)

    def test_identical_values(self):
        median, mad = compute_mad([7.0, 7.0, 7.0, 7.0, 7.0])
        assert median == pytest.approx(7.0)
        assert mad == pytest.approx(0.0)

    def test_known_dataset(self):
        # Classic MAD example: [1, 1, 2, 2, 4, 6, 9]
        # median = 2, deviations = [0, 0, 1, 1, 2, 4, 7], MAD = 1
        median, mad = compute_mad([1.0, 1.0, 2.0, 2.0, 4.0, 6.0, 9.0])
        assert median == pytest.approx(2.0)
        assert mad == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# detect_anomalies — 10 normal + 1 outlier
# ---------------------------------------------------------------------------


class TestDetectAnomalies:

    def test_flags_outlier_error_count(self, db):
        """10 normal sessions + 1 outlier with extreme error count."""
        normal = [(f"s{i}", 5.0) for i in range(10)]
        outlier = [("s_outlier", 100.0)]
        _insert_sessions(db, "error_count", normal + outlier)

        anomalous = detect_anomalies(db, "error_count", threshold_mads=3)
        assert "s_outlier" in anomalous
        # Normal sessions should not be flagged
        for i in range(10):
            assert f"s{i}" not in anomalous

    def test_flags_outlier_cost(self, db):
        """Outlier session with extreme cost."""
        normal = [(f"s{i}", 0.50) for i in range(10)]
        outlier = [("s_expensive", 50.0)]
        _insert_sessions(db, "total_cost_usd", normal + outlier)

        anomalous = detect_anomalies(db, "total_cost_usd", threshold_mads=3)
        assert "s_expensive" in anomalous

    def test_no_anomalies_when_all_similar(self, db):
        """All sessions have similar values — no anomalies."""
        sessions = [(f"s{i}", 10.0 + i * 0.1) for i in range(10)]
        _insert_sessions(db, "error_count", sessions)

        anomalous = detect_anomalies(db, "error_count", threshold_mads=3)
        assert len(anomalous) == 0

    def test_returns_empty_with_insufficient_data(self, db):
        """Fewer than 3 sessions — not enough data."""
        _insert_sessions(db, "error_count", [("s1", 5.0), ("s2", 10.0)])

        anomalous = detect_anomalies(db, "error_count")
        assert anomalous == []

    def test_handles_all_identical_values_with_outlier(self, db):
        """When MAD=0, any value != median is anomalous."""
        sessions = [(f"s{i}", 5.0) for i in range(10)]
        sessions.append(("s_odd", 50.0))
        _insert_sessions(db, "error_count", sessions)

        anomalous = detect_anomalies(db, "error_count", threshold_mads=3)
        assert "s_odd" in anomalous

    def test_rejects_unsupported_metric(self, db):
        with pytest.raises(ValueError, match="Unsupported metric"):
            detect_anomalies(db, "nonexistent_column")

    def test_supports_session_duration(self, db):
        """Test with session_duration_seconds metric."""
        normal = [(f"s{i}", 300.0) for i in range(10)]
        outlier = [("s_long", 36000.0)]  # 10 hours
        _insert_sessions(db, "session_duration_seconds", normal + outlier)

        anomalous = detect_anomalies(
            db, "session_duration_seconds", threshold_mads=3,
        )
        assert "s_long" in anomalous

    def test_supports_token_metrics(self, db):
        """Test with total_input_tokens."""
        normal = [(f"s{i}", 1000.0) for i in range(10)]
        outlier = [("s_heavy", 500000.0)]
        _insert_sessions(db, "total_input_tokens", normal + outlier)

        anomalous = detect_anomalies(
            db, "total_input_tokens", threshold_mads=3,
        )
        assert "s_heavy" in anomalous

    def test_custom_threshold(self, db):
        """Stricter threshold flags more sessions."""
        # Values with moderate spread
        sessions = [(f"s{i}", float(i * 10)) for i in range(11)]
        _insert_sessions(db, "error_count", sessions)

        strict = detect_anomalies(db, "error_count", threshold_mads=1.5)
        loose = detect_anomalies(db, "error_count", threshold_mads=5.0)
        assert len(strict) >= len(loose)
