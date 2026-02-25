"""Unit tests for sio.mining.jsonl_parser.parse_jsonl.

Covers the public interface only — no implementation details.

Wire format expected by the parser (one JSON object per line):

    {"type":"human","message":{"role":"user","content":"..."},"timestamp":"..."}
    {"type":"assistant","message":{"role":"assistant","content":"..."},"timestamp":"..."}
    {"type":"tool_use","tool_name":"Read","tool_input":{...},"tool_output":"...","timestamp":"..."}
    {"type":"tool_use","tool_name":"Bash","tool_input":{...},"tool_output":null,"error":"...","timestamp":"..."}

Each dict returned by parse_jsonl has these keys:
    role        str
    content     str
    tool_name   str | None
    tool_input  str | None   (serialised JSON string of the raw tool_input object)
    tool_output str | None
    error       str | None
    timestamp   str | None
"""

from __future__ import annotations

import json
from pathlib import Path

from sio.mining.jsonl_parser import parse_jsonl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS_BASE = "2026-02-25T10:00:0{}Z"


def _write_lines(path: Path, lines: list[str]) -> None:
    """Write *lines* to *path*, one per physical line."""
    path.write_text("\n".join(lines), encoding="utf-8")


def _human(content: str, ts: str) -> dict:
    return {
        "type": "human",
        "message": {"role": "user", "content": content},
        "timestamp": ts,
    }


def _assistant(content: str, ts: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
        "timestamp": ts,
    }


