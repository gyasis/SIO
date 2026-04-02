"""Unit tests for sio.mining.violation_detector — T046 [US5].

Tests the rule violation detection system that parses instruction file rules
and matches mined errors against them to detect enforcement failures (FR-026,
FR-027).

Functions under test:
    parse_rules(file_path) -> list[Rule]
    detect_violations(rules, error_records) -> list[Violation]
    get_violation_report(db, rule_file_paths) -> dict
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from sio.mining.violation_detector import (
    Rule,
    Violation,
    detect_violations,
    get_violation_report,
    parse_rules,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SESSION_ID = "test-session-001"
_TS_BASE = "2026-02-25T10:00:{:02d}.000Z"


def _ts(offset: int = 0) -> str:
    """Return a deterministic ISO-8601 timestamp at second *offset*."""
    return _TS_BASE.format(offset % 60)


def _error_record(
    error_text: str,
    *,
    error_type: str = "tool_failure",
    session_id: str = _SESSION_ID,
    timestamp: str | None = None,
    tool_name: str | None = None,
    user_message: str | None = None,
    context_before: str | None = None,
    context_after: str | None = None,
    tool_input: str | None = None,
    tool_output: str | None = None,
) -> dict:
    """Build a minimal error record dict matching the error_records schema."""
    return {
        "id": None,
        "session_id": session_id,
        "timestamp": timestamp or _ts(0),
        "source_type": "specstory",
        "source_file": "test-session.md",
        "tool_name": tool_name,
        "error_text": error_text,
        "user_message": user_message,
        "context_before": context_before,
        "context_after": context_after,
        "error_type": error_type,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "mined_at": _ts(59),
    }


@pytest.fixture()
def rules_file(tmp_path: Path) -> Path:
    """Create a sample markdown instruction file with imperative rules."""
    content = textwrap.dedent("""\
        # My Project Rules

        ## Code Style

        - Never use SELECT * in SQL queries
        - Always use absolute paths in tool calls
        - MUST include type hints on all public functions
        - DO NOT commit .env files to the repository

        ## Git Workflow

        Some general description text here that is not a rule.

        - never use git push --force on main branch
        - always run tests before committing

        ## Notes

        This is just informational text with no imperative language.
        See the docs for more details.
    """)
    fp = tmp_path / "CLAUDE.md"
    fp.write_text(content)
    return fp


@pytest.fixture()
def empty_rules_file(tmp_path: Path) -> Path:
    """Create an instruction file with no imperative rules."""
    content = textwrap.dedent("""\
        # Project Notes

        This is informational text only.
        No rules or constraints here.
        Just documentation.
    """)
    fp = tmp_path / "notes.md"
    fp.write_text(content)
    return fp


@pytest.fixture()
def rules_with_code_blocks(tmp_path: Path) -> Path:
    """Create an instruction file with imperative text inside code blocks."""
    content = textwrap.dedent("""\
        # Rules

        - Never delete production data

        ```python
        # This MUST NOT be matched as a rule
        x = "always do something"
        ```

        - Always backup before migration
    """)
    fp = tmp_path / "rules-code.md"
    fp.write_text(content)
    return fp


@pytest.fixture()
def in_memory_db() -> sqlite3.Connection:
    """Create an in-memory SIO database with the error_records table."""
    from sio.core.db.schema import init_db

    conn = init_db(":memory:")
    return conn


# ---------------------------------------------------------------------------
# parse_rules tests
# ---------------------------------------------------------------------------


class TestParseRules:
    """Tests for parse_rules() — extracting rules from markdown files."""

    def test_extracts_imperative_rules(self, rules_file: Path) -> None:
        """Standard rules with NEVER/ALWAYS/MUST/DO NOT are extracted."""
        rules = parse_rules(rules_file)
        texts = [r.text for r in rules]

        assert any("SELECT *" in t for t in texts)
        assert any("absolute paths" in t for t in texts)
        assert any("type hints" in t for t in texts)
        assert any(".env" in t for t in texts)
        assert any("git push --force" in t for t in texts)
        assert any("tests before committing" in t for t in texts)

    def test_returns_correct_line_numbers(self, rules_file: Path) -> None:
        """Each Rule has the correct line number from the source file."""
        rules = parse_rules(rules_file)
        # The first rule "Never use SELECT *" is on line 5.
        select_rules = [r for r in rules if "SELECT" in r.text]
        assert len(select_rules) == 1
        assert select_rules[0].line_number == 5

    def test_stores_file_path(self, rules_file: Path) -> None:
        """Each Rule records its originating file path."""
        rules = parse_rules(rules_file)
        assert all(r.file_path == str(rules_file) for r in rules)

    def test_skips_headings_and_blanks(self, rules_file: Path) -> None:
        """Heading lines and blank lines are not parsed as rules."""
        rules = parse_rules(rules_file)
        texts = [r.text for r in rules]
        # Headings should not appear
        assert not any(t.startswith("#") for t in texts)
        # No empty rules
        assert all(t.strip() for t in texts)

    def test_skips_informational_text(self, rules_file: Path) -> None:
        """Lines without imperative language are skipped."""
        rules = parse_rules(rules_file)
        texts = [r.text for r in rules]
        assert not any("general description" in t.lower() for t in texts)
        assert not any("informational text" in t.lower() for t in texts)

    def test_empty_rules_file(self, empty_rules_file: Path) -> None:
        """File with no imperative language returns an empty list."""
        rules = parse_rules(empty_rules_file)
        assert rules == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file returns an empty list without raising."""
        rules = parse_rules(tmp_path / "does-not-exist.md")
        assert rules == []

    def test_skips_content_in_code_blocks(
        self, rules_with_code_blocks: Path,
    ) -> None:
        """Imperative text inside code fences is not extracted as rules."""
        rules = parse_rules(rules_with_code_blocks)
        texts = [r.text for r in rules]

        # These are real rules outside code blocks.
        assert any("production data" in t for t in texts)
        assert any("backup" in t.lower() for t in texts)

        # This is inside a code block — should NOT be extracted.
        assert not any("MUST NOT be matched" in t for t in texts)
        assert len(rules) == 2

    def test_returns_rule_namedtuple(self, rules_file: Path) -> None:
        """Returned items are Rule namedtuples with expected fields."""
        rules = parse_rules(rules_file)
        assert len(rules) > 0
        r = rules[0]
        assert isinstance(r, Rule)
        assert hasattr(r, "text")
        assert hasattr(r, "file_path")
        assert hasattr(r, "line_number")

    def test_case_insensitive_imperative_detection(
        self, tmp_path: Path,
    ) -> None:
        """Both uppercase and lowercase imperative keywords are detected."""
        content = textwrap.dedent("""\
            - NEVER use eval()
            - never hardcode credentials
            - Always validate input
            - always sanitize output
            - MUST log errors
            - must handle exceptions
            - DO NOT ignore warnings
            - do not skip tests
        """)
        fp = tmp_path / "mixed-case.md"
        fp.write_text(content)
        rules = parse_rules(fp)
        assert len(rules) == 8


