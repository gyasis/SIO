"""Tests for sio.reporting.search_discipline — US6 T060 + T061.

T060: discipline report emits recency-rate, multi-hop-rate, files-first-rate,
      context-walk-rate over a window from invocation telemetry.

T061: a sub-target rate is flagged in sio briefing (AS-2).

Rate definitions (from research.md §B + BASELINE.md):
  recency-rate       = invocations with --recent            / total search invocations
  multi-hop-rate     = invocations with --refine|--strategy  / total search invocations
                       (--within/--use-cache are sio-suggest-only; never on search)
  files-first-rate   = invocations with --files             / total search invocations
  context-walk-rate  = invocations with --context|--around  / total search invocations

Targets (BASELINE.md § "Target deltas"):
  recency-first   >= 85%
  multi-hop       >= 5%
  context walk-back >= 15%
  (files-first has no BASELINE target; reported for observability only)

Telemetry source: behavior_invocations.db, table behavior_invocations,
  column tool_input (JSON, key "command") for tool_name = 'Bash'.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from sio.core.db.schema import init_db

# ---------------------------------------------------------------------------
# Helpers for building fake telemetry rows
# ---------------------------------------------------------------------------


def _ts(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _bash_row(command: str, days_ago: int = 0) -> dict:
    """Return a minimal behavior_invocations row dict for a Bash call."""
    return {
        "session_id": f"test-session-{days_ago}-{hash(command) % 9999:04d}",
        "timestamp": _ts(days_ago),
        "platform": "claude-code",
        "user_message": "test",
        "behavior_type": "skill",
        "actual_action": "Bash",
        "tool_name": "Bash",
        "tool_input": json.dumps({"command": command}),
    }


def _insert_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for row in rows:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO behavior_invocations ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Fake invocations DB fixture (isolated temp DB, not the real ~/.sio one)
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_invocations_db(tmp_path) -> sqlite3.Connection:
    """In-memory behavior_invocations DB pre-populated with known command rows.

    Layout (all within the last 14 days):
      10 search invocations total:
        5 with --recent        → recency-rate   = 5/10 = 50%
        1 with --refine        → multi-hop-rate = 1/10 = 10%
        3 with --files         → files-first    = 3/10 = 30%
        1 with --context       → context-walk   = 1/10 = 10%
      2 non-search Bash rows   → should NOT be counted
      1 search row 30 days ago → outside default 14-day window

    This gives deterministic per-discipline counts for assertions.
    """
    db_path = str(tmp_path / "behavior_invocations.db")
    conn = init_db(db_path)

    search_rows = [
        # 5 with --recent
        _bash_row("session-search foo --recent 7", days_ago=1),
        _bash_row("session-search bar --recent 14", days_ago=2),
        _bash_row("sio search baz --recent 7", days_ago=3),
        _bash_row("session-search xyz --recent 30", days_ago=4),
        _bash_row("sio search qux --recent 7 --files", days_ago=5),  # recent + files
        # 1 with --refine only
        _bash_row("sio search abc --refine narrowing", days_ago=6),
        # 1 with --files only (not --recent)
        _bash_row("session-search def --files", days_ago=7),
        # 1 with --files only (different command)
        _bash_row("sio search ghi --files --count", days_ago=8),
        # 1 with --context
        _bash_row("session-search jkl --context 3", days_ago=9),
        # 1 plain (no discipline flags)
        _bash_row("session-search mno", days_ago=10),
    ]
    # 2 non-search Bash rows (should be ignored)
    non_search_rows = [
        _bash_row("git status", days_ago=1),
        _bash_row("ls /tmp", days_ago=2),
    ]
    # 1 search row outside 14-day window (should be excluded in windowed query)
    old_search_row = [
        _bash_row("session-search old --recent 7", days_ago=30),
    ]

    _insert_rows(conn, search_rows + non_search_rows + old_search_row)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# T060 — discipline report emits per-rate values from telemetry
# ---------------------------------------------------------------------------


class TestDisciplineReport:
    """T060: the report function computes correct per-discipline rates."""

    def test_returns_all_four_rates(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=14)
        assert "recency_rate" in rates
        assert "multi_hop_rate" in rates
        assert "files_first_rate" in rates
        assert "context_walk_rate" in rates

    def test_total_invocations_within_window(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=14)
        # 10 search rows are within 14 days; the old row (30 days ago) is excluded
        assert rates["total_search_invocations"] == 10

    def test_recency_rate_correct(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=14)
        # 5 rows have --recent (rows 1-5, including the --recent --files combo)
        assert abs(rates["recency_rate"] - 0.50) < 0.001

    def test_multi_hop_rate_correct(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=14)
        # 1 row has --refine (multi-hop signal)
        assert abs(rates["multi_hop_rate"] - 0.10) < 0.001

    def test_files_first_rate_correct(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=14)
        # 3 rows have --files (rows 5, 7, 8 — row 5 is --recent --files combo)
        assert abs(rates["files_first_rate"] - 0.30) < 0.001

    def test_context_walk_rate_correct(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=14)
        # 1 row has --context
        assert abs(rates["context_walk_rate"] - 0.10) < 0.001

    def test_old_rows_excluded_by_window(self, fake_invocations_db):
        """The 30-day-old search row must not be counted in a 14-day window."""
        from sio.reporting.search_discipline import compute_discipline_rates

        narrow = compute_discipline_rates(fake_invocations_db, window_days=14)
        wide = compute_discipline_rates(fake_invocations_db, window_days=60)
        assert wide["total_search_invocations"] == 11  # includes the old row
        assert narrow["total_search_invocations"] == 10

    def test_non_search_rows_excluded(self, fake_invocations_db):
        """Git/ls Bash rows must not affect search counts."""
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=14)
        # Only 10 genuine search rows in window, not 12
        assert rates["total_search_invocations"] == 10

    def test_empty_db_returns_zeros(self, tmp_path):
        """Empty DB should return zero rates without crashing."""
        from sio.reporting.search_discipline import compute_discipline_rates

        conn = init_db(str(tmp_path / "empty.db"))
        rates = compute_discipline_rates(conn, window_days=14)
        assert rates["total_search_invocations"] == 0
        assert rates["recency_rate"] == 0.0
        assert rates["multi_hop_rate"] == 0.0
        assert rates["files_first_rate"] == 0.0
        assert rates["context_walk_rate"] == 0.0
        conn.close()


class TestDisciplineFlagSemantics:
    """BUGFIX lock-in: grade only real flags; credit --around; exclude phantoms."""

    def test_phantom_flags_do_not_count_as_multi_hop(self, tmp_path):
        """--within / --use-cache are sio-suggest-only and must NOT count."""
        from sio.reporting.search_discipline import compute_discipline_rates

        conn = init_db(str(tmp_path / "phantom.db"))
        _insert_rows(
            conn,
            [
                _bash_row("sio search a --within cache.csv", days_ago=1),
                _bash_row("sio search b --use-cache", days_ago=2),
            ],
        )
        rates = compute_discipline_rates(conn, window_days=14)
        assert rates["total_search_invocations"] == 2
        assert rates["multi_hop_rate"] == 0.0  # neither phantom flag counts
        conn.close()

    def test_strategy_counts_as_multi_hop(self, tmp_path):
        """--strategy is a real sio-search narrowing flag → multi-hop."""
        from sio.reporting.search_discipline import compute_discipline_rates

        conn = init_db(str(tmp_path / "strategy.db"))
        _insert_rows(
            conn,
            [_bash_row("sio search a --refine x --strategy filter", days_ago=1)],
        )
        rates = compute_discipline_rates(conn, window_days=14)
        assert rates["multi_hop_rate"] == 1.0
        conn.close()

    def test_around_counts_as_context_walk(self, tmp_path):
        """--around is the modern role-aware context walk → context-walk credit."""
        from sio.reporting.search_discipline import compute_discipline_rates

        conn = init_db(str(tmp_path / "around.db"))
        _insert_rows(
            conn,
            [
                _bash_row("sio search a --around 3", days_ago=1),
                _bash_row("session-search b --context 2", days_ago=2),
            ],
        )
        rates = compute_discipline_rates(conn, window_days=14)
        assert rates["context_walk_rate"] == 1.0  # both --around and --context
        conn.close()


# ---------------------------------------------------------------------------
# T060 — also–stars: windows respects `window_days` parameter
# ---------------------------------------------------------------------------


class TestDisciplineWindow:
    """Window parameter controls lookback."""

    def test_window_days_0_includes_all(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        rates = compute_discipline_rates(fake_invocations_db, window_days=0)
        # window_days=0 means no cutoff → all 11 rows (10 recent + 1 old)
        assert rates["total_search_invocations"] == 11

    def test_narrow_window_fewer_than_full(self, fake_invocations_db):
        from sio.reporting.search_discipline import compute_discipline_rates

        # A narrow 5-day window should capture fewer rows than a 14-day window.
        # rows with days_ago in {1,2,3,4,5} vs {1..10} → 5 vs 10.
        # We use a large-enough window to avoid microsecond boundary issues
        # (timedelta boundaries are tested at the 14-day level where the fixture
        # has a hard gap between days_ago=10 and days_ago=30).
        narrow = compute_discipline_rates(fake_invocations_db, window_days=6)
        wide = compute_discipline_rates(fake_invocations_db, window_days=14)
        assert narrow["total_search_invocations"] < wide["total_search_invocations"]


# ---------------------------------------------------------------------------
# T061 — sub-target rate is flagged in sio briefing
# ---------------------------------------------------------------------------


class TestBriefingRegressionFlag:
    """T061: when a rate is below its BASELINE target, briefing surfaces a flag."""

    def _make_briefing_db(self, tmp_path) -> sqlite3.Connection:
        """Return an in-memory SIO DB (for briefing), not the invocations DB."""
        return init_db(str(tmp_path / "sio.db"))

    def test_flag_present_when_all_rates_sub_target(self, tmp_path):
        """All rates below target → regression flag appears in briefing."""
        from sio.reporting.search_discipline import compute_discipline_rates
        from sio.suggestions.consultant import build_session_briefing

        sio_db = self._make_briefing_db(tmp_path)
        invocations_db = init_db(str(tmp_path / "invocations.db"))

        # Insert rows with 0% recency, 0% multi-hop, 0% context (all sub-target)
        plain_rows = [_bash_row("session-search foo", days_ago=i) for i in range(1, 6)]
        _insert_rows(invocations_db, plain_rows)

        rates = compute_discipline_rates(invocations_db, window_days=14)

        with (
            patch("sio.suggestions.consultant._get_rule_file_paths", return_value=[]),
            patch("sio.suggestions.consultant._section_budget", return_value=None),
            patch(
                "sio.suggestions.consultant._section_search_discipline",
                return_value=_discipline_flag_section(rates),
            ),
        ):
            result = build_session_briefing(sio_db)

        assert "Search Discipline" in result or "search-discipline" in result.lower()
        sio_db.close()
        invocations_db.close()

    def test_flag_absent_when_all_rates_meet_target(self, tmp_path):
        """All rates above target → no regression flag."""
        from sio.reporting.search_discipline import TARGETS, compute_discipline_rates
        from sio.suggestions.consultant import build_session_briefing

        sio_db = self._make_briefing_db(tmp_path)
        invocations_db = init_db(str(tmp_path / "invocations.db"))

        # Build 20 rows that exceed ALL targets:
        #   recency    >= 85%  → 18 / 20 = 90%  ✓
        #   multi-hop  >= 5%   →  2 / 20 = 10%  ✓
        #   context    >= 15%  →  4 / 20 = 20%  ✓
        # (rows with --recent --context count for BOTH recency AND context)
        rows = []
        # 14 plain --recent rows
        for i in range(14):
            rows.append(_bash_row(f"session-search t{i} --recent 7", days_ago=i % 13 + 1))
        # 4 --recent --context rows (boost both recency and context)
        for i in range(4):
            rows.append(
                _bash_row(f"session-search c{i} --recent 7 --context 3", days_ago=i + 1)
            )
        # 2 --refine rows (multi-hop; no --recent, so recency unchanged)
        rows.append(_bash_row("sio search x --refine narrow", days_ago=1))
        rows.append(_bash_row("sio search y --refine cache", days_ago=2))
        # Total: 20 rows, 18 with --recent (14+4), 2 with --refine, 4 with --context
        _insert_rows(invocations_db, rows)

        rates = compute_discipline_rates(invocations_db, window_days=14)

        # Verify our fixture actually satisfies the targets
        assert rates["recency_rate"] >= TARGETS["recency_rate"]      # 18/20 = 90%
        assert rates["multi_hop_rate"] >= TARGETS["multi_hop_rate"]   # 2/20  = 10%
        assert rates["context_walk_rate"] >= TARGETS["context_walk_rate"]  # 4/20 = 20%

        with (
            patch("sio.suggestions.consultant._get_rule_file_paths", return_value=[]),
            patch("sio.suggestions.consultant._section_budget", return_value=None),
            patch(
                "sio.suggestions.consultant._section_search_discipline",
                return_value=None,  # no flag when all rates healthy
            ),
        ):
            result = build_session_briefing(sio_db)

        # With mocked section returning None, regression section absent
        assert "recency" not in result.lower() or "Search Discipline" not in result
        sio_db.close()
        invocations_db.close()

    def test_section_search_discipline_sub_target(self, fake_invocations_db, tmp_path):
        """_section_search_discipline returns a flag string when rate is below target."""
        from sio.suggestions.consultant import _section_search_discipline

        # fake_invocations_db has recency_rate=50% (below 85% target)
        result = _section_search_discipline(fake_invocations_db, window_days=14)
        assert result is not None
        assert "recency" in result.lower() or "Search Discipline" in result

    def test_section_search_discipline_healthy(self, tmp_path):
        """_section_search_discipline returns None when all rates meet target."""
        from sio.suggestions.consultant import _section_search_discipline

        invocations_db = init_db(str(tmp_path / "healthy.db"))
        # 20 search rows: all with --recent and --context to exceed targets
        rows = []
        for i in range(17):
            rows.append(
                _bash_row(f"session-search t{i} --recent 7 --context 3", days_ago=i % 13 + 1)
            )
        for i in range(2):
            rows.append(_bash_row(f"sio search r{i} --refine narrow", days_ago=i + 1))
        rows.append(_bash_row("session-search z --recent 7", days_ago=1))
        _insert_rows(invocations_db, rows)

        result = _section_search_discipline(invocations_db, window_days=14)
        assert result is None
        invocations_db.close()

    def test_section_returns_none_on_empty_telemetry(self, tmp_path):
        """Empty invocations DB → no flag (degrade gracefully)."""
        from sio.suggestions.consultant import _section_search_discipline

        empty_db = init_db(str(tmp_path / "empty.db"))
        result = _section_search_discipline(empty_db, window_days=14)
        assert result is None
        empty_db.close()


# ---------------------------------------------------------------------------
# Integration: briefing doesn't crash without telemetry DB
# ---------------------------------------------------------------------------


class TestBriefingGracefulDegradation:
    """sio briefing degrades gracefully with no invocations telemetry."""

    def test_briefing_no_invocations_env(self, tmp_path):
        """briefing with SIO_INVOCATIONS_DB_PATH pointing at non-existent file."""
        from sio.suggestions.consultant import build_session_briefing

        sio_db = init_db(str(tmp_path / "sio.db"))
        nonexistent = str(tmp_path / "does_not_exist.db")

        with (
            patch("sio.suggestions.consultant._get_rule_file_paths", return_value=[]),
            patch("sio.suggestions.consultant._section_budget", return_value=None),
            patch.dict("os.environ", {"SIO_INVOCATIONS_DB_PATH": nonexistent}),
        ):
            result = build_session_briefing(sio_db)

        # Must not crash; no discipline section expected
        assert isinstance(result, str)
        sio_db.close()


# ---------------------------------------------------------------------------
# T073-F3: Non-mocked integration — build_session_briefing calls real
# _section_search_discipline (AS-2 acceptance wiring is exercised end-to-end)
# ---------------------------------------------------------------------------


class TestBriefingDisciplineIntegration:
    """Integration: build_session_briefing → _section_search_discipline wiring.

    This test does NOT mock _section_search_discipline.  Instead it:
    1. Builds a real behavior_invocations.db with known sub-target rows.
    2. Points SIO_INVOCATIONS_DB_PATH at that DB via env var.
    3. Calls build_session_briefing() with only the non-discipline sections
       mocked out (violations, declining, budget, pending, stats — which depend
       on sio.db internals not relevant here).
    4. Asserts the output contains the discipline regression flag without any
       mocking of the _section_search_discipline path.

    This exercises the AS-2 acceptance criterion that the briefing wiring is
    real, not patched — something the over-mocked T061 tests could not verify.
    """

    def test_briefing_includes_discipline_flag_without_mocking_section(
        self, tmp_path
    ):
        """End-to-end: sub-target invocations DB → regression flag in briefing."""
        from sio.suggestions.consultant import build_session_briefing

        # Real SIO db (empty — we only care about discipline section)
        sio_db = init_db(str(tmp_path / "sio.db"))

        # Real behavior_invocations.db with 0% recency (well below 85% target)
        inv_db_path = str(tmp_path / "behavior_invocations.db")
        inv_conn = init_db(inv_db_path)
        # 5 plain search rows, no --recent flag → recency_rate = 0%
        plain_rows = [_bash_row("session-search foo", days_ago=i) for i in range(1, 6)]
        _insert_rows(inv_conn, plain_rows)
        inv_conn.close()

        # Mock only the non-discipline sections that touch sio.db tables we
        # haven't populated.  _section_search_discipline is NOT mocked.
        with (
            patch("sio.suggestions.consultant._section_violations", return_value=None),
            patch("sio.suggestions.consultant._section_declining_rules", return_value=None),
            patch("sio.suggestions.consultant._section_budget", return_value=None),
            patch("sio.suggestions.consultant._section_pending", return_value=None),
            patch("sio.suggestions.consultant._section_session_stats", return_value=None),
            patch.dict("os.environ", {"SIO_INVOCATIONS_DB_PATH": inv_db_path}),
        ):
            result = build_session_briefing(sio_db)

        # The real _section_search_discipline must have fired and produced a flag
        assert "Search Discipline" in result, (
            f"Expected 'Search Discipline' regression flag in briefing, got:\n{result}"
        )
        # The recency metric should be called out (0% vs 85% target)
        assert "recency" in result.lower(), (
            f"Expected recency flag in discipline section, got:\n{result}"
        )
        sio_db.close()


# ---------------------------------------------------------------------------
# Helper: construct the discipline flag section string for patching
# ---------------------------------------------------------------------------


def _discipline_flag_section(rates: dict) -> str | None:
    """Replicate the real section logic for test patching."""
    from sio.reporting.search_discipline import TARGETS

    sub = []
    for metric, target in TARGETS.items():
        rate = rates.get(metric, 0.0)
        if rate < target:
            sub.append(f"{metric}={rate:.0%} (target ≥{target:.0%})")
    if not sub:
        return None
    return "## Search Discipline Regression\n- " + "\n- ".join(sub)
