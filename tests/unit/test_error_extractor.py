"""Unit tests for sio.mining.error_extractor — T050 [US4].

Tests the extract_errors() function which ingests parsed message dicts from
either the SpecStory or JSONL parser and classifies them into four error
categories:

    tool_failure      — a tool call with a non-null error field or failed output
    user_correction   — user message containing correction phrasing
    repeated_attempt  — same tool called multiple times in succession (retries)
    undo              — user message with git checkout / git revert / "undo that"

The function signature under test:

    extract_errors(
        parsed_messages: list[dict],
        source_file: str,
        source_type: str,
    ) -> list[dict]

Each returned dict must conform to the ErrorRecord schema:
    session_id, timestamp, source_type, source_file,
    tool_name, error_text, user_message, context_before,
    context_after, error_type, mined_at
"""

from __future__ import annotations

import pytest

from sio.mining.error_extractor import extract_errors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOURCE_FILE = "2026-02-25_10-00-00Z-test-session.md"
_SOURCE_TYPE = "specstory"
_SESSION_ID = "test-session-001"
_TS_BASE = "2026-02-25T10:00:{:02d}.000Z"


def _ts(offset: int = 0) -> str:
    """Return a deterministic ISO-8601 timestamp at second *offset*."""
    return _TS_BASE.format(offset % 60)


def _human(content: str, offset: int = 0) -> dict:
    """Build a human-role message dict."""
    return {
        "role": "human",
        "content": content,
        "tool_name": None,
        "tool_input": None,
        "tool_output": None,
        "error": None,
        "session_id": _SESSION_ID,
        "timestamp": _ts(offset),
    }


def _assistant(
    content: str,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_output: str | None = None,
    error: str | None = None,
    offset: int = 0,
) -> dict:
    """Build an assistant-role message dict."""
    return {
        "role": "assistant",
        "content": content,
        "tool_name": tool_name,
        "tool_input": tool_input or {},
        "tool_output": tool_output,
        "error": error,
        "session_id": _SESSION_ID,
        "timestamp": _ts(offset),
    }


# ---------------------------------------------------------------------------
# Mandatory ErrorRecord schema keys
# ---------------------------------------------------------------------------

_SCHEMA_KEYS = frozenset(
    {
        "session_id",
        "timestamp",
        "source_type",
        "source_file",
        "tool_name",
        "error_text",
        "user_message",
        "context_before",
        "context_after",
        "error_type",
        "mined_at",
    }
)


def _assert_schema(record: dict) -> None:
    """Assert that *record* carries every required ErrorRecord key."""
    missing = _SCHEMA_KEYS - record.keys()
    assert not missing, f"ErrorRecord missing keys: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 1. tool_failure
# ---------------------------------------------------------------------------