def _tool_use(
    tool_name: str,
    tool_input: dict,
    tool_output: str | None,
    ts: str,
    error: str | None = None,
) -> dict:
    obj: dict = {
        "type": "tool_use",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "timestamp": ts,
    }
    if error is not None:
        obj["error"] = error
    return obj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseLineByLine:
    """parse_jsonl returns one dict per valid JSONL line."""

    def test_parse_line_by_line(self, tmp_path: Path) -> None:
        """Five distinct messages are returned, one per line."""
        wire_objects = [
            _human("Read foo.py", _TS_BASE.format(0)),
            _assistant("I will read that.", _TS_BASE.format(1)),
            _tool_use("Read", {"file_path": "/tmp/foo.py"}, "contents of foo", _TS_BASE.format(2)),
            _assistant("The file contains one function.", _TS_BASE.format(3)),
            _human("Thanks.", _TS_BASE.format(4)),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        assert len(result) == 5

    def test_all_returned_dicts_have_required_keys(self, tmp_path: Path) -> None:
        """Every returned dict exposes the full seven-key schema."""
        required_keys = {"role", "content", "tool_name", "tool_input", "tool_output", "error", "timestamp"}
        wire_objects = [
            _human("Hello", _TS_BASE.format(0)),
            _assistant("Hi", _TS_BASE.format(1)),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        for record in result:
            assert required_keys.issubset(record.keys()), (
                f"Missing keys in record: {required_keys - record.keys()}"
            )

    def test_role_values_are_strings(self, tmp_path: Path) -> None:
        """The ``role`` field is always a plain string."""
        wire_objects = [
            _human("Hello", _TS_BASE.format(0)),
            _assistant("Hi", _TS_BASE.format(1)),
            _tool_use("Bash", {"command": "ls"}, "file.py", _TS_BASE.format(2)),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        for record in result:
            assert isinstance(record["role"], str)

    def test_content_values_are_strings(self, tmp_path: Path) -> None:
        """The ``content`` field is always a string (may be empty for tool lines)."""
        wire_objects = [
            _human("Do something", _TS_BASE.format(0)),
            _tool_use("Read", {"file_path": "/tmp/x.py"}, "output", _TS_BASE.format(1)),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        for record in result:
            assert isinstance(record["content"], str)


class TestExtractToolMetadata:
    """Tool metadata (tool_name, tool_input, tool_output) is extracted correctly."""

    def test_tool_name_extracted(self, tmp_path: Path) -> None:
        """tool_name is populated from the ``tool_name`` key on tool_use lines."""
        path = tmp_path / "session.jsonl"
        _write_lines(
            path,
            [json.dumps(_tool_use("Read", {"file_path": "/tmp/foo.py"}, "file body", _TS_BASE.format(0)))],
        )

        result = parse_jsonl(path)

        assert result[0]["tool_name"] == "Read"

    def test_tool_input_extracted(self, tmp_path: Path) -> None:
        """tool_input is present and contains the serialised input parameters."""
        path = tmp_path / "session.jsonl"
        raw_input = {"file_path": "/tmp/bar.py"}
        _write_lines(
            path,
            [json.dumps(_tool_use("Read", raw_input, "body", _TS_BASE.format(0)))],
        )

        result = parse_jsonl(path)

        # tool_input may be a JSON string or a dict — either way it must encode
        # the original parameters.
        tool_input = result[0]["tool_input"]
        assert tool_input is not None
        parsed = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
        assert parsed["file_path"] == "/tmp/bar.py"

    def test_tool_output_extracted(self, tmp_path: Path) -> None:
        """tool_output is the raw output string returned by the tool."""
        path = tmp_path / "session.jsonl"
        _write_lines(
            path,
            [json.dumps(_tool_use("Bash", {"command": "ls"}, "main.py\nutils.py", _TS_BASE.format(0)))],
        )

        result = parse_jsonl(path)

        assert result[0]["tool_output"] == "main.py\nutils.py"

    def test_non_tool_lines_have_none_tool_fields(self, tmp_path: Path) -> None:
        """Human and assistant messages have None for all three tool fields."""
        wire_objects = [
            _human("Hello", _TS_BASE.format(0)),
            _assistant("Hi", _TS_BASE.format(1)),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        for record in result:
            assert record["tool_name"] is None
            assert record["tool_input"] is None
            assert record["tool_output"] is None

    def test_multiple_tool_names_preserved(self, tmp_path: Path) -> None:
        """Different tool_name values on successive lines are all preserved."""
        wire_objects = [
            _tool_use("Read", {"file_path": "/a.py"}, "a", _TS_BASE.format(0)),
            _tool_use("Bash", {"command": "pwd"}, "/home", _TS_BASE.format(1)),
            _tool_use("Glob", {"pattern": "**/*.py"}, "a.py", _TS_BASE.format(2)),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        assert [r["tool_name"] for r in result] == ["Read", "Bash", "Glob"]


class TestExtractErrors:
    """The ``error`` field is populated when a tool_use line carries an error."""

    def test_error_field_extracted(self, tmp_path: Path) -> None:
        """error is the exact string from the ``error`` key."""
        path = tmp_path / "session.jsonl"
        _write_lines(
            path,
            [
                json.dumps(
                    _tool_use(
                        "Bash",
                        {"command": "false"},
                        None,
                        _TS_BASE.format(0),
                        error="Command failed with exit code 1",
                    )
                )
            ],
        )

        result = parse_jsonl(path)

        assert result[0]["error"] == "Command failed with exit code 1"

    def test_error_is_none_when_absent(self, tmp_path: Path) -> None:
        """error is None when the ``error`` key is not present on the line."""
        path = tmp_path / "session.jsonl"
        _write_lines(
            path,
            [json.dumps(_tool_use("Read", {"file_path": "/ok.py"}, "content", _TS_BASE.format(0)))],
        )

        result = parse_jsonl(path)

        assert result[0]["error"] is None

    def test_error_on_human_message_is_none(self, tmp_path: Path) -> None:
        """Human messages never carry an error value."""
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(_human("Do something", _TS_BASE.format(0)))])

        result = parse_jsonl(path)

        assert result[0]["error"] is None

    def test_mixed_success_and_error_tool_uses(self, tmp_path: Path) -> None:
        """Only the failing tool_use has a non-None error; successes have None."""
        wire_objects = [
            _tool_use("Read", {"file_path": "/ok.py"}, "content", _TS_BASE.format(0)),
            _tool_use(
                "Bash",
                {"command": "bad"},
                None,
                _TS_BASE.format(1),
                error="exit 127",
            ),
            _tool_use("Glob", {"pattern": "*.py"}, "ok.py", _TS_BASE.format(2)),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        assert result[0]["error"] is None
        assert result[1]["error"] == "exit 127"
        assert result[2]["error"] is None


class TestHandleMissingFields:
    """Missing optional fields do not raise; they produce None in the output."""

    def test_missing_timestamp_produces_none(self, tmp_path: Path) -> None:
        """A line without ``timestamp`` returns timestamp=None, not a KeyError."""
        obj = {"type": "human", "message": {"role": "user", "content": "Hi"}}
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj)])

        result = parse_jsonl(path)

        assert len(result) == 1
        assert result[0]["timestamp"] is None

    def test_missing_tool_output_produces_none(self, tmp_path: Path) -> None:
        """tool_output absent from a tool_use line is mapped to None."""
        obj = {
            "type": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "/x.py"},
            "timestamp": _TS_BASE.format(0),
            # tool_output intentionally absent
        }
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj)])

        result = parse_jsonl(path)

        assert len(result) == 1
        assert result[0]["tool_output"] is None

    def test_missing_error_field_produces_none(self, tmp_path: Path) -> None:
        """error absent from a tool_use line is mapped to None."""
        obj = {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_output": "file.py",
            "timestamp": _TS_BASE.format(0),
            # error intentionally absent
        }
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj)])

        result = parse_jsonl(path)

        assert len(result) == 1
        assert result[0]["error"] is None

    def test_missing_content_in_message(self, tmp_path: Path) -> None:
        """A message dict without ``content`` is parsed without raising."""
        obj = {
            "type": "assistant",
            "message": {"role": "assistant"},  # content omitted
            "timestamp": _TS_BASE.format(0),
        }
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj)])

        result = parse_jsonl(path)

        assert len(result) == 1
        # content should be a string (empty string or None — either is acceptable)
        assert result[0]["content"] is None or isinstance(result[0]["content"], str)

    def test_null_tool_output_preserved_as_none(self, tmp_path: Path) -> None:
        """An explicit JSON null for tool_output maps to Python None, not the string 'null'."""
        obj = {
            "type": "tool_use",
            "tool_name": "Bash",
            "tool_input": {"command": "exit 1"},
            "tool_output": None,
            "timestamp": _TS_BASE.format(0),
        }
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj)])

        result = parse_jsonl(path)

        assert result[0]["tool_output"] is None


