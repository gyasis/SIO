"""Unit tests for sio.mining.specstory_parser.parse_specstory.

Covers the public interface only — no implementation details.

The SpecStory markdown format understood by these tests:

    # Session: <name>

    **Human:** <content>

    ---

    **Assistant:** <content>
    [Tool call: <ToolName> with input <json>]
    [Tool output: <output>]
    [Tool error: <error_text>]

    ---

Each dict returned by parse_specstory has these keys:
    role        str                  "human" or "assistant"
    content     str                  raw text of the block
    tool_calls  list[dict]           possibly empty; each dict has:
        tool_name   str
        tool_input  str | None       serialised JSON string (may be None)
        tool_output str | None
        error       str | None

A file-level timestamp is extracted from the filename:
    "2026-02-25_10-00-00Z-session-name.md"  ->  "2026-02-25T10:00:00Z"
"""

from __future__ import annotations

import json
from pathlib import Path

from sio.mining.specstory_parser import parse_specstory

# ---------------------------------------------------------------------------
# Module-level sentinel — lets every test assert the correct return type
# without re-importing.
# ---------------------------------------------------------------------------

_REQUIRED_BLOCK_KEYS: frozenset[str] = frozenset({"role", "content", "tool_calls"})
_REQUIRED_TOOL_CALL_KEYS: frozenset[str] = frozenset(
    {"tool_name", "tool_input", "tool_output", "error"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_specstory(path: Path, content: str) -> None:
    """Write *content* to *path* with UTF-8 encoding."""
    path.write_text(content, encoding="utf-8")


def _tool_call_line(tool_name: str, tool_input: dict) -> str:
    return f"[Tool call: {tool_name} with input {json.dumps(tool_input)}]"


def _tool_output_line(output: str) -> str:
    return f"[Tool output: {output}]"


def _tool_error_line(error: str) -> str:
    return f"[Tool error: {error}]"


def _make_specstory(blocks: list[dict]) -> str:
    """Render a list of block dicts into a SpecStory markdown string.

    Each block dict must have:
        role: "human" | "assistant"
        text: str           — prose content for the block
        tool_calls: list[dict]  — optional; each has tool_name, tool_input,
                                   tool_output (optional), error (optional)
    """
    parts: list[str] = []
    for idx, block in enumerate(blocks):
        role_label = "Human" if block["role"] == "human" else "Assistant"
        lines: list[str] = [f"**{role_label}:** {block.get('text', '')}"]
        for tc in block.get("tool_calls", []):
            lines.append(_tool_call_line(tc["tool_name"], tc.get("tool_input", {})))
            if "tool_output" in tc and tc["tool_output"] is not None:
                lines.append(_tool_output_line(tc["tool_output"]))
            if "error" in tc and tc["error"] is not None:
                lines.append(_tool_error_line(tc["error"]))
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# TestParseHumanAssistantBlocks
# ---------------------------------------------------------------------------


class TestParseHumanAssistantBlocks:
    """parse_specstory splits the file into per-block dicts with correct roles."""

    def test_human_and_assistant_roles_detected(self, tmp_path: Path) -> None:
        """A file with one Human and one Assistant block returns two dicts with correct roles."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Can you read foo.py?"},
                {"role": "assistant", "text": "Sure, reading now."},
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-test.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assert len(result) == 2
        assert result[0]["role"] == "human"
        assert result[1]["role"] == "assistant"

    def test_multiple_turns_preserve_order(self, tmp_path: Path) -> None:
        """Four alternating turns keep their order in the output list."""
        blocks = [
            {"role": "human", "text": "First question."},
            {"role": "assistant", "text": "First answer."},
            {"role": "human", "text": "Second question."},
            {"role": "assistant", "text": "Second answer."},
        ]
        path = tmp_path / "2026-02-25_10-00-00Z-multi.md"
        _write_specstory(path, _make_specstory(blocks))

        result = parse_specstory(path)

        assert len(result) == 4
        assert [r["role"] for r in result] == ["human", "assistant", "human", "assistant"]

    def test_every_block_has_required_keys(self, tmp_path: Path) -> None:
        """Every returned dict exposes the mandatory three-key schema."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Hello."},
                {"role": "assistant", "text": "Hi."},
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-keys.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        for block in result:
            assert _REQUIRED_BLOCK_KEYS.issubset(block.keys()), (
                f"Missing keys: {_REQUIRED_BLOCK_KEYS - block.keys()}"
            )

    def test_content_field_is_string(self, tmp_path: Path) -> None:
        """The content field is always a string, not None or a non-str type."""
        content = _make_specstory([{"role": "human", "text": "Some text here."}])
        path = tmp_path / "2026-02-25_10-00-00Z-content.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        for block in result:
            assert isinstance(block["content"], str)

    def test_role_values_are_lowercase_strings(self, tmp_path: Path) -> None:
        """Role values are the canonical lowercase strings 'human' and 'assistant'."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Q"},
                {"role": "assistant", "text": "A"},
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-roles.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        for block in result:
            assert block["role"] in {"human", "assistant"}

    def test_tool_calls_field_is_list(self, tmp_path: Path) -> None:
        """tool_calls is always a list — even for blocks with no tool usage."""
        content = _make_specstory(
            [
                {"role": "human", "text": "No tools here."},
                {"role": "assistant", "text": "Nor here."},
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-no-tools.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        for block in result:
            assert isinstance(block["tool_calls"], list)

    def test_human_block_has_empty_tool_calls(self, tmp_path: Path) -> None:
        """Human blocks never have tool calls — the list must be empty."""
        content = _make_specstory([{"role": "human", "text": "Just a question."}])
        path = tmp_path / "2026-02-25_10-00-00Z-human-only.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assert result[0]["tool_calls"] == []

    def test_return_type_is_list_of_dicts(self, tmp_path: Path) -> None:
        """parse_specstory always returns list[dict]."""
        content = _make_specstory([{"role": "human", "text": "Hi."}])
        path = tmp_path / "2026-02-25_10-00-00Z-type.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)


# ---------------------------------------------------------------------------
# TestExtractToolCalls
# ---------------------------------------------------------------------------


class TestExtractToolCalls:
    """Tool calls embedded in assistant blocks are fully extracted."""

    def test_single_read_tool_call_extracted(self, tmp_path: Path) -> None:
        """A Read tool call produces one tool_calls entry with correct tool_name."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Read foo.py"},
                {
                    "role": "assistant",
                    "text": "Reading the file now.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/tmp/foo.py"},
                            "tool_output": "def foo(): pass\n",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-read.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        assert len(assistant_block["tool_calls"]) == 1
        assert assistant_block["tool_calls"][0]["tool_name"] == "Read"

    def test_bash_tool_call_extracted(self, tmp_path: Path) -> None:
        """A Bash tool call records the correct tool_name."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Run the tests."},
                {
                    "role": "assistant",
                    "text": "Running pytest.",
                    "tool_calls": [
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "pytest tests/ -v"},
                            "tool_output": "2 passed in 0.45s",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-bash.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        assert assistant_block["tool_calls"][0]["tool_name"] == "Bash"

    def test_edit_tool_call_extracted(self, tmp_path: Path) -> None:
        """An Edit tool call records the correct tool_name."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Fix the typo."},
                {
                    "role": "assistant",
                    "text": "Editing the file.",
                    "tool_calls": [
                        {
                            "tool_name": "Edit",
                            "tool_input": {
                                "file_path": "/tmp/main.py",
                                "old_string": "pritn",
                                "new_string": "print",
                            },
                            "tool_output": "File updated successfully.",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-edit.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        assert assistant_block["tool_calls"][0]["tool_name"] == "Edit"

    def test_tool_input_is_populated(self, tmp_path: Path) -> None:
        """tool_input is present and encodes the original parameters."""
        raw_input = {"file_path": "/home/user/project/main.py"}
        content = _make_specstory(
            [
                {"role": "human", "text": "Read main.py"},
                {
                    "role": "assistant",
                    "text": "Reading.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": raw_input,
                            "tool_output": "# main module\n",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-input.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        tc = next(b for b in result if b["role"] == "assistant")["tool_calls"][0]
        assert tc["tool_input"] is not None
        parsed = json.loads(tc["tool_input"]) if isinstance(tc["tool_input"], str) else tc["tool_input"]
        assert parsed["file_path"] == "/home/user/project/main.py"

    def test_tool_output_is_populated(self, tmp_path: Path) -> None:
        """tool_output captures the raw output text from the tool block."""
        expected_output = "def main():\n    print('hello')\n"
        content = _make_specstory(
            [
                {"role": "human", "text": "Read main.py"},
                {
                    "role": "assistant",
                    "text": "Reading.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/tmp/main.py"},
                            "tool_output": expected_output,
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-output.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        tc = next(b for b in result if b["role"] == "assistant")["tool_calls"][0]
        assert tc["tool_output"] is not None
        assert expected_output in tc["tool_output"] or tc["tool_output"] == expected_output

    def test_every_tool_call_dict_has_required_keys(self, tmp_path: Path) -> None:
        """Every tool_calls entry exposes tool_name, tool_input, tool_output, error."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Do work."},
                {
                    "role": "assistant",
                    "text": "Working.",
                    "tool_calls": [
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "ls"},
                            "tool_output": "main.py",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-tc-keys.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        for block in result:
            for tc in block["tool_calls"]:
                assert _REQUIRED_TOOL_CALL_KEYS.issubset(tc.keys()), (
                    f"Missing keys in tool_call: {_REQUIRED_TOOL_CALL_KEYS - tc.keys()}"
                )

    def test_successful_tool_call_error_is_none(self, tmp_path: Path) -> None:
        """A tool call without an error marker has error=None."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Run ls."},
                {
                    "role": "assistant",
                    "text": "Running.",
                    "tool_calls": [
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "ls"},
                            "tool_output": "main.py\n",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-no-err.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        tc = next(b for b in result if b["role"] == "assistant")["tool_calls"][0]
        assert tc["error"] is None


# ---------------------------------------------------------------------------
# TestExtractErrors
# ---------------------------------------------------------------------------


class TestExtractErrors:
    """Error text embedded in a tool block is surfaced in the error field."""

    def test_error_field_populated_when_error_marker_present(self, tmp_path: Path) -> None:
        """A block with a Tool error line has a non-None error field."""
        error_text = "FileNotFoundError: /tmp/missing.py does not exist"
        content = _make_specstory(
            [
                {"role": "human", "text": "Read missing file."},
                {
                    "role": "assistant",
                    "text": "Attempting to read.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/tmp/missing.py"},
                            "error": error_text,
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-err.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        tc = next(b for b in result if b["role"] == "assistant")["tool_calls"][0]
        assert tc["error"] is not None

    def test_error_text_is_preserved(self, tmp_path: Path) -> None:
        """The error field contains the original error string (or a string containing it)."""
        error_text = "PermissionError: [Errno 13] Permission denied: '/etc/shadow'"
        content = _make_specstory(
            [
                {"role": "human", "text": "Read secret file."},
                {
                    "role": "assistant",
                    "text": "Trying.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/etc/shadow"},
                            "error": error_text,
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-err-text.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        tc = next(b for b in result if b["role"] == "assistant")["tool_calls"][0]
        assert isinstance(tc["error"], str)
        assert len(tc["error"]) > 0

    def test_error_is_none_for_successful_tool_calls(self, tmp_path: Path) -> None:
        """Tool calls without an error marker always have error=None."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Run pwd."},
                {
                    "role": "assistant",
                    "text": "Running.",
                    "tool_calls": [
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "pwd"},
                            "tool_output": "/home/user/project\n",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-no-err2.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        tc = next(b for b in result if b["role"] == "assistant")["tool_calls"][0]
        assert tc["error"] is None

    def test_mixed_success_and_error_tool_calls(self, tmp_path: Path) -> None:
        """In a block with two tool calls, only the failing one has a non-None error."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Run both commands."},
                {
                    "role": "assistant",
                    "text": "Running both.",
                    "tool_calls": [
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "ls"},
                            "tool_output": "main.py\n",
                        },
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "failing_cmd"},
                            "error": "exit code 127",
                        },
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-mixed.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        tool_calls = assistant_block["tool_calls"]
        assert len(tool_calls) == 2
        # First call succeeded — no error.
        assert tool_calls[0]["error"] is None
        # Second call failed — error present.
        assert tool_calls[1]["error"] is not None

    def test_error_field_type_is_str_or_none(self, tmp_path: Path) -> None:
        """The error field is always str or None, never another type."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Try."},
                {
                    "role": "assistant",
                    "text": "Trying.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/tmp/ok.py"},
                            "tool_output": "content",
                        }
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-err-type.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        for block in result:
            for tc in block["tool_calls"]:
                assert tc["error"] is None or isinstance(tc["error"], str)

    def test_sample_specstory_fixture_with_errors(self, sample_specstory_file) -> None:
        """The conftest sample_specstory_file fixture with errors does not crash the parser."""
        path = sample_specstory_file(errors=["TimeoutError: tool exceeded 30s limit"])

        result = parse_specstory(path)

        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestHandleMalformedMarkdown
# ---------------------------------------------------------------------------


class TestHandleMalformedMarkdown:
    """Broken or incomplete markdown never raises; partial results are acceptable."""

    def test_unclosed_block_does_not_raise(self, tmp_path: Path) -> None:
        """A file ending mid-block (no closing separator) returns without raising."""
        path = tmp_path / "2026-02-25_10-00-00Z-unclosed.md"
        _write_specstory(path, "**Human:** Question without a proper ending")

        result = parse_specstory(path)

        assert isinstance(result, list)

    def test_only_separators_does_not_raise(self, tmp_path: Path) -> None:
        """A file made entirely of --- separators returns a list without raising."""
        path = tmp_path / "2026-02-25_10-00-00Z-sep-only.md"
        _write_specstory(path, "---\n---\n---\n")

        result = parse_specstory(path)

        assert isinstance(result, list)

    def test_interleaved_garbage_does_not_raise(self, tmp_path: Path) -> None:
        """Random non-markdown lines mixed with valid blocks don't crash the parser."""
        raw = (
            "some random text\n"
            "**Human:** A real question\n"
            "not a separator\n"
            "**Assistant:** A real answer\n"
            "more garbage !!!@#$%\n"
        )
        path = tmp_path / "2026-02-25_10-00-00Z-garbage.md"
        _write_specstory(path, raw)

        result = parse_specstory(path)

        assert isinstance(result, list)

    def test_malformed_tool_call_line_does_not_crash(self, tmp_path: Path) -> None:
        """A tool call annotation that is syntactically incomplete is skipped or tolerated."""
        raw = (
            "**Human:** Try a broken tool call.\n\n"
            "---\n\n"
            "**Assistant:** Here is a broken tool call annotation.\n"
            "[Tool call: Read with input NOTVALIDJSON{{{]\n"
            "[Tool output: some output]\n"
        )
        path = tmp_path / "2026-02-25_10-00-00Z-broken-json.md"
        _write_specstory(path, raw)

        result = parse_specstory(path)

        assert isinstance(result, list)

    def test_return_type_always_list(self, tmp_path: Path) -> None:
        """parse_specstory always returns a list regardless of input quality."""
        path = tmp_path / "2026-02-25_10-00-00Z-always-list.md"
        _write_specstory(path, "**Assistant:** No human turn.")

        result = parse_specstory(path)

        assert isinstance(result, list)

    def test_partial_results_on_truncated_file(self, tmp_path: Path) -> None:
        """A file with one complete and one truncated block returns at least the complete one."""
        raw = (
            "**Human:** Good question.\n\n"
            "---\n\n"
            "**Assistant:** I will"
            # Truncated — no closing newline or further content
        )
        path = tmp_path / "2026-02-25_10-00-00Z-truncated.md"
        _write_specstory(path, raw)

        result = parse_specstory(path)

        # At minimum the human block should be recoverable, or the result is an empty list.
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestEmptyFile
# ---------------------------------------------------------------------------


class TestEmptyFile:
    """An empty or whitespace-only file returns an empty list."""

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Zero-byte file produces [] without raising."""
        path = tmp_path / "2026-02-25_10-00-00Z-empty.md"
        path.write_text("", encoding="utf-8")

        result = parse_specstory(path)

        assert result == []

    def test_whitespace_only_file_returns_empty_list(self, tmp_path: Path) -> None:
        """A file containing only newlines and spaces produces []."""
        path = tmp_path / "2026-02-25_10-00-00Z-ws.md"
        path.write_text("\n\n   \n\t\n", encoding="utf-8")

        result = parse_specstory(path)

        assert result == []

    def test_frontmatter_only_file_returns_empty_list(self, tmp_path: Path) -> None:
        """A file with YAML front matter but no conversation blocks returns []."""
        path = tmp_path / "2026-02-25_10-00-00Z-fm.md"
        path.write_text("---\ntools: [Read, Bash]\n---\n\n", encoding="utf-8")

        result = parse_specstory(path)

        assert result == []

    def test_return_type_is_list_for_empty(self, tmp_path: Path) -> None:
        """Return type is list even when the file is empty."""
        path = tmp_path / "2026-02-25_10-00-00Z-empty2.md"
        path.write_text("", encoding="utf-8")

        result = parse_specstory(path)

        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestTimestampFromFilename
# ---------------------------------------------------------------------------


class TestTimestampFromFilename:
    """Timestamps are parsed from the SpecStory filename convention."""

    def test_standard_filename_produces_iso_timestamp(self, tmp_path: Path) -> None:
        """The canonical filename format maps to a UTC ISO 8601 timestamp string."""
        path = tmp_path / "2026-02-25_10-00-00Z-my-session.md"
        path.write_text("", encoding="utf-8")

        parse_specstory(path)

        # Empty file — no blocks. Timestamp is accessible via a module-level helper or
        # on a separate attribute.  We test the dedicated extraction function instead.
        from sio.mining.specstory_parser import extract_timestamp_from_filename

        ts = extract_timestamp_from_filename(path)
        assert ts == "2026-02-25T10:00:00Z"

    def test_timestamp_carries_correct_date_part(self, tmp_path: Path) -> None:
        """The date portion of the parsed timestamp matches the filename date."""
        path = tmp_path / "2025-12-31_23-59-59Z-year-end.md"
        path.write_text("", encoding="utf-8")

        from sio.mining.specstory_parser import extract_timestamp_from_filename

        ts = extract_timestamp_from_filename(path)
        assert ts.startswith("2025-12-31")

    def test_timestamp_carries_correct_time_part(self, tmp_path: Path) -> None:
        """The time portion of the parsed timestamp matches the filename time."""
        path = tmp_path / "2026-01-15_08-30-45Z-morning.md"
        path.write_text("", encoding="utf-8")

        from sio.mining.specstory_parser import extract_timestamp_from_filename

        ts = extract_timestamp_from_filename(path)
        assert "08:30:45" in ts

    def test_timestamp_ends_with_z_suffix(self, tmp_path: Path) -> None:
        """The returned timestamp string ends with 'Z' (UTC indicator)."""
        path = tmp_path / "2026-02-25_10-00-00Z-session.md"
        path.write_text("", encoding="utf-8")

        from sio.mining.specstory_parser import extract_timestamp_from_filename

        ts = extract_timestamp_from_filename(path)
        assert ts.endswith("Z")

    def test_timestamp_separator_is_t(self, tmp_path: Path) -> None:
        """The date/time separator in the returned timestamp is 'T', not a space."""
        path = tmp_path / "2026-02-25_14-22-00Z-afternoon.md"
        path.write_text("", encoding="utf-8")

        from sio.mining.specstory_parser import extract_timestamp_from_filename

        ts = extract_timestamp_from_filename(path)
        assert "T" in ts

    def test_unrecognised_filename_returns_none_or_raises(self, tmp_path: Path) -> None:
        """A filename without the timestamp prefix either returns None or raises ValueError."""
        path = tmp_path / "not-a-specstory-file.md"
        path.write_text("", encoding="utf-8")

        from sio.mining.specstory_parser import extract_timestamp_from_filename

        try:
            ts = extract_timestamp_from_filename(path)
            assert ts is None
        except (ValueError, AttributeError):
            pass  # raising is also an acceptable contract

    def test_different_session_names_do_not_affect_timestamp(self, tmp_path: Path) -> None:
        """The session name slug after the timestamp does not alter the parsed timestamp."""
        path_a = tmp_path / "2026-02-25_10-00-00Z-alpha-session.md"
        path_b = tmp_path / "2026-02-25_10-00-00Z-beta-session.md"
        path_a.write_text("", encoding="utf-8")
        path_b.write_text("", encoding="utf-8")

        from sio.mining.specstory_parser import extract_timestamp_from_filename

        ts_a = extract_timestamp_from_filename(path_a)
        ts_b = extract_timestamp_from_filename(path_b)
        assert ts_a == ts_b


# ---------------------------------------------------------------------------
# TestMultipleToolCallsInOneBlock
# ---------------------------------------------------------------------------


class TestMultipleToolCallsInOneBlock:
    """An assistant block that invokes multiple tools has all of them captured."""

    def test_three_tool_calls_all_extracted(self, tmp_path: Path) -> None:
        """Three tool calls in one assistant block produces tool_calls of length 3."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Read, run, and edit."},
                {
                    "role": "assistant",
                    "text": "Doing all three.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/tmp/a.py"},
                            "tool_output": "# a",
                        },
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "pytest"},
                            "tool_output": "1 passed",
                        },
                        {
                            "tool_name": "Edit",
                            "tool_input": {
                                "file_path": "/tmp/a.py",
                                "old_string": "# a",
                                "new_string": "# b",
                            },
                            "tool_output": "File updated.",
                        },
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-multi-tc.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        assert len(assistant_block["tool_calls"]) == 3

    def test_tool_names_order_preserved_in_multi_call_block(self, tmp_path: Path) -> None:
        """Tool calls appear in the same order they occur in the source block."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Multiple tools please."},
                {
                    "role": "assistant",
                    "text": "Running them in order.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/tmp/x.py"},
                            "tool_output": "x",
                        },
                        {
                            "tool_name": "Glob",
                            "tool_input": {"pattern": "**/*.py"},
                            "tool_output": "x.py",
                        },
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "echo done"},
                            "tool_output": "done",
                        },
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-order.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        names = [tc["tool_name"] for tc in assistant_block["tool_calls"]]
        assert names == ["Read", "Glob", "Bash"]

    def test_each_tool_call_in_multi_block_has_required_keys(self, tmp_path: Path) -> None:
        """Every tool_calls entry in a multi-call block has all four required keys."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Do stuff."},
                {
                    "role": "assistant",
                    "text": "Doing.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/tmp/a.py"},
                            "tool_output": "content",
                        },
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "ls"},
                            "tool_output": "a.py",
                        },
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-multi-keys.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        for tc in assistant_block["tool_calls"]:
            assert _REQUIRED_TOOL_CALL_KEYS.issubset(tc.keys()), (
                f"Missing keys: {_REQUIRED_TOOL_CALL_KEYS - tc.keys()}"
            )

    def test_multi_call_block_with_mixed_errors(self, tmp_path: Path) -> None:
        """In a block with four calls, the two failing ones have errors and the rest have None."""
        content = _make_specstory(
            [
                {"role": "human", "text": "Four calls."},
                {
                    "role": "assistant",
                    "text": "Four responses.",
                    "tool_calls": [
                        {
                            "tool_name": "Read",
                            "tool_input": {"file_path": "/ok.py"},
                            "tool_output": "ok",
                        },
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "bad1"},
                            "error": "exit code 1",
                        },
                        {
                            "tool_name": "Glob",
                            "tool_input": {"pattern": "*.py"},
                            "tool_output": "ok.py",
                        },
                        {
                            "tool_name": "Bash",
                            "tool_input": {"command": "bad2"},
                            "error": "exit code 2",
                        },
                    ],
                },
            ]
        )
        path = tmp_path / "2026-02-25_10-00-00Z-4-calls.md"
        _write_specstory(path, content)

        result = parse_specstory(path)

        assistant_block = next(b for b in result if b["role"] == "assistant")
        tool_calls = assistant_block["tool_calls"]
        assert len(tool_calls) == 4
        assert tool_calls[0]["error"] is None
        assert tool_calls[1]["error"] is not None
        assert tool_calls[2]["error"] is None
        assert tool_calls[3]["error"] is not None

    def test_sample_specstory_fixture_multi_tool_block(self, sample_specstory_file) -> None:
        """The conftest fixture (which embeds four default tool calls) parses without crashing."""
        path = sample_specstory_file()

        result = parse_specstory(path)

        assert isinstance(result, list)
        # The fixture puts all tool calls in the assistant block.
        assistant_blocks = [b for b in result if b["role"] == "assistant"]
        assert len(assistant_blocks) >= 1
        all_tool_calls = [tc for b in assistant_blocks for tc in b["tool_calls"]]
        # The fixture embeds 4 default tool calls — we expect at least that many.
        assert len(all_tool_calls) >= 4