class TestIdentifyToolFailures:
    """Messages with a non-null error field or explicit failure text must produce
    error_type='tool_failure' records."""

    def test_single_tool_error_field(self):
        """An assistant message with a populated error field is a tool_failure."""
        messages = [
            _human("Run the migration.", offset=0),
            _assistant(
                content="Running migration.",
                tool_name="Bash",
                tool_input={"command": "python migrate.py"},
                tool_output=None,
                error="CalledProcessError: exit status 1",
                offset=1,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        rec = results[0]
        _assert_schema(rec)
        assert rec["error_type"] == "tool_failure"
        assert rec["tool_name"] == "Bash"
        assert "CalledProcessError" in rec["error_text"]

    def test_error_field_sets_source_metadata(self):
        """source_file and source_type are propagated from call-site arguments."""
        messages = [
            _assistant(
                content="Reading file.",
                tool_name="Read",
                error="FileNotFoundError: /tmp/missing.py",
                offset=0,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        assert results[0]["source_file"] == _SOURCE_FILE
        assert results[0]["source_type"] == _SOURCE_TYPE

    def test_multiple_tool_failures_extracted(self):
        """Every message with an error field produces an independent record."""
        messages = [
            _assistant(
                "First failure.",
                tool_name="Bash",
                error="TimeoutError: exceeded 30s",
                offset=0,
            ),
            _assistant(
                "Second failure.",
                tool_name="Read",
                error="PermissionError: /etc/secret",
                offset=1,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 2
        types = {r["error_type"] for r in results}
        assert types == {"tool_failure"}

    def test_mined_at_is_populated(self):
        """mined_at must be a non-empty string (extraction timestamp)."""
        messages = [
            _assistant(
                "Failure.",
                tool_name="Glob",
                error="SomeError: something went wrong",
                offset=0,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert results[0]["mined_at"] != ""
        assert results[0]["mined_at"] is not None

    def test_tool_name_none_still_captured(self):
        """A message with an error but no tool_name is still a tool_failure."""
        messages = [
            _assistant(
                "Internal error.",
                tool_name=None,
                error="RuntimeError: unexpected state",
                offset=0,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        assert results[0]["error_type"] == "tool_failure"


# ---------------------------------------------------------------------------
# 2. user_correction
# ---------------------------------------------------------------------------


class TestIdentifyUserCorrections:
    """Human messages with correction phrasing trigger error_type='user_correction'."""

    @pytest.mark.parametrize(
        "content",
        [
            "No, actually I wanted the other file.",
            "That's wrong, please use the dev branch.",
            "Not what I wanted — try again.",
            "No, use src/ not lib/.",
            "Actually, I meant to run pytest not ruff.",
            "That's not right, revert that change.",
        ],
    )
    def test_correction_phrases_detected(self, content: str):
        messages = [
            _human("Do something.", offset=0),
            _assistant("Doing it.", tool_name="Bash", offset=1),
            _human(content, offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        correction_records = [r for r in results if r["error_type"] == "user_correction"]
        assert len(correction_records) >= 1, (
            f"Expected user_correction for message: {content!r}"
        )

    def test_correction_user_message_field_set(self):
        """The user_message field must contain the correcting message text."""
        correction = "No, actually that's wrong."
        messages = [
            _human("Do the thing.", offset=0),
            _assistant("Done.", tool_name="Read", offset=1),
            _human(correction, offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        correction_records = [r for r in results if r["error_type"] == "user_correction"]
        assert len(correction_records) == 1
        assert correction_records[0]["user_message"] == correction

    def test_correction_schema_complete(self):
        """A user_correction record must satisfy the full ErrorRecord schema."""
        messages = [
            _human("That's wrong.", offset=0),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        for rec in results:
            if rec["error_type"] == "user_correction":
                _assert_schema(rec)

    def test_partial_word_no_false_positive(self):
        """'Notice the warning' must not trigger correction detection."""
        messages = [
            _human("Notice the test output above.", offset=0),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        correction_records = [r for r in results if r["error_type"] == "user_correction"]
        assert len(correction_records) == 0


# ---------------------------------------------------------------------------
# 3. repeated_attempt
# ---------------------------------------------------------------------------


class TestIdentifyRepeatedAttempts:
    """Same tool invoked multiple times in succession signals error_type='repeated_attempt'."""

    def test_three_identical_tool_calls_flagged(self):
        """Three consecutive Bash calls with similar input must produce a repeated_attempt."""
        messages = [
            _human("Run the tests.", offset=0),
            _assistant("Running.", tool_name="Bash", tool_input={"command": "pytest tests/ -v"}, offset=1),
            _assistant("Retrying.", tool_name="Bash", tool_input={"command": "pytest tests/ -v"}, offset=2),
            _assistant("Retrying again.", tool_name="Bash", tool_input={"command": "pytest tests/ -v"}, offset=3),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        repeated = [r for r in results if r["error_type"] == "repeated_attempt"]
        assert len(repeated) >= 1

    def test_repeated_attempt_captures_tool_name(self):
        """The tool_name field on repeated_attempt records matches the repeated tool."""
        messages = [
            _assistant("Read attempt 1.", tool_name="Read", tool_input={"file_path": "/tmp/foo.py"}, offset=0),
            _assistant("Read attempt 2.", tool_name="Read", tool_input={"file_path": "/tmp/foo.py"}, offset=1),
            _assistant("Read attempt 3.", tool_name="Read", tool_input={"file_path": "/tmp/foo.py"}, offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        repeated = [r for r in results if r["error_type"] == "repeated_attempt"]
        assert len(repeated) >= 1
        for rec in repeated:
            assert rec["tool_name"] == "Read"

    def test_two_calls_not_flagged_as_repeated(self):
        """Two consecutive calls do not constitute a repeated_attempt (threshold is 3+)."""
        messages = [
            _assistant("Call 1.", tool_name="Bash", tool_input={"command": "ls"}, offset=0),
            _assistant("Call 2.", tool_name="Bash", tool_input={"command": "ls"}, offset=1),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        repeated = [r for r in results if r["error_type"] == "repeated_attempt"]
        assert len(repeated) == 0

    def test_different_tools_not_flagged(self):
        """Alternating different tools must not produce repeated_attempt."""
        messages = [
            _assistant("Read.", tool_name="Read", tool_input={"file_path": "/tmp/a.py"}, offset=0),
            _assistant("Bash.", tool_name="Bash", tool_input={"command": "ls"}, offset=1),
            _assistant("Read again.", tool_name="Read", tool_input={"file_path": "/tmp/a.py"}, offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        repeated = [r for r in results if r["error_type"] == "repeated_attempt"]
        assert len(repeated) == 0

    def test_repeated_attempt_schema_complete(self):
        """repeated_attempt records must satisfy the full ErrorRecord schema."""
        messages = [
            _assistant("Attempt 1.", tool_name="Glob", tool_input={"pattern": "**/*.py"}, offset=0),
            _assistant("Attempt 2.", tool_name="Glob", tool_input={"pattern": "**/*.py"}, offset=1),
            _assistant("Attempt 3.", tool_name="Glob", tool_input={"pattern": "**/*.py"}, offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        for rec in results:
            if rec["error_type"] == "repeated_attempt":
                _assert_schema(rec)


# ---------------------------------------------------------------------------
# 4. undo
# ---------------------------------------------------------------------------


class TestIdentifyUndos:
    """Human messages requesting an undo action produce error_type='undo'."""

    @pytest.mark.parametrize(
        "content",
        [
            "git checkout -- .",
            "git revert HEAD",
            "undo that last change",
            "revert that, I don't want it",
            "Please undo that.",
            "Undo that edit you just made.",
        ],
    )
    def test_undo_phrases_detected(self, content: str):
        messages = [
            _human("Make a change.", offset=0),
            _assistant("Changed it.", tool_name="Edit", offset=1),
            _human(content, offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        undo_records = [r for r in results if r["error_type"] == "undo"]
        assert len(undo_records) >= 1, f"Expected undo for message: {content!r}"

    def test_undo_user_message_preserved(self):
        """The user_message field must hold the full undo message text."""
        undo_msg = "git revert HEAD~1"
        messages = [
            _human(undo_msg, offset=0),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        undo_records = [r for r in results if r["error_type"] == "undo"]
        assert len(undo_records) == 1
        assert undo_records[0]["user_message"] == undo_msg

    def test_undo_schema_complete(self):
        """undo records must satisfy the full ErrorRecord schema."""
        messages = [
            _human("undo that", offset=0),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        for rec in results:
            if rec["error_type"] == "undo":
                _assert_schema(rec)

    def test_git_push_not_undo(self):
        """'git push origin main' must not be classified as undo."""
        messages = [
            _human("git push origin main", offset=0),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        undo_records = [r for r in results if r["error_type"] == "undo"]
        assert len(undo_records) == 0


# ---------------------------------------------------------------------------
# 5. No false positives
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    """A clean successful conversation must produce zero error records."""

    def test_clean_conversation_returns_empty(self):
        """Normal back-and-forth with no errors, corrections, retries, or undos."""
        messages = [
            _human("Read the file src/main.py.", offset=0),
            _assistant(
                "Reading the file.",
                tool_name="Read",
                tool_input={"file_path": "/home/user/src/main.py"},
                tool_output='def main():\n    print("hello")\n',
                offset=1,
            ),
            _assistant("The file defines a main() function that prints 'hello'.", offset=2),
            _human("Run the tests.", offset=3),
            _assistant(
                "Running pytest.",
                tool_name="Bash",
                tool_input={"command": "pytest tests/ -v"},
                tool_output="2 passed in 0.45s",
                offset=4,
            ),
            _human("Great, looks good.", offset=5),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert results == [], f"Expected no errors, got: {results}"

    def test_normal_human_message_not_correction(self):
        """Ordinary human messages must not be mistaken for corrections."""
        messages = [
            _human("Please read the README.", offset=0),
            _human("Now summarise it.", offset=1),
            _human("Thanks.", offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert results == []

    def test_successful_tool_output_not_failure(self):
        """A tool call with a non-empty output and no error must not be flagged."""
        messages = [
            _assistant(
                "Done.",
                tool_name="Edit",
                tool_output="File updated successfully.",
                error=None,
                offset=0,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert results == []


# ---------------------------------------------------------------------------
# 6. Context captured
# ---------------------------------------------------------------------------


class TestContextCaptured:
    """context_before and context_after must be populated from surrounding messages."""

    def test_context_before_from_preceding_message(self):
        """context_before must reference text from the message before the error."""
        prior_content = "Please run the database migration script."
        messages = [
            _human(prior_content, offset=0),
            _assistant(
                "Running migration.",
                tool_name="Bash",
                tool_input={"command": "python migrate.py"},
                error="IntegrityError: UNIQUE constraint failed",
                offset=1,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        assert results[0]["context_before"] is not None
        assert results[0]["context_before"] != ""

    def test_context_after_from_following_message(self):
        """context_after must reference text from the message after the error."""
        following_content = "Let me try a different approach."
        messages = [
            _assistant(
                "Failure.",
                tool_name="Read",
                error="FileNotFoundError: /tmp/gone.py",
                offset=0,
            ),
            _human(following_content, offset=1),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        assert results[0]["context_after"] is not None
        assert results[0]["context_after"] != ""

    def test_context_before_none_when_first_message(self):
        """When the error is the very first message, context_before may be None or empty."""
        messages = [
            _assistant(
                "First message and it errored.",
                tool_name="Bash",
                error="SomeError",
                offset=0,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        # Either None or empty string is acceptable for a missing predecessor.
        assert results[0]["context_before"] in (None, "")

    def test_context_after_none_when_last_message(self):
        """When the error is the last message, context_after may be None or empty."""
        messages = [
            _human("Do something.", offset=0),
            _assistant(
                "Last message and it errored.",
                tool_name="Bash",
                error="FinalError",
                offset=1,
            ),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        # Either None or empty string is acceptable when no successor exists.
        assert results[0]["context_after"] in (None, "")

    def test_context_before_and_after_both_present_in_middle(self):
        """A record in the middle of the conversation must have both context fields."""
        messages = [
            _human("Run tests.", offset=0),
            _assistant(
                "Retrying.",
                tool_name="Bash",
                error="ConnectionError: timed out",
                offset=1,
            ),
            _human("Try with a different flag.", offset=2),
        ]

        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert len(results) == 1
        assert results[0]["context_before"] not in (None, "")
        assert results[0]["context_after"] not in (None, "")


# ---------------------------------------------------------------------------
# 7. Mixed error types
# ---------------------------------------------------------------------------


class TestMixedErrorTypes:
    """A realistic conversation containing multiple error categories must classify
    each event correctly and independently."""

    def _build_mixed_conversation(self) -> list[dict]:
        return [
            _human("Run the tests.", offset=0),
            # tool_failure: Bash tool errors out
            _assistant(
                "Running tests.",
                tool_name="Bash",
                tool_input={"command": "pytest tests/"},
                error="ImportError: cannot import name 'foo'",
                offset=1,
            ),
            # user_correction: user objects to the approach
            _human("No, actually run ruff first.", offset=2),
            # Two more identical Bash calls — sets up for repeated_attempt below
            _assistant(
                "Running ruff.",
                tool_name="Bash",
                tool_input={"command": "ruff check ."},
                tool_output="All checks passed.",
                offset=3,
            ),
            # repeated_attempt: same Read called three times
            _assistant(
                "Reading config (1).",
                tool_name="Read",
                tool_input={"file_path": "/project/config.toml"},
                offset=4,
            ),
            _assistant(
                "Reading config (2).",
                tool_name="Read",
                tool_input={"file_path": "/project/config.toml"},
                offset=5,
            ),
            _assistant(
                "Reading config (3).",
                tool_name="Read",
                tool_input={"file_path": "/project/config.toml"},
                offset=6,
            ),
            # undo: user asks to revert changes
            _human("git revert HEAD", offset=7),
        ]

    def test_all_error_types_present(self):
        """At least one record of each expected error_type must be returned."""
        messages = self._build_mixed_conversation()
        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        found_types = {r["error_type"] for r in results}
        expected = {"tool_failure", "user_correction", "repeated_attempt", "undo"}
        assert expected == found_types, (
            f"Missing error types: {expected - found_types}; "
            f"extra error types: {found_types - expected}"
        )

    def test_all_records_satisfy_schema(self):
        """Every returned record in the mixed conversation must be schema-valid."""
        messages = self._build_mixed_conversation()
        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        assert results, "Expected at least one record from the mixed conversation."
        for rec in results:
            _assert_schema(rec)

    def test_source_metadata_consistent_across_records(self):
        """All records from the same call must carry the same source_file and source_type."""
        messages = self._build_mixed_conversation()
        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        for rec in results:
            assert rec["source_file"] == _SOURCE_FILE
            assert rec["source_type"] == _SOURCE_TYPE

    def test_records_count_at_least_one_per_type(self):
        """There must be at least one record per detected error category."""
        messages = self._build_mixed_conversation()
        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)

        from collections import Counter

        counts = Counter(r["error_type"] for r in results)
        assert counts["tool_failure"] >= 1
        assert counts["user_correction"] >= 1
        assert counts["repeated_attempt"] >= 1
        assert counts["undo"] >= 1


# ---------------------------------------------------------------------------
# 8. Empty input
# ---------------------------------------------------------------------------


class TestEmptyInput:
    """Edge cases around empty or minimal input."""

    def test_empty_list_returns_empty_list(self):
        """An empty message list must produce an empty result list."""
        results = extract_errors([], _SOURCE_FILE, _SOURCE_TYPE)
        assert results == []

    def test_return_type_is_list(self):
        """The return value must always be a list, never None."""
        results = extract_errors([], _SOURCE_FILE, _SOURCE_TYPE)
        assert isinstance(results, list)

    def test_single_clean_message_returns_empty(self):
        """A single normal human message must yield no records."""
        messages = [_human("Hello.", offset=0)]
        results = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)
        assert results == []


# ---------------------------------------------------------------------------
# 9. Schema compliance via sample_error_records fixture
# ---------------------------------------------------------------------------


class TestSchemaViaFixture:
    """Use the shared sample_error_records fixture to verify that real
    extract_errors output is compatible with the v2 error_records table."""

    def test_extracted_records_insertable_into_v2_db(
        self, v2_db, sample_error_records
    ):
        """Records returned by extract_errors must be insertable into v2_db.

        This cross-validates the dict shape returned by extract_errors against
        the live SQLite schema managed by the v2_db fixture.
        """
        # Build a minimal message set that produces one tool_failure.
        messages = [
            _assistant(
                "Failed to read.",
                tool_name="Read",
                error="FileNotFoundError: /tmp/gone.py",
                offset=0,
            ),
        ]

        records = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)
        assert records, "Expected at least one record to insert."

        for rec in records:
            _assert_schema(rec)
            v2_db.execute(
                """
                INSERT INTO error_records
                    (session_id, timestamp, source_type, source_file,
                     tool_name, error_text, user_message,
                     context_before, context_after, error_type, mined_at)
                VALUES
                    (:session_id, :timestamp, :source_type, :source_file,
                     :tool_name, :error_text, :user_message,
                     :context_before, :context_after, :error_type, :mined_at)
                """,
                rec,
            )
        v2_db.commit()

        row_count = v2_db.execute(
            "SELECT COUNT(*) FROM error_records WHERE source_file = ?",
            (_SOURCE_FILE,),
        ).fetchone()[0]

        assert row_count == len(records)

    def test_fixture_and_extracted_records_share_schema(
        self, sample_error_records
    ):
        """Keys from the fixture must match keys produced by extract_errors.

        This guards against silent schema drift between the fixture and the
        implementation.
        """
        fixture_records = sample_error_records(count=1)
        fixture_keys = frozenset(fixture_records[0].keys()) - {"id"}  # id is DB-assigned

        messages = [
            _assistant(
                "Error occurred.",
                tool_name="Bash",
                error="RuntimeError: something exploded",
                offset=0,
            ),
        ]

        extracted = extract_errors(messages, _SOURCE_FILE, _SOURCE_TYPE)
        assert extracted, "Expected at least one extracted record."

        extracted_keys = frozenset(extracted[0].keys())

        # Every schema key must appear in both sources.
        assert _SCHEMA_KEYS.issubset(fixture_keys), (
            f"Fixture missing schema keys: {_SCHEMA_KEYS - fixture_keys}"
        )
        assert _SCHEMA_KEYS.issubset(extracted_keys), (
            f"Extractor missing schema keys: {_SCHEMA_KEYS - extracted_keys}"
        )
