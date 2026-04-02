"""Unit tests for sio.mining.approval_detector — T021 [US2].

Tests the detect_approvals() function which analyzes parsed message sequences
to determine whether the user approved or rejected each tool call based on
the user's response following tool execution.

The function signature under test:

    detect_approvals(
        parsed_messages: list[dict],
    ) -> dict

Returned dict schema:
    {
        "overall_approval_rate": float,     # 0.0 to 1.0
        "total_tool_calls": int,
        "approved_count": int,
        "rejected_count": int,
        "per_tool": {
            "<tool_name>": {
                "approved": int,
                "rejected": int,
                "total": int,
                "approval_rate": float,
            },
            ...
        },
        "details": [
            {
                "tool_name": str,
                "timestamp": str,
                "approved": bool,
                "user_response": str,
            },
            ...
        ],
    }

Acceptance criteria: FR-010, FR-011.
These tests are TDD — they WILL fail until implementation is written.
"""

from __future__ import annotations

import pytest

from sio.mining.approval_detector import detect_approvals

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = "approval-test-session-001"
_TS_BASE = "2026-03-01T10:00:{:02d}.000Z"


def _ts(offset: int = 0) -> str:
    return _TS_BASE.format(offset % 60)


def _human(content: str, offset: int = 0) -> dict:
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
# Fixture: 10 tool calls, 8 approved, 2 rejected
# ---------------------------------------------------------------------------


@pytest.fixture
def ten_tool_calls_eight_approved() -> list[dict]:
    """Conversation with 10 tool calls: 8 user-approved, 2 user-rejected.

    Tool breakdown:
        Read  - 3 calls, 3 approved  (100%)
        Edit  - 5 calls, 3 approved  (60%)
        Bash  - 2 calls, 2 approved  (100%)
    """
    messages = []
    offset = 0

    # --- Read x3 (all approved) ---
    for i in range(3):
        messages.append(
            _assistant(
                f"Reading file {i}.",
                tool_name="Read",
                tool_input={"file_path": f"/tmp/file_{i}.py"},
                tool_output=f"content of file {i}",
                offset=offset,
            )
        )
        offset += 1
        messages.append(_human("ok looks good", offset=offset))
        offset += 1

    # --- Edit x5 (3 approved, 2 rejected) ---
    for i in range(5):
        messages.append(
            _assistant(
                f"Editing file {i}.",
                tool_name="Edit",
                tool_input={
                    "file_path": f"/tmp/file_{i}.py",
                    "old_string": "old",
                    "new_string": "new",
                },
                tool_output="File updated successfully.",
                offset=offset,
            )
        )
        offset += 1
        if i < 3:
            # Approved
            messages.append(_human("yes that's correct", offset=offset))
        else:
            # Rejected
            messages.append(
                _human("no that's wrong, revert that change", offset=offset)
            )
        offset += 1

    # --- Bash x2 (all approved) ---
    for i in range(2):
        messages.append(
            _assistant(
                f"Running command {i}.",
                tool_name="Bash",
                tool_input={"command": f"echo test_{i}"},
                tool_output=f"test_{i}",
                offset=offset,
            )
        )
        offset += 1
        messages.append(_human("great, that works", offset=offset))
        offset += 1

    return messages


# ---------------------------------------------------------------------------
# Test class: overall approval rate (FR-010)
# ---------------------------------------------------------------------------


class TestOverallApprovalRate:
    """FR-010: Detect user approval vs rejection of tool calls."""

    def test_overall_rate_80_percent(self, ten_tool_calls_eight_approved):
        """8/10 approved => 0.8 overall rate."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        assert result["overall_approval_rate"] == pytest.approx(0.8, abs=0.01)

    def test_total_tool_calls_count(self, ten_tool_calls_eight_approved):
        """Should count exactly 10 tool calls."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        assert result["total_tool_calls"] == 10

    def test_approved_count(self, ten_tool_calls_eight_approved):
        """Should count 8 approved."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        assert result["approved_count"] == 8

    def test_rejected_count(self, ten_tool_calls_eight_approved):
        """Should count 2 rejected."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        assert result["rejected_count"] == 2


# ---------------------------------------------------------------------------
# Test class: per-tool breakdown (FR-011)
# ---------------------------------------------------------------------------