class TestHandleCorruptLines:
    """Corrupt or non-JSON lines are skipped; valid lines are still returned."""

    def test_corrupt_json_line_is_skipped(self, tmp_path: Path) -> None:
        """A line with broken JSON is silently skipped."""
        lines = [
            json.dumps(_human("Hello", _TS_BASE.format(0))),
            "{this is not valid json",
            json.dumps(_assistant("Hi", _TS_BASE.format(1))),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, lines)

        result = parse_jsonl(path)

        assert len(result) == 2

    def test_empty_lines_are_skipped(self, tmp_path: Path) -> None:
        """Blank lines between valid records are silently skipped."""
        lines = [
            json.dumps(_human("Hello", _TS_BASE.format(0))),
            "",
            "   ",
            json.dumps(_assistant("Hi", _TS_BASE.format(1))),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, lines)

        result = parse_jsonl(path)

        assert len(result) == 2

    def test_random_text_lines_are_skipped(self, tmp_path: Path) -> None:
        """Lines that are plain text (not JSON objects) are silently skipped."""
        lines = [
            json.dumps(_human("First message", _TS_BASE.format(0))),
            "not json at all",
            "another garbage line !!!",
            json.dumps(_assistant("Response", _TS_BASE.format(1))),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, lines)

        result = parse_jsonl(path)

        assert len(result) == 2

    def test_mix_of_corrupt_and_valid_returns_only_valid(self, tmp_path: Path) -> None:
        """In a heavily mixed file, only the N valid lines come back."""
        valid_1 = json.dumps(_human("Q1", _TS_BASE.format(0)))
        valid_2 = json.dumps(_tool_use("Read", {"file_path": "/f.py"}, "x", _TS_BASE.format(1)))
        valid_3 = json.dumps(_assistant("Done.", _TS_BASE.format(2)))

        lines = [
            valid_1,
            "{bad",
            "",
            "garbage",
            valid_2,
            '{"incomplete":',
            valid_3,
            "trailing junk",
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, lines)

        result = parse_jsonl(path)

        assert len(result) == 3

    def test_parse_does_not_raise_on_any_corrupt_content(self, tmp_path: Path) -> None:
        """parse_jsonl never raises regardless of how corrupt the file is."""
        lines = ["{{{", "null", "[]", "123", "", "   \t  "]
        path = tmp_path / "session.jsonl"
        _write_lines(path, lines)

        # Must not raise.
        result = parse_jsonl(path)

        assert isinstance(result, list)


