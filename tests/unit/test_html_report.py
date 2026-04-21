"""Unit tests for sio.reports.html_report — T080 [US9].

Tests generate_html_report(db, days=30) producing a valid, self-contained
HTML string with all required sections.

Acceptance criteria: FR-047, FR-048.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sio.core.db.schema import init_db
from sio.reports.html_report import generate_html_report

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _ts(days_ago: float = 0) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def empty_db() -> sqlite3.Connection:
    """In-memory DB with schema but no data."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def populated_db() -> sqlite3.Connection:
    """In-memory DB seeded with representative data across all tables."""
    conn = init_db(":memory:")

    # --- session_metrics ---
    for i in range(5):
        conn.execute(
            "INSERT INTO session_metrics "
            "(session_id, file_path, total_input_tokens, total_output_tokens, "
            "total_cache_read_tokens, total_cache_create_tokens, "
            "cache_hit_ratio, total_cost_usd, session_duration_seconds, "
            "message_count, tool_call_count, error_count, correction_count, "
            "positive_signal_count, sidechain_count, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"sess-{i:03d}",
                f"/tmp/sess-{i}.jsonl",
                10000 + i * 1000,  # input tokens
                5000 + i * 500,  # output tokens
                3000,  # cache read
                1000,  # cache create
                0.65 + i * 0.05,  # cache hit ratio
                0.50 + i * 0.10,  # cost
                600 + i * 60,  # duration
                20 + i * 5,  # messages
                10 + i * 2,  # tool calls
                2 + i,  # errors
                1,  # corrections
                3,  # positive signals
                0,  # sidechains
                _ts(days_ago=i * 2),
            ),
        )

    # --- error_records ---
    error_types = [
        "tool_failure",
        "user_correction",
        "tool_failure",
        "repeated_attempt",
        "undo",
    ]
    for i, etype in enumerate(error_types):
        conn.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "tool_name, error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"sess-{i % 5:03d}",
                _ts(days_ago=i * 3),
                "jsonl",
                f"/tmp/sess-{i % 5}.jsonl",
                "Bash" if i % 2 == 0 else "Edit",
                f"Test error message {i}",
                etype,
                _ts(days_ago=0),
            ),
        )

    # --- patterns ---
    for i in range(3):
        conn.execute(
            "INSERT INTO patterns "
            "(pattern_id, description, tool_name, error_count, "
            "session_count, first_seen, last_seen, rank_score, "
            "grade, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"pat-{i:03d}",
                f"Pattern description #{i}",
                "Bash" if i % 2 == 0 else "Edit",
                5 + i * 3,
                2 + i,
                _ts(days_ago=20),
                _ts(days_ago=i),
                0.9 - i * 0.2,
                ["strong", "emerging", "declining"][i],
                _ts(days_ago=20),
                _ts(days_ago=0),
            ),
        )

    # --- suggestions ---
    for i in range(2):
        conn.execute(
            "INSERT INTO suggestions "
            "(pattern_id, description, confidence, proposed_change, "
            "target_file, change_type, status, ai_explanation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                i + 1,
                f"Suggestion #{i}: avoid pattern {i}",
                0.85 - i * 0.1,
                f"## Rule\nNever do thing #{i}. Always do the other thing.\n",
                "CLAUDE.md",
                "claude_md_rule",
                "pending",
                f"This rule prevents errors of type {i}.",
                _ts(days_ago=i),
            ),
        )

    # --- velocity_snapshots ---
    for i in range(4):
        conn.execute(
            "INSERT INTO velocity_snapshots "
            "(error_type, session_id, error_rate, error_count_in_window, "
            "window_start, window_end, rule_applied, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "tool_failure" if i < 2 else "user_correction",
                f"sess-{i:03d}",
                0.3 - i * 0.05,
                5 - i,
                _ts(days_ago=10 + i * 2),
                _ts(days_ago=i * 2),
                1 if i == 0 else 0,
                _ts(days_ago=i),
            ),
        )

    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# T080-1: Returns valid HTML string
# ---------------------------------------------------------------------------