class TestPerToolBreakdown:
    """FR-011: Compute per-tool approval rates across sessions."""

    def test_read_100_percent_approval(self, ten_tool_calls_eight_approved):
        """Read: 3/3 approved => 100% rate."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        read_stats = result["per_tool"]["Read"]
        assert read_stats["approved"] == 3
        assert read_stats["rejected"] == 0
        assert read_stats["total"] == 3
        assert read_stats["approval_rate"] == pytest.approx(1.0)

    def test_edit_60_percent_approval(self, ten_tool_calls_eight_approved):
        """Edit: 3/5 approved => 60% rate."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        edit_stats = result["per_tool"]["Edit"]
        assert edit_stats["approved"] == 3
        assert edit_stats["rejected"] == 2
        assert edit_stats["total"] == 5
        assert edit_stats["approval_rate"] == pytest.approx(0.6, abs=0.01)

    def test_bash_100_percent_approval(self, ten_tool_calls_eight_approved):
        """Bash: 2/2 approved => 100% rate."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        bash_stats = result["per_tool"]["Bash"]
        assert bash_stats["approved"] == 2
        assert bash_stats["rejected"] == 0
        assert bash_stats["total"] == 2
        assert bash_stats["approval_rate"] == pytest.approx(1.0)

    def test_per_tool_keys_present(self, ten_tool_calls_eight_approved):
        """All three tool names should be in per_tool dict."""
        result = detect_approvals(ten_tool_calls_eight_approved)
        assert "Read" in result["per_tool"]
        assert "Edit" in result["per_tool"]
        assert "Bash" in result["per_tool"]


# ---------------------------------------------------------------------------
# Test class: details list
# ---------------------------------------------------------------------------


class TestApprovalDetails:
    """Each tool call should have a detail entry with approval status."""

    def test_details_count_matches_tool_calls(
        self, ten_tool_calls_eight_approved
    ):
        result = detect_approvals(ten_tool_calls_eight_approved)
        assert len(result["details"]) == 10

    def test_details_contain_required_fields(
        self, ten_tool_calls_eight_approved
    ):
        result = detect_approvals(ten_tool_calls_eight_approved)
        for detail in result["details"]:
            assert "tool_name" in detail
            assert "timestamp" in detail
            assert "approved" in detail
            assert "user_response" in detail

    def test_rejected_details_marked_false(
        self, ten_tool_calls_eight_approved
    ):
        result = detect_approvals(ten_tool_calls_eight_approved)
        rejected = [d for d in result["details"] if not d["approved"]]
        assert len(rejected) == 2
        for d in rejected:
            assert d["tool_name"] == "Edit"


# ---------------------------------------------------------------------------
# Test class: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for approval detection."""

    def test_no_tool_calls(self):
        """Conversation with no tool calls => zero counts, 0.0 rate."""
        messages = [
            _human("hello", offset=0),
            _assistant("Hi there! How can I help?", offset=1),
            _human("just chatting", offset=2),
        ]
        result = detect_approvals(messages)
        assert result["total_tool_calls"] == 0
        assert result["approved_count"] == 0
        assert result["rejected_count"] == 0
        assert result["overall_approval_rate"] == 0.0
        assert result["per_tool"] == {}
        assert result["details"] == []

    def test_all_approved(self):
        """Every tool call approved => 100% rate."""
        messages = [
            _assistant(
                "Reading.",
                tool_name="Read",
                tool_output="content",
                offset=0,
            ),
            _human("yes good", offset=1),
            _assistant(
                "Editing.",
                tool_name="Edit",
                tool_output="done",
                offset=2,
            ),
            _human("perfect", offset=3),
        ]
        result = detect_approvals(messages)
        assert result["total_tool_calls"] == 2
        assert result["approved_count"] == 2
        assert result["rejected_count"] == 0
        assert result["overall_approval_rate"] == pytest.approx(1.0)

    def test_all_rejected(self):
        """Every tool call rejected => 0% rate."""
        messages = [
            _assistant(
                "Reading.",
                tool_name="Read",
                tool_output="content",
                offset=0,
            ),
            _human("no, that's the wrong file", offset=1),
            _assistant(
                "Editing.",
                tool_name="Edit",
                tool_output="done",
                offset=2,
            ),
            _human("no, revert that immediately", offset=3),
        ]
        result = detect_approvals(messages)
        assert result["total_tool_calls"] == 2
        assert result["approved_count"] == 0
        assert result["rejected_count"] == 2
        assert result["overall_approval_rate"] == pytest.approx(0.0)

    def test_empty_messages(self):
        """Empty input => zero everything."""
        result = detect_approvals([])
        assert result["total_tool_calls"] == 0
        assert result["overall_approval_rate"] == 0.0
        assert result["per_tool"] == {}
        assert result["details"] == []

    def test_tool_call_with_no_following_human_message(self):
        """A tool call at the end with no user response should not crash."""
        messages = [
            _assistant(
                "Reading.",
                tool_name="Read",
                tool_output="content",
                offset=0,
            ),
        ]
        result = detect_approvals(messages)
        # The tool call has no user response so it should not count
        # as either approved or rejected, or implementation may choose
        # to skip it. Either way, no crash.
        assert result["total_tool_calls"] >= 0