class TestEmptyFile:
    """An empty JSONL file returns an empty list."""

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Zero-byte file produces an empty list, not an exception."""
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")

        result = parse_jsonl(path)

        assert result == []

    def test_whitespace_only_file_returns_empty_list(self, tmp_path: Path) -> None:
        """A file containing only whitespace/newlines produces an empty list."""
        path = tmp_path / "ws.jsonl"
        path.write_text("\n\n\n   \n", encoding="utf-8")

        result = parse_jsonl(path)

        assert result == []

    def test_return_type_is_list(self, tmp_path: Path) -> None:
        """Return type is always list[dict], including for empty files."""
        path = tmp_path / "empty2.jsonl"
        path.write_text("", encoding="utf-8")

        result = parse_jsonl(path)

        assert isinstance(result, list)


class TestTimestampsPreserved:
    """Timestamps from each wire-format line are passed through unchanged."""

    def test_timestamps_match_source(self, tmp_path: Path) -> None:
        """Each record's timestamp equals the timestamp in the originating wire object."""
        timestamps = [
            "2026-02-25T10:00:00Z",
            "2026-02-25T10:00:01Z",
            "2026-02-25T10:00:02Z",
        ]
        wire_objects = [
            _human("Message one", timestamps[0]),
            _assistant("Message two", timestamps[1]),
            _tool_use("Read", {"file_path": "/x.py"}, "body", timestamps[2]),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        assert [r["timestamp"] for r in result] == timestamps

    def test_timestamps_are_strings(self, tmp_path: Path) -> None:
        """Timestamps are returned as strings, not datetime objects."""
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(_human("Hi", "2026-02-25T10:00:00Z"))])

        result = parse_jsonl(path)

        assert isinstance(result[0]["timestamp"], str)

    def test_timestamp_order_matches_line_order(self, tmp_path: Path) -> None:
        """Timestamps appear in the same order as the source lines."""
        ts_values = [
            "2026-02-25T09:00:00Z",
            "2026-02-25T09:00:05Z",
            "2026-02-25T09:00:10Z",
            "2026-02-25T09:00:15Z",
            "2026-02-25T09:00:20Z",
        ]
        wire_objects = [
            _human("a", ts_values[0]),
            _assistant("b", ts_values[1]),
            _tool_use("Bash", {"command": "ls"}, "out", ts_values[2]),
            _human("c", ts_values[3]),
            _assistant("d", ts_values[4]),
        ]
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj) for obj in wire_objects])

        result = parse_jsonl(path)

        assert [r["timestamp"] for r in result] == ts_values

    def test_missing_timestamp_does_not_skip_record(self, tmp_path: Path) -> None:
        """A line missing the timestamp key is still included; timestamp is None."""
        obj = {"type": "human", "message": {"role": "user", "content": "no ts"}}
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(obj)])

        result = parse_jsonl(path)

        assert len(result) == 1
        assert result[0]["timestamp"] is None

    def test_high_precision_timestamp_preserved_verbatim(self, tmp_path: Path) -> None:
        """Sub-second precision in a timestamp is preserved exactly as written."""
        ts = "2026-02-25T10:00:00.123456Z"
        path = tmp_path / "session.jsonl"
        _write_lines(path, [json.dumps(_human("precise", ts))])

        result = parse_jsonl(path)

        assert result[0]["timestamp"] == ts


class TestSampleJsonlFixtureCompatibility:
    """
    The sample_jsonl_file conftest fixture writes already-normalised dicts
    (role/content/tool_name/...).  Confirm parse_jsonl can handle that format
    too (graceful degradation path — the fixture format is still valid JSON so
    lines are either parsed or skipped without crashing).
    """

    def test_fixture_file_does_not_crash_parser(self, sample_jsonl_file) -> None:
        """parse_jsonl runs to completion on a fixture-generated file."""
        path = sample_jsonl_file()

        result = parse_jsonl(path)

        assert isinstance(result, list)