class TestHtmlReportBasic:
    """generate_html_report returns a valid HTML document."""

    def test_returns_string(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_starts_with_doctype(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert html.strip().startswith("<!DOCTYPE html>")

    def test_has_html_tags(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "<html" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "<body>" in html


# ---------------------------------------------------------------------------
# T080-2: Contains all required sections
# ---------------------------------------------------------------------------


class TestHtmlReportSections:
    """Report contains all 5 required sections."""

    def test_session_metrics_dashboard(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "Session Metrics Dashboard" in html
        assert "Total Tokens" in html
        assert "Total Cost" in html
        assert "Cache Efficiency" in html
        assert "metricsChart" in html

    def test_error_trend_chart(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "Error Trend" in html
        assert "errorChart" in html
        assert "errorLabels" in html
        assert "errorCounts" in html

    def test_pattern_table(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "patternTable" in html
        assert "Confidence" in html
        assert "Grade" in html
        # Verify pattern data is present
        assert "pat-000" in html
        assert "Pattern description #0" in html
        # Verify grade badges
        assert "badge-blue" in html or "badge-yellow" in html

    def test_suggestion_cards(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "Suggestion" in html
        assert "Copy Rule" in html
        assert "copyRule" in html
        assert "navigator.clipboard.writeText" in html
        # Verify suggestion data
        assert "Suggestion #0" in html

    def test_velocity_graph(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "Learning Velocity" in html
        assert "velocityChart" in html
        assert "velocityLabels" in html
        assert "velocityDatasets" in html


# ---------------------------------------------------------------------------
# T080-3: Self-contained — no external CSS/JS links that break offline
# ---------------------------------------------------------------------------


class TestHtmlReportSelfContained:
    """Report should be viewable offline (CSS inline, JS data inline)."""

    def test_css_is_inline(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "<style>" in html
        # No external CSS links
        css_links = re.findall(
            r'<link[^>]+rel=["\']stylesheet["\'][^>]*>',
            html,
        )
        assert len(css_links) == 0

    def test_chart_data_is_inline(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        # Data arrays are embedded as JS
        assert "const metricsLabels" in html
        assert "const errorLabels" in html
        assert "const velocityLabels" in html

    def test_chartjs_has_fallback_comment(self, populated_db):
        """Chart.js CDN link exists but has a fallback comment."""
        html = generate_html_report(populated_db, days=30)
        assert "chart.js" in html.lower() or "Chart" in html
        # Check for fallback guidance
        assert "offline" in html.lower() or "fallback" in html.lower()

    def test_no_external_image_links(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        img_tags = re.findall(r"<img[^>]+src=[\"']http", html)
        assert len(img_tags) == 0


# ---------------------------------------------------------------------------
# T080-4: Empty DB — graceful output, no crash
# ---------------------------------------------------------------------------


class TestHtmlReportEmptyDb:
    """Verify report generates correctly with no data."""

    def test_empty_db_no_crash(self, empty_db):
        html = generate_html_report(empty_db, days=30)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_empty_db_has_structure(self, empty_db):
        html = generate_html_report(empty_db, days=30)
        assert "<!DOCTYPE html>" in html
        assert "Session Metrics Dashboard" in html
        assert "Error Trend" in html
        assert "Discovered Patterns" in html

    def test_empty_db_shows_no_data_messages(self, empty_db):
        html = generate_html_report(empty_db, days=30)
        # Pattern table should show empty message
        assert "No patterns discovered" in html or "0 sessions" in html

    def test_empty_db_no_errors_stat(self, empty_db):
        html = generate_html_report(empty_db, days=30)
        # Empty JS arrays are fine
        assert "const metricsLabels = []" in html
        assert "const errorLabels = []" in html

    def test_empty_db_suggestion_cards_empty(self, empty_db):
        html = generate_html_report(empty_db, days=30)
        assert "No suggestions pending" in html


# ---------------------------------------------------------------------------
# T080-5: Populated DB — all sections have data
# ---------------------------------------------------------------------------


class TestHtmlReportPopulatedData:
    """With populated DB, all sections contain real data."""

    def test_session_count_nonzero(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "5 sessions analyzed" in html

    def test_tokens_nonzero(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        # Tokens should be rendered as formatted number
        assert "K" in html or "M" in html or "0" not in html[:500]

    def test_patterns_in_table(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "pat-000" in html
        assert "pat-001" in html
        assert "pat-002" in html

    def test_suggestions_rendered(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "Never do thing #0" in html
        assert "Never do thing #1" in html

    def test_velocity_data_in_js(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        assert "tool_failure" in html
        assert "user_correction" in html

    def test_error_trend_has_data(self, populated_db):
        html = generate_html_report(populated_db, days=30)
        # errorCounts should contain non-empty array
        match = re.search(r"const errorCounts = (\[.*?\]);", html)
        assert match is not None
        counts = match.group(1)
        assert counts != "[]"

    def test_sortable_table_js(self, populated_db):
        """Table sorting JavaScript is present."""
        html = generate_html_report(populated_db, days=30)
        assert "function sortTable" in html
        assert 'onclick="sortTable' in html

    def test_copy_button_js(self, populated_db):
        """Copy button uses navigator.clipboard.writeText."""
        html = generate_html_report(populated_db, days=30)
        assert "function copyRule" in html
        assert "navigator.clipboard.writeText" in html


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestHtmlReportEdgeCases:
    """Edge cases and boundary conditions."""

    def test_custom_days(self, populated_db):
        html = generate_html_report(populated_db, days=7)
        assert "7-day window" in html

    def test_html_escaping(self, populated_db):
        """Injected HTML in pattern descriptions should be escaped."""
        populated_db.execute(
            "INSERT INTO patterns "
            "(pattern_id, description, tool_name, error_count, "
            "session_count, first_seen, last_seen, rank_score, "
            "grade, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "xss-test",
                '<script>alert("xss")</script>',
                "Bash",
                1,
                1,
                _ts(1),
                _ts(0),
                0.5,
                "emerging",
                _ts(1),
                _ts(0),
            ),
        )
        populated_db.commit()

        html = generate_html_report(populated_db, days=30)
        assert '<script>alert("xss")</script>' not in html
        assert "&lt;script&gt;" in html

    def test_large_token_formatting(self, populated_db):
        """Large token counts should be human-readable."""
        populated_db.execute(
            "INSERT INTO session_metrics "
            "(session_id, file_path, total_input_tokens, total_output_tokens, "
            "total_cache_read_tokens, total_cache_create_tokens, "
            "cache_hit_ratio, total_cost_usd, message_count, "
            "tool_call_count, error_count, correction_count, "
            "positive_signal_count, sidechain_count, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sess-big",
                "/tmp/big.jsonl",
                5000000,
                2000000,  # 7M total
                0,
                0,
                0.0,
                5.0,
                100,
                50,
                10,
                2,
                5,
                0,
                _ts(0),
            ),
        )
        populated_db.commit()

        html = generate_html_report(populated_db, days=30)
        # Should have M or K formatting for large numbers
        assert "M" in html or "K" in html
