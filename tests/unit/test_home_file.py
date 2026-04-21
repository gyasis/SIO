"""T035 [US4] Unit tests for sio.suggestions.home_file — ranked markdown output.

Tests cover:
- write_suggestions(suggestions: list[dict], path: str) -> None
    Writes a prioritised markdown document grouping suggestions by confidence:
      high   > 0.7
      medium  0.4–0.7 (inclusive of 0.4, exclusive of 0.7)
      low    < 0.4
    Each entry includes approve/reject CLI commands.

These tests are expected to FAIL until the implementation is written.
"""

from __future__ import annotations

from pathlib import Path

from sio.suggestions.home_file import write_suggestions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_suggestion(
    *,
    id: int = 1,
    pattern_id: int = 1,
    dataset_id: int = 1,
    description: str = "Add a rule to always verify file paths before reading.",
    confidence: float = 0.8,
    proposed_change: str = "## File Safety\nAlways check file exists before calling Read.",
    target_file: str = "CLAUDE.md",
    change_type: str = "claude_md_rule",
    status: str = "pending",
) -> dict:
    """Build a minimal suggestion dict matching the generator contract."""
    return {
        "id": id,
        "pattern_id": pattern_id,
        "dataset_id": dataset_id,
        "description": description,
        "confidence": confidence,
        "proposed_change": proposed_change,
        "target_file": target_file,
        "change_type": change_type,
        "status": status,
    }


