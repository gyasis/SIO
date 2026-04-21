"""Tests for sio.suggestions.skill_generator."""

from __future__ import annotations

import os
import re
import tempfile

import pytest

from sio.suggestions.skill_generator import (
    generate_skill_from_flow,
    generate_skill_from_pattern,
    write_skill_file,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def realistic_pattern() -> dict:
    """A pattern dict matching what the patterns table produces."""
    return {
        "id": 42,
        "pattern_id": "pat-abc123",
        "description": "File not found when editing non-existent paths",
        "tool_name": "Edit",
        "error_count": 12,
        "session_count": 5,
        "grade": 0.85,
        "confidence": 0.85,
        "rank_score": 7.2,
    }


@pytest.fixture()
def positive_examples() -> list[dict]:
    """Positive signal dicts matching positive_records schema."""
    return [
        {
            "signal_type": "confirmation",
            "signal_text": "yes exactly",
            "context_before": "I read the file first, then applied the edit",
            "tool_name": "Read",
            "timestamp": "2025-06-01T12:00:00Z",
        },
        {
            "signal_type": "gratitude",
            "signal_text": "thanks, that looks good",
            "context_before": "Verified the edit was applied correctly",
            "tool_name": "Edit",
            "timestamp": "2025-06-01T12:01:00Z",
        },
        {
            "signal_type": "implicit_approval",
            "signal_text": "ok",
            "context_before": "Ran the tests and all passed",
            "tool_name": "Bash",
            "timestamp": "2025-06-01T12:02:00Z",
        },
    ]


@pytest.fixture()
def flow_ngram_4() -> tuple[str, ...]:
    """A 4-tool flow n-gram."""
    return ("Read", "Grep", "Edit", "Bash")


@pytest.fixture()
def session_examples() -> list[dict]:
    """Session context dicts for flow-based skill generation."""
    return [
        {
            "user_goal": "Fix the import error in the CLI module",
            "final_outcome": "Tests pass now, thanks",
            "duration_seconds": 120.0,
            "tool_sequence": [
                {"tool": "Read", "ext": ".py"},
                {"tool": "Grep", "ext": ""},
                {"tool": "Edit", "ext": ".py"},
                {"tool": "Bash", "ext": ""},
            ],
        },
        {
            "user_goal": "Update the database schema migration",
            "final_outcome": "Looks correct",
            "duration_seconds": 90.0,
            "tool_sequence": [
                {"tool": "Read", "ext": ".sql"},
                {"tool": "Grep", "ext": ""},
                {"tool": "Edit", "ext": ".sql"},
                {"tool": "Bash", "ext": ""},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Tests for generate_skill_from_pattern
# ---------------------------------------------------------------------------


class TestGenerateSkillFromPattern:
    """Tests for the pattern-based skill generator."""

    def test_has_all_required_sections(self, realistic_pattern, positive_examples):
        """Output must contain all four required sections."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        assert "# Skill:" in result
        assert "## When to Use" in result
        assert "## Steps" in result
        assert "## Guardrails" in result
        assert "## Why This Skill Exists" in result

    def test_title_derived_from_description(self, realistic_pattern, positive_examples):
        """Title should incorporate the pattern description."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        assert "File not found" in result

    def test_trigger_conditions_include_tool(self, realistic_pattern, positive_examples):
        """When to Use should reference the tool name."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        assert "`Edit`" in result

    def test_trigger_conditions_from_error_context(self, realistic_pattern, positive_examples):
        """When to Use should include error-context triggers."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        # "not found" in description should produce a file-existence trigger
        assert "may not exist" in result

    def test_steps_from_flow_sequence(self, realistic_pattern, positive_examples):
        """When flow_sequence is provided, steps follow that order."""
        flow = ["Read", "Grep", "Edit", "Bash"]
        result = generate_skill_from_pattern(
            realistic_pattern, positive_examples, flow_sequence=flow
        )
        # Steps should be numbered and follow the flow order
        assert "1. Call `Read`" in result
        assert "2. Call `Grep`" in result
        assert "3. Call `Edit`" in result
        assert "4. Call `Bash`" in result

    def test_steps_inferred_from_positives(self, realistic_pattern, positive_examples):
        """Without flow_sequence, steps come from positive examples."""
        result = generate_skill_from_pattern(
            realistic_pattern, positive_examples, flow_sequence=None
        )
        # Should reference the tools from positive examples
        assert "`Read`" in result
        assert "`Edit`" in result

    def test_guardrails_negate_error_pattern(self, realistic_pattern, positive_examples):
        """Guardrails should include ALWAYS/NEVER rules matching the error."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        # "not found" pattern should produce file-existence guardrails
        assert "ALWAYS" in result
        assert "NEVER" in result
        assert "verify" in result.lower() or "exist" in result.lower()

    def test_provenance_includes_stats(self, realistic_pattern, positive_examples):
        """Why This Skill Exists should show occurrence and session counts."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        assert "12" in result  # error_count
        assert "5" in result  # session_count
        assert "0.85" in result  # confidence

    def test_provenance_includes_auto_generated_note(self, realistic_pattern, positive_examples):
        """Provenance should note this was auto-generated by SIO."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        assert "auto-generated by SIO" in result

    def test_output_is_valid_markdown(self, realistic_pattern, positive_examples):
        """Output should be valid markdown with headings and numbered steps."""
        result = generate_skill_from_pattern(realistic_pattern, positive_examples)
        # Has markdown headings
        headings = re.findall(r"^#{1,2} .+", result, re.MULTILINE)
        assert len(headings) >= 4

        # Has numbered steps
        numbered = re.findall(r"^\d+\. .+", result, re.MULTILINE)
        assert len(numbered) >= 1

        # Has bullet points
        bullets = re.findall(r"^- .+", result, re.MULTILINE)
        assert len(bullets) >= 1

    def test_empty_positive_examples(self, realistic_pattern):
        """Should produce valid output even with no positive examples."""
        result = generate_skill_from_pattern(realistic_pattern, [])
        assert "# Skill:" in result
        assert "## Steps" in result
        # Should fall back to generic steps
        assert "1." in result

    def test_single_tool_pattern(self):
        """Single tool with minimal metadata should still work."""
        pattern = {
            "tool_name": "Bash",
            "description": "timeout when running long commands",
            "error_count": 3,
            "session_count": 1,
        }
        result = generate_skill_from_pattern(pattern, [])
        assert "# Skill:" in result
        assert "`Bash`" in result
        assert "timeout" in result.lower() or "NEVER" in result

    def test_mcp_tool_name_cleaned(self):
        """MCP tool names with prefixes should be cleaned for display."""
        pattern = {
            "tool_name": "mcp__graphiti__search_nodes",
            "description": "Rate limit exceeded on search",
            "error_count": 5,
            "session_count": 2,
        }
        result = generate_skill_from_pattern(pattern, [])
        assert "graphiti.search_nodes" in result
        assert "mcp__" not in result

    def test_pattern_with_label_fallback(self):
        """When description is missing, should fall back to label."""
        pattern = {
            "label": "Edit file not found errors",
            "tool_name": "Edit",
            "count": 8,
            "session_count": 3,
            "confidence": 0.9,
        }
        result = generate_skill_from_pattern(pattern, [])
        assert "Edit file not found" in result


# ---------------------------------------------------------------------------
# Tests for generate_skill_from_flow
# ---------------------------------------------------------------------------


class TestGenerateSkillFromFlow:
    """Tests for the flow-based skill generator."""

    def test_has_all_required_sections(self, flow_ngram_4, session_examples):
        """Output must contain all four required sections."""
        result = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        assert "# Skill:" in result
        assert "## When to Use" in result
        assert "## Steps" in result
        assert "## Guardrails" in result
        assert "## Why This Skill Exists" in result

    def test_steps_match_flow_order(self, flow_ngram_4, session_examples):
        """Steps should exactly follow the n-gram tool order."""
        result = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        assert "1. Call `Read`" in result
        assert "2. Call `Grep`" in result
        assert "3. Call `Edit`" in result
        assert "4. Call `Bash`" in result

    def test_title_contains_flow_arrow_notation(self, flow_ngram_4, session_examples):
        """Title should show the tool flow with arrow notation."""
        result = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        assert "Read -> Grep -> Edit -> Bash" in result

    def test_success_rate_in_provenance(self, flow_ngram_4, session_examples):
        """Provenance should include the success rate."""
        result = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        assert "85%" in result

    def test_session_count_in_provenance(self, flow_ngram_4, session_examples):
        """Provenance should include how many sessions exhibited this flow."""
        result = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        assert "2" in result  # len(session_examples)

    def test_rle_suffix_handled(self, session_examples):
        """Tool names with RLE '+' suffix should be handled gracefully."""
        ngram = ("Read.py+", "Grep", "Edit.py", "Bash")
        result = generate_skill_from_flow(ngram, 0.75, session_examples)
        # RLE items should show "repeat as needed"
        assert "repeat as needed" in result
        # Non-RLE items should not
        lines = result.split("\n")
        grep_line = [ln for ln in lines if "Grep" in ln and ln.strip().startswith("2.")]
        assert grep_line
        assert "repeat as needed" not in grep_line[0]

    def test_edit_guardrail_when_edit_in_flow(self, session_examples):
        """Flows containing Edit should get the read-before-edit guardrail."""
        ngram = ("Read", "Edit")
        result = generate_skill_from_flow(ngram, 0.9, session_examples)
        assert "read the target file before editing" in result.lower()

    def test_bash_guardrail_when_bash_in_flow(self, session_examples):
        """Flows containing Bash should get the exit-code guardrail."""
        ngram = ("Read", "Bash")
        result = generate_skill_from_flow(ngram, 0.8, session_examples)
        assert "exit code" in result.lower()

    def test_goals_extracted_from_session_examples(self, flow_ngram_4, session_examples):
        """When to Use should reference user goals from session examples."""
        result = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        assert "Fix the import error" in result or "Task resembles" in result

    def test_empty_session_examples(self, flow_ngram_4):
        """Should produce valid output with empty session examples."""
        result = generate_skill_from_flow(flow_ngram_4, 0.5, [])
        assert "# Skill:" in result
        assert "## Steps" in result
        assert "50%" in result

    def test_output_is_valid_markdown(self, flow_ngram_4, session_examples):
        """Output should be valid markdown."""
        result = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        headings = re.findall(r"^#{1,2} .+", result, re.MULTILINE)
        assert len(headings) >= 4
        numbered = re.findall(r"^\d+\. .+", result, re.MULTILINE)
        assert len(numbered) == 4  # Exactly 4 tools in the n-gram


# ---------------------------------------------------------------------------
# Tests for write_skill_file
# ---------------------------------------------------------------------------


class TestWriteSkillFile:
    """Tests for the file writer."""

    def test_creates_file(self):
        """File should be created and readable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            content = "# Skill: Test\n\nHello world."
            path = write_skill_file(content, "test-skill", target_dir=tmpdir)
            assert os.path.isfile(path)
            with open(path, encoding="utf-8") as f:
                assert f.read() == content

    def test_returns_absolute_path(self):
        """Returned path should be absolute."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_skill_file("content", "slug", target_dir=tmpdir)
            assert os.path.isabs(path)

    def test_slug_sanitized(self):
        """Unsafe characters in slug should be sanitized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_skill_file("# test", "My Skill / Version: 2", target_dir=tmpdir)
            filename = os.path.basename(path)
            assert "/" not in filename
            assert ":" not in filename
            assert " " not in filename
            assert filename.endswith(".md")

    def test_creates_target_dir(self):
        """Should create the target directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "dir")
            path = write_skill_file("content", "test", target_dir=nested)
            assert os.path.isfile(path)

    def test_empty_slug_gets_default(self):
        """Empty slug should get a fallback name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_skill_file("content", "", target_dir=tmpdir)
            assert "unnamed-skill" in os.path.basename(path)

    def test_long_slug_truncated(self):
        """Slugs longer than 60 chars should be truncated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            long_slug = "a" * 200
            path = write_skill_file("content", long_slug, target_dir=tmpdir)
            # filename = slug + ".md", slug capped at 60
            basename = os.path.basename(path)
            assert len(basename) <= 60 + 3  # 60 chars + ".md"

    def test_identical_content_skips_write(self):
        """Writing identical content to the same slug should return the same path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = write_skill_file("same content", "same-slug", target_dir=tmpdir)
            path2 = write_skill_file("same content", "same-slug", target_dir=tmpdir)
            assert path1 == path2
            # Only one file should exist
            files = os.listdir(tmpdir)
            assert len(files) == 1

    def test_different_content_gets_numeric_suffix(self):
        """Writing different content to the same slug should create a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = write_skill_file("version 1", "same-slug", target_dir=tmpdir)
            path2 = write_skill_file("version 2", "same-slug", target_dir=tmpdir)
            assert path1 != path2
            assert os.path.basename(path2) == "same-slug-2.md"
            with open(path1, encoding="utf-8") as f:
                assert f.read() == "version 1"
            with open(path2, encoding="utf-8") as f:
                assert f.read() == "version 2"

    def test_multiple_different_content_increments_suffix(self):
        """Successive different content should increment the suffix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_skill_file("v1", "slug", target_dir=tmpdir)
            write_skill_file("v2", "slug", target_dir=tmpdir)
            path3 = write_skill_file("v3", "slug", target_dir=tmpdir)
            assert os.path.basename(path3) == "slug-3.md"


# ---------------------------------------------------------------------------
# Integration: full pipeline test
# ---------------------------------------------------------------------------


class TestIntegration:
    """End-to-end tests combining generation + writing."""

    def test_pattern_to_file(self, realistic_pattern, positive_examples):
        """Generate from pattern and write to file."""
        content = generate_skill_from_pattern(realistic_pattern, positive_examples)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_skill_file(content, "file-not-found-edit", target_dir=tmpdir)
            assert os.path.isfile(path)
            with open(path, encoding="utf-8") as f:
                written = f.read()
            assert "# Skill:" in written
            assert "## Guardrails" in written

    def test_flow_to_file(self, flow_ngram_4, session_examples):
        """Generate from flow and write to file."""
        content = generate_skill_from_flow(flow_ngram_4, 0.85, session_examples)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_skill_file(content, "read-grep-edit-bash", target_dir=tmpdir)
            assert os.path.isfile(path)
            with open(path, encoding="utf-8") as f:
                written = f.read()
            assert "Read -> Grep -> Edit -> Bash" in written
