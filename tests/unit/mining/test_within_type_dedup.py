"""Unit tests for T105: within-type dedup only — cross-type rows are preserved.

FR-020: dedup must only operate within the same error_type bucket.
Rows of different error_types sharing the same (session_id, user_message) must
ALL be kept.
"""

from __future__ import annotations

from sio.mining.pipeline import _dedup_by_error_type_priority


class TestWithinTypeDedup:
    """_dedup_by_error_type_priority removes within-type duplicates but keeps cross-type rows."""

    def test_same_type_same_key_deduped(self):
        """Two records with the SAME error_type and same (sid, umsg) — only one survives.

        Within-type dedup: identical (session_id, user_message, error_type)
        tuples collapse to a single representative row.
        """
        records = [
            {
                "session_id": "s1",
                "user_message": "msg",
                "error_type": "tool_failure",
                "error_text": "err A",
            },
            {
                "session_id": "s1",
                "user_message": "msg",
                "error_type": "tool_failure",
                "error_text": "err B (dup same type)",
            },
        ]
        result = _dedup_by_error_type_priority(records)
        # Both have the same (sid, umsg, type) — only one should survive
        assert len(result) == 1
        assert result[0]["error_type"] == "tool_failure"

    def test_cross_type_rows_both_preserved(self):
        """tool_failure and user_correction sharing the same (sid, umsg) must BOTH survive."""
        records = [
            {
                "session_id": "s1",
                "user_message": "same message",
                "error_type": "tool_failure",
                "error_text": "Tool failed",
            },
            {
                "session_id": "s1",
                "user_message": "same message",
                "error_type": "user_correction",
                "error_text": "User corrected",
            },
        ]
        result = _dedup_by_error_type_priority(records)
        # Both types must survive — dedup is within-type only
        result_types = {r["error_type"] for r in result}
        assert "tool_failure" in result_types, "tool_failure row must be preserved"
        assert "user_correction" in result_types, "user_correction row must be preserved"

    def test_ungrouped_records_always_kept(self):
        """Records missing session_id or user_message are always preserved."""
        records = [
            {
                "session_id": "",
                "user_message": "msg",
                "error_type": "tool_failure",
                "error_text": "err",
            },
            {
                "session_id": "s1",
                "user_message": "",
                "error_type": "tool_failure",
                "error_text": "err",
            },
            {
                "session_id": "s1",
                "user_message": "msg",
                "error_type": "tool_failure",
                "error_text": "err",
            },
        ]
        result = _dedup_by_error_type_priority(records)
        # First two are ungrouped and must pass through; third is the winner of its group
        assert len(result) == 3