def _read(path: Path) -> str:
    """Read the file at *path* and return its text content."""
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestWritesValidMarkdown:
    """Output file must be valid markdown containing level-1 or level-2 headings."""

    def test_output_file_is_created(self, tmp_path: Path) -> None:
        target = tmp_path / "suggestions.md"
        write_suggestions([_make_suggestion()], str(target))
        assert target.exists()

    def test_output_contains_heading(self, tmp_path: Path) -> None:
        target = tmp_path / "suggestions.md"
        write_suggestions([_make_suggestion()], str(target))
        content = _read(target)
        # At minimum a top-level or section heading must be present.
        assert content.startswith("#") or "\n#" in content

    def test_output_is_non_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "suggestions.md"
        write_suggestions([_make_suggestion()], str(target))
        assert len(_read(target).strip()) > 0

    def test_output_contains_suggestion_description(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(description="Always verify file paths before reading.")
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        assert "Always verify file paths before reading." in _read(target)

    def test_output_contains_confidence_value(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(confidence=0.85)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target)
        # Confidence should appear as a numeric value in the output.
        assert "0.85" in content or "85" in content

    def test_output_contains_proposed_change(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(
            proposed_change="Always check file exists before calling Read."
        )
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        assert "Always check file exists before calling Read." in _read(target)

    def test_output_contains_target_file(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(target_file="CLAUDE.md")
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        assert "CLAUDE.md" in _read(target)


class TestRankedSections:
    """Suggestions must be grouped under High / Medium / Low priority headings."""

    def test_high_section_present_when_high_confidence(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(id=1, confidence=0.9)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target).lower()
        assert "high" in content

    def test_medium_section_present_when_medium_confidence(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(id=1, confidence=0.5)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target).lower()
        assert "medium" in content

    def test_low_section_present_when_low_confidence(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(id=1, confidence=0.2)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target).lower()
        assert "low" in content

    def test_high_appears_before_medium_in_output(self, tmp_path: Path) -> None:
        suggestions = [
            _make_suggestion(id=1, confidence=0.9, description="High priority suggestion"),
            _make_suggestion(id=2, confidence=0.5, description="Medium priority suggestion"),
        ]
        target = tmp_path / "suggestions.md"
        write_suggestions(suggestions, str(target))
        content = _read(target).lower()
        assert content.index("high") < content.index("medium")

    def test_medium_appears_before_low_in_output(self, tmp_path: Path) -> None:
        suggestions = [
            _make_suggestion(id=1, confidence=0.5, description="Medium priority suggestion"),
            _make_suggestion(id=2, confidence=0.2, description="Low priority suggestion"),
        ]
        target = tmp_path / "suggestions.md"
        write_suggestions(suggestions, str(target))
        content = _read(target).lower()
        assert content.index("medium") < content.index("low")

    def test_high_suggestion_lands_in_high_section(self, tmp_path: Path) -> None:
        """A suggestion with confidence=0.9 must appear under the High section,
        not Medium or Low."""
        suggestions = [
            _make_suggestion(id=1, confidence=0.9, description="HighConf suggestion text"),
            _make_suggestion(id=2, confidence=0.5, description="MedConf suggestion text"),
            _make_suggestion(id=3, confidence=0.2, description="LowConf suggestion text"),
        ]
        target = tmp_path / "suggestions.md"
        write_suggestions(suggestions, str(target))
        content = _read(target)

        # Find section boundaries by looking for heading keywords.
        content_lower = content.lower()
        high_pos = content_lower.index("high")
        medium_pos = content_lower.index("medium")
        low_pos = content_lower.index("low")

        high_text_pos = content.index("HighConf suggestion text")
        med_text_pos = content.index("MedConf suggestion text")
        low_text_pos = content.index("LowConf suggestion text")

        # Each suggestion must sit within its expected section.
        assert high_pos < high_text_pos < medium_pos
        assert medium_pos < med_text_pos < low_pos
        assert low_pos < low_text_pos

    def test_boundary_confidence_07_treated_as_high(self, tmp_path: Path) -> None:
        """Confidence strictly > 0.7 is high; we test 0.71 to confirm."""
        suggestion = _make_suggestion(id=1, confidence=0.71)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target).lower()
        assert "high" in content

    def test_boundary_confidence_04_treated_as_medium(self, tmp_path: Path) -> None:
        """Confidence == 0.4 (inclusive lower bound of medium) lands in medium."""
        suggestion = _make_suggestion(id=1, confidence=0.4)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target).lower()
        assert "medium" in content

    def test_three_tier_output_has_all_sections_when_all_present(self, tmp_path: Path) -> None:
        suggestions = [
            _make_suggestion(id=1, confidence=0.95),
            _make_suggestion(id=2, confidence=0.55),
            _make_suggestion(id=3, confidence=0.15),
        ]
        target = tmp_path / "suggestions.md"
        write_suggestions(suggestions, str(target))
        content = _read(target).lower()
        assert "high" in content
        assert "medium" in content
        assert "low" in content


class TestIncludesApproveReject:
    """Every suggestion entry must include sio approve <id> and sio reject <id> commands."""

    def test_approve_command_present(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(id=7)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        assert "sio approve 7" in _read(target)

    def test_reject_command_present(self, tmp_path: Path) -> None:
        suggestion = _make_suggestion(id=7)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        assert "sio reject 7" in _read(target)

    def test_commands_present_for_multiple_suggestions(self, tmp_path: Path) -> None:
        suggestions = [
            _make_suggestion(id=10, confidence=0.9),
            _make_suggestion(id=11, confidence=0.5),
            _make_suggestion(id=12, confidence=0.2),
        ]
        target = tmp_path / "suggestions.md"
        write_suggestions(suggestions, str(target))
        content = _read(target)
        for suggestion in suggestions:
            sid = suggestion["id"]
            assert f"sio approve {sid}" in content, f"approve command missing for id={sid}"
            assert f"sio reject {sid}" in content, f"reject command missing for id={sid}"

    def test_approve_command_appears_in_code_block_or_inline_code(self, tmp_path: Path) -> None:
        """Commands should be formatted as code so they are copy-pasteable."""
        suggestion = _make_suggestion(id=5)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target)
        # Either a fenced block or inline backtick wrapping must exist.
        assert "`sio approve 5`" in content or "```" in content

    def test_approve_before_reject_per_entry(self, tmp_path: Path) -> None:
        """Within any single suggestion block, approve command comes first."""
        suggestion = _make_suggestion(id=3)
        target = tmp_path / "suggestions.md"
        write_suggestions([suggestion], str(target))
        content = _read(target)
        approve_pos = content.index("sio approve 3")
        reject_pos = content.index("sio reject 3")
        assert approve_pos < reject_pos


class TestHandlesEmptySuggestions:
    """An empty suggestions list must produce a file that says 'No suggestions'."""

    def test_empty_list_produces_file(self, tmp_path: Path) -> None:
        target = tmp_path / "suggestions.md"
        write_suggestions([], str(target))
        assert target.exists()

    def test_empty_list_says_no_suggestions(self, tmp_path: Path) -> None:
        target = tmp_path / "suggestions.md"
        write_suggestions([], str(target))
        content = _read(target).lower()
        assert "no suggestions" in content

    def test_empty_list_still_valid_markdown(self, tmp_path: Path) -> None:
        """Even for empty input the file must contain at least one heading."""
        target = tmp_path / "suggestions.md"
        write_suggestions([], str(target))
        content = _read(target)
        # A heading character must be present somewhere.
        assert "#" in content

    def test_empty_list_does_not_contain_approve_reject(self, tmp_path: Path) -> None:
        """No approve/reject commands in the file when there are no suggestions."""
        target = tmp_path / "suggestions.md"
        write_suggestions([], str(target))
        content = _read(target)
        assert "sio approve" not in content
        assert "sio reject" not in content