# ---------------------------------------------------------------------------
# detect_violations tests
# ---------------------------------------------------------------------------


class TestDetectViolations:
    """Tests for detect_violations() — matching errors against rules."""

    def test_select_star_violation_detected(self) -> None:
        """Error containing 'SELECT *' matches rule 'Never use SELECT *'."""
        rules = [
            Rule(
                text="Never use SELECT * in SQL queries",
                file_path="CLAUDE.md",
                line_number=5,
            ),
        ]
        errors = [
            _error_record(
                "Query executed: SELECT * FROM users WHERE id = 1",
                tool_name="Bash",
            ),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 1
        assert violations[0].rule.text == "Never use SELECT * in SQL queries"
        assert violations[0].match_type == "keyword"
        assert violations[0].confidence == 1.0

    def test_relative_path_violation_detected(self) -> None:
        """Error about relative paths matches 'Always use absolute paths'."""
        rules = [
            Rule(
                text="Always use absolute paths in tool calls",
                file_path="CLAUDE.md",
                line_number=6,
            ),
        ]
        errors = [
            _error_record(
                "File not found: relative path './src/main.py' used",
                error_type="tool_failure",
            ),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 1
        assert "absolute paths" in violations[0].rule.text

    def test_no_match_returns_empty(self) -> None:
        """Error about timeout with no matching rule produces no violation."""
        rules = [
            Rule(
                text="Never use SELECT * in SQL queries",
                file_path="CLAUDE.md",
                line_number=5,
            ),
            Rule(
                text="Always use absolute paths in tool calls",
                file_path="CLAUDE.md",
                line_number=6,
            ),
        ]
        errors = [
            _error_record(
                "Connection timed out after 30 seconds",
                error_type="tool_failure",
            ),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 0

    def test_violation_priority_higher_than_patterns(self) -> None:
        """Violations have confidence=1.0 (higher than typical pattern scores)."""
        rules = [
            Rule(text="Never use SELECT *", file_path="CLAUDE.md", line_number=1),
        ]
        errors = [
            _error_record("Used SELECT * FROM orders"),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 1
        # FR-027: violations flagged higher than new patterns.
        # Pattern rank_scores are typically 0.0-1.0; violation confidence is 1.0.
        assert violations[0].confidence == 1.0

    def test_empty_rules_returns_empty(self) -> None:
        """Empty rules list produces no violations."""
        errors = [_error_record("Some error")]
        violations = detect_violations([], errors)
        assert violations == []

    def test_empty_errors_returns_empty(self) -> None:
        """Empty error records list produces no violations."""
        rules = [
            Rule(text="Never use SELECT *", file_path="CLAUDE.md", line_number=1),
        ]
        violations = detect_violations(rules, [])
        assert violations == []

    def test_no_matches_returns_empty(self) -> None:
        """When no error matches any rule, result is empty."""
        rules = [
            Rule(text="Never use SELECT *", file_path="CLAUDE.md", line_number=1),
        ]
        errors = [
            _error_record("Syntax error in Python code"),
            _error_record("Network connection refused"),
        ]
        violations = detect_violations(rules, errors)
        assert violations == []

    def test_case_insensitive_matching(self) -> None:
        """Keyword matching is case-insensitive."""
        rules = [
            Rule(
                text="Never use SELECT * in SQL queries",
                file_path="CLAUDE.md",
                line_number=5,
            ),
        ]
        errors = [
            _error_record("Ran: select * from users"),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 1

    def test_multiple_rules_multiple_errors(self) -> None:
        """Multiple violations across different rules and errors."""
        rules = [
            Rule(text="Never use SELECT *", file_path="CLAUDE.md", line_number=1),
            Rule(
                text="Always use absolute paths",
                file_path="CLAUDE.md",
                line_number=2,
            ),
        ]
        errors = [
            _error_record("SELECT * FROM users"),
            _error_record("relative path used: ./file.txt"),
            _error_record("Connection timed out"),  # no match
        ]

        violations = detect_violations(rules, errors)
        # SELECT * matches first rule; relative path matches second rule
        assert len(violations) == 2

    def test_sorted_by_frequency_then_recency(self) -> None:
        """Violations sorted: most violated rule first, then most recent."""
        rules = [
            Rule(text="Never use SELECT *", file_path="CLAUDE.md", line_number=1),
            Rule(
                text="Always use absolute paths",
                file_path="CLAUDE.md",
                line_number=2,
            ),
        ]
        errors = [
            _error_record(
                "SELECT * FROM users", timestamp="2026-02-25T10:00:01.000Z",
            ),
            _error_record(
                "SELECT * FROM orders", timestamp="2026-02-25T10:00:05.000Z",
            ),
            _error_record(
                "SELECT * FROM products", timestamp="2026-02-25T10:00:10.000Z",
            ),
            _error_record(
                "relative path ./foo", timestamp="2026-02-25T10:00:08.000Z",
            ),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 4

        # "SELECT *" rule violated 3x should come before "absolute paths" (1x).
        select_violations = [v for v in violations if "SELECT" in v.rule.text]
        path_violations = [v for v in violations if "path" in v.rule.text.lower()]
        assert len(select_violations) == 3
        assert len(path_violations) == 1

        # First violation in sorted list should be from the most frequent rule.
        assert "SELECT" in violations[0].rule.text

    def test_matches_in_user_message(self) -> None:
        """Violation detected when keyword is in user_message, not error_text."""
        rules = [
            Rule(
                text="Never use SELECT * in queries",
                file_path="CLAUDE.md",
                line_number=1,
            ),
        ]
        errors = [
            _error_record(
                "Query failed",
                user_message="Run SELECT * FROM accounts",
            ),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 1

    def test_matches_in_context_fields(self) -> None:
        """Violation detected when keyword is in context_before/context_after."""
        rules = [
            Rule(
                text="Always use absolute paths",
                file_path="CLAUDE.md",
                line_number=1,
            ),
        ]
        errors = [
            _error_record(
                "File not found",
                context_before="I'll use the relative path ./src/main.py",
            ),
        ]

        violations = detect_violations(rules, errors)
        assert len(violations) == 1

    def test_returns_violation_namedtuple(self) -> None:
        """Returned items are Violation namedtuples with expected fields."""
        rules = [
            Rule(text="Never use SELECT *", file_path="CLAUDE.md", line_number=1),
        ]
        errors = [_error_record("Used SELECT * FROM foo")]

        violations = detect_violations(rules, errors)
        assert len(violations) == 1
        v = violations[0]
        assert isinstance(v, Violation)
        assert isinstance(v.rule, Rule)
        assert isinstance(v.error_record, dict)
        assert v.match_type in ("keyword", "semantic")
        assert isinstance(v.confidence, float)

    def test_same_error_matches_multiple_rules(self) -> None:
        """A single error can violate multiple rules simultaneously."""
        rules = [
            Rule(text="Never use SELECT *", file_path="CLAUDE.md", line_number=1),
            Rule(
                text="Always use explicit column lists",
                file_path="CLAUDE.md",
                line_number=2,
            ),
        ]
        errors = [
            _error_record(
                "Ran SELECT * — should use explicit column lists instead",
            ),
        ]

        violations = detect_violations(rules, errors)
        # Both rules can match the same error.
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# get_violation_report tests
# ---------------------------------------------------------------------------


class TestGetViolationReport:
    """Tests for get_violation_report() — full pipeline with DB."""

    def test_report_with_violations(
        self, in_memory_db: sqlite3.Connection, rules_file: Path,
    ) -> None:
        """Report includes violations when errors match rules."""
        # Insert error records that violate rules.
        in_memory_db.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "session-1", "2026-02-25T10:00:00Z", "specstory",
                "test.md", "Executed SELECT * FROM users",
                "tool_failure", "2026-02-25T12:00:00Z",
            ),
        )
        in_memory_db.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "session-2", "2026-02-25T11:00:00Z", "specstory",
                "test.md", "SELECT * FROM orders",
                "tool_failure", "2026-02-25T12:00:00Z",
            ),
        )
        in_memory_db.commit()

        report = get_violation_report(
            in_memory_db, [str(rules_file)],
        )

        assert report["total_rules"] > 0
        assert len(report["violations"]) >= 2
        assert report["compliant_rules"] >= 0
        assert report["date_range"]["start"] is not None
        assert report["date_range"]["end"] is not None

        # Violation summary should have the SELECT * rule.
        summary = report["violation_summary"]
        assert len(summary) > 0
        select_summary = [s for s in summary if "SELECT" in s["rule_text"]]
        assert len(select_summary) == 1
        assert select_summary[0]["count"] >= 2
        assert select_summary[0]["sessions"] >= 1

    def test_report_no_violations(
        self, in_memory_db: sqlite3.Connection, rules_file: Path,
    ) -> None:
        """Report with no matching errors shows all rules compliant."""
        # Insert an error that matches no rule.
        in_memory_db.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "session-1", "2026-02-25T10:00:00Z", "specstory",
                "test.md", "Connection timed out",
                "tool_failure", "2026-02-25T12:00:00Z",
            ),
        )
        in_memory_db.commit()

        report = get_violation_report(
            in_memory_db, [str(rules_file)],
        )

        assert report["violations"] == []
        assert report["compliant_rules"] == report["total_rules"]
        assert report["total_rules"] > 0
        assert report["violation_summary"] == []

    def test_report_empty_rules(
        self, in_memory_db: sqlite3.Connection, empty_rules_file: Path,
    ) -> None:
        """Report with no parsable rules returns empty result."""
        report = get_violation_report(
            in_memory_db, [str(empty_rules_file)],
        )

        assert report["total_rules"] == 0
        assert report["compliant_rules"] == 0
        assert report["violations"] == []

    def test_report_empty_db(
        self, in_memory_db: sqlite3.Connection, rules_file: Path,
    ) -> None:
        """Report with no error records returns empty violations."""
        report = get_violation_report(
            in_memory_db, [str(rules_file)],
        )

        assert report["violations"] == []
        assert report["compliant_rules"] == report["total_rules"]
        assert report["date_range"]["start"] is None

    def test_report_with_since_filter(
        self, in_memory_db: sqlite3.Connection, rules_file: Path,
    ) -> None:
        """Since filter restricts which error records are checked."""
        # Insert old and new errors.
        in_memory_db.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "session-old", "2026-01-01T10:00:00Z", "specstory",
                "test.md", "SELECT * FROM old_table",
                "tool_failure", "2026-01-01T12:00:00Z",
            ),
        )
        in_memory_db.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "session-new", "2026-02-25T10:00:00Z", "specstory",
                "test.md", "SELECT * FROM new_table",
                "tool_failure", "2026-02-25T12:00:00Z",
            ),
        )
        in_memory_db.commit()

        # Filter to only the new error.
        report = get_violation_report(
            in_memory_db,
            [str(rules_file)],
            since="2026-02-01T00:00:00Z",
        )

        # Only the recent error should be in violations.
        assert len(report["violations"]) >= 1
        timestamps = [v["timestamp"] for v in report["violations"]]
        assert all(ts >= "2026-02-01" for ts in timestamps)

    def test_report_multiple_rule_files(
        self, in_memory_db: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        """Report aggregates rules from multiple instruction files."""
        file1 = tmp_path / "rules1.md"
        file1.write_text("- Never use SELECT * in queries\n")

        file2 = tmp_path / "rules2.md"
        file2.write_text("- Always validate user input\n")

        in_memory_db.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "session-1", "2026-02-25T10:00:00Z", "specstory",
                "test.md", "SELECT * FROM users",
                "tool_failure", "2026-02-25T12:00:00Z",
            ),
        )
        in_memory_db.commit()

        report = get_violation_report(
            in_memory_db, [str(file1), str(file2)],
        )

        assert report["total_rules"] == 2
        # SELECT * violation should be found; validate input should be compliant.
        assert len(report["violation_summary"]) >= 1
        assert report["compliant_rules"] >= 1

    def test_report_violation_dict_structure(
        self, in_memory_db: sqlite3.Connection, tmp_path: Path,
    ) -> None:
        """Violation dicts in report have expected keys for JSON output."""
        rule_file = tmp_path / "rules.md"
        rule_file.write_text("- Never use SELECT * in queries\n")

        in_memory_db.execute(
            "INSERT INTO error_records "
            "(session_id, timestamp, source_type, source_file, "
            "error_text, error_type, mined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "session-1", "2026-02-25T10:00:00Z", "specstory",
                "test.md", "SELECT * FROM foo",
                "tool_failure", "2026-02-25T12:00:00Z",
            ),
        )
        in_memory_db.commit()

        report = get_violation_report(
            in_memory_db, [str(rule_file)],
        )

        assert len(report["violations"]) >= 1
        v = report["violations"][0]
        expected_keys = {
            "rule_text", "rule_file", "rule_line", "error_text",
            "error_type", "session_id", "timestamp", "match_type",
            "confidence",
        }
        assert expected_keys.issubset(v.keys())
