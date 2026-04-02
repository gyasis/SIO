"""TDD tests for enhanced JSONL parser fields (T005).

Tests that parse_jsonl extracts the new metadata fields added by the
competitive enhancement spec: input_tokens, output_tokens,
cache_creation_input_tokens, cache_read_input_tokens, costUsd,
stopReason, isSidechain, and model.

Follows the pattern established in test_jsonl_parser.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sio.mining.jsonl_parser import parse_jsonl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS_BASE = "2026-04-01T10:00:0{}Z"


def _write_lines(path: Path, lines: list[str]) -> None:
    """Write *lines* to *path*, one per physical line."""
    path.write_text("\n".join(lines), encoding="utf-8")


def _assistant_with_usage(
    content: str,
    ts: str,
    *,
    input_tokens: int = 1500,
    output_tokens: int = 350,
    cache_creation_input_tokens: int = 200,
    cache_read_input_tokens: int = 800,
    cost_usd: float = 0.0042,
    stop_reason: str = "end_turn",
    is_sidechain: bool = False,
    model: str = "claude-opus-4-6-20250415",
) -> dict:
    """Build a real Claude Code assistant wire-format line with usage metadata."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": content,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
            },
        },
        "costUsd": cost_usd,
        "stopReason": stop_reason,
        "isSidechain": is_sidechain,
        "model": model,
        "timestamp": ts,
    }


def _assistant_with_tool_and_usage(
    content_text: str,
    tool_name: str,
    tool_input: dict,
    ts: str,
    *,
    input_tokens: int = 2000,
    output_tokens: int = 500,
    cache_creation_input_tokens: int = 100,
    cache_read_input_tokens: int = 1200,
    cost_usd: float = 0.0065,
    stop_reason: str = "tool_use",
    is_sidechain: bool = False,
    model: str = "claude-opus-4-6-20250415",
) -> dict:
    """Build a real assistant line with both text + tool_use blocks and usage."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": content_text},
                {
                    "type": "tool_use",
                    "id": f"toolu_{tool_name.lower()}_{ts[-3:-1]}",
                    "name": tool_name,
                    "input": tool_input,
                },
            ],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
            },
        },
        "costUsd": cost_usd,
        "stopReason": stop_reason,
        "isSidechain": is_sidechain,
        "model": model,
        "timestamp": ts,
    }


def _human(content: str, ts: str) -> dict:
    """Build a human message in real wire format (no usage)."""
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "timestamp": ts,
    }


def _legacy_human(content: str, ts: str) -> dict:
    """Build a human message in legacy test-fixture format."""
    return {
        "type": "human",
        "message": {"role": "user", "content": content},
        "timestamp": ts,
    }


def _legacy_tool_use(
    tool_name: str,
    tool_input: dict,
    tool_output: str | None,
    ts: str,
) -> dict:
    """Build a tool_use line in legacy format (no usage metadata)."""
    return {
        "type": "tool_use",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "timestamp": ts,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def realistic_session(tmp_path: Path) -> Path:
    """Write a realistic Claude Code JSONL session with usage metadata.

    Contains: human -> assistant with usage -> assistant with tool+usage -> human
    """
    wire_objects = [
        _human("Read the config file and check if it's valid.", _TS_BASE.format(0)),
        _assistant_with_tool_and_usage(
            "Let me read the configuration file.",
            "Read",
            {"file_path": "/home/user/project/config.yaml"},
            _TS_BASE.format(1),
            input_tokens=2500,
            output_tokens=420,
            cache_creation_input_tokens=300,
            cache_read_input_tokens=1100,
            cost_usd=0.0078,
            stop_reason="tool_use",
            model="claude-opus-4-6-20250415",
        ),
        _assistant_with_usage(
            "The config file looks valid. It has the correct structure.",
            _TS_BASE.format(3),
            input_tokens=3200,
            output_tokens=180,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=2800,
            cost_usd=0.0035,
            stop_reason="end_turn",
            model="claude-opus-4-6-20250415",
        ),
        _human("Great, thanks!", _TS_BASE.format(4)),
    ]
    path = tmp_path / "session_with_usage.jsonl"
    _write_lines(path, [json.dumps(obj) for obj in wire_objects])
    return path


@pytest.fixture
def sidechain_session(tmp_path: Path) -> Path:
    """Write a session that includes sidechain (sub-agent) messages."""
    wire_objects = [
        _human("Run the full test suite.", _TS_BASE.format(0)),
        _assistant_with_usage(
            "Running tests now.",
            _TS_BASE.format(1),
            is_sidechain=False,
            model="claude-opus-4-6-20250415",
        ),
        _assistant_with_usage(
            "Sub-agent analyzing test results.",
            _TS_BASE.format(2),
            is_sidechain=True,
            model="claude-opus-4-6-20250415",
            input_tokens=900,
            output_tokens=200,
            cost_usd=0.0015,
        ),
        _assistant_with_usage(
            "All tests passed.",
            _TS_BASE.format(3),
            is_sidechain=False,
            model="claude-opus-4-6-20250415",
        ),
    ]
    path = tmp_path / "sidechain_session.jsonl"
    _write_lines(path, [json.dumps(obj) for obj in wire_objects])
    return path


# ---------------------------------------------------------------------------
# Tests: Token usage extraction
# ---------------------------------------------------------------------------


class TestTokenUsageExtraction:
    """parse_jsonl extracts token usage from the usage object."""

    def test_input_tokens_extracted(self, realistic_session):
        records = parse_jsonl(realistic_session)
        # Find the first assistant record with usage data
        assistant_recs = [r for r in records if r.get("input_tokens") is not None]
        assert len(assistant_recs) > 0, "No records with input_tokens found"
        assert isinstance(assistant_recs[0]["input_tokens"], int)

    def test_output_tokens_extracted(self, realistic_session):
        records = parse_jsonl(realistic_session)
        assistant_recs = [r for r in records if r.get("output_tokens") is not None]
        assert len(assistant_recs) > 0, "No records with output_tokens found"
        assert isinstance(assistant_recs[0]["output_tokens"], int)

    def test_cache_creation_tokens_extracted(self, realistic_session):
        records = parse_jsonl(realistic_session)
        assistant_recs = [
            r for r in records
            if r.get("cache_creation_input_tokens") is not None
        ]
        assert len(assistant_recs) > 0, "No records with cache_creation_input_tokens"

    def test_cache_read_tokens_extracted(self, realistic_session):
        records = parse_jsonl(realistic_session)
        assistant_recs = [
            r for r in records
            if r.get("cache_read_input_tokens") is not None
        ]
        assert len(assistant_recs) > 0, "No records with cache_read_input_tokens"

    def test_token_values_match_source(self, realistic_session):
        records = parse_jsonl(realistic_session)
        # The second message is the tool-use assistant with input_tokens=2500
        assistant_recs = [r for r in records if r.get("input_tokens") is not None]
        # At least one should have 2500 input tokens (the tool-use assistant)
        token_values = {r["input_tokens"] for r in assistant_recs}
        assert 2500 in token_values or 3200 in token_values, (
            f"Expected 2500 or 3200 in token values, got {token_values}"
        )


# ---------------------------------------------------------------------------
# Tests: Cost extraction
# ---------------------------------------------------------------------------


class TestCostExtraction:
    """parse_jsonl extracts costUsd from assistant messages."""

    def test_cost_usd_extracted(self, realistic_session):
        records = parse_jsonl(realistic_session)
        cost_recs = [r for r in records if r.get("cost_usd") is not None]
        assert len(cost_recs) > 0, "No records with cost_usd found"

    def test_cost_usd_is_float(self, realistic_session):
        records = parse_jsonl(realistic_session)
        cost_recs = [r for r in records if r.get("cost_usd") is not None]
        for rec in cost_recs:
            assert isinstance(rec["cost_usd"], (int, float))

    def test_cost_values_match_source(self, realistic_session):
        records = parse_jsonl(realistic_session)
        cost_recs = [r for r in records if r.get("cost_usd") is not None]
        cost_values = {round(r["cost_usd"], 4) for r in cost_recs}
        assert 0.0078 in cost_values or 0.0035 in cost_values, (
            f"Expected 0.0078 or 0.0035 in cost values, got {cost_values}"
        )


# ---------------------------------------------------------------------------
# Tests: Stop reason extraction
# ---------------------------------------------------------------------------


class TestStopReasonExtraction:
    """parse_jsonl extracts stopReason from assistant messages."""

    def test_stop_reason_extracted(self, realistic_session):
        records = parse_jsonl(realistic_session)
        sr_recs = [r for r in records if r.get("stop_reason") is not None]
        assert len(sr_recs) > 0, "No records with stop_reason found"

    def test_stop_reason_values(self, realistic_session):
        records = parse_jsonl(realistic_session)
        sr_recs = [r for r in records if r.get("stop_reason") is not None]
        sr_values = {r["stop_reason"] for r in sr_recs}
        # Should have at least tool_use or end_turn
        assert sr_values & {"tool_use", "end_turn"}, (
            f"Expected 'tool_use' or 'end_turn' in stop reasons, got {sr_values}"
        )


# ---------------------------------------------------------------------------
# Tests: Sidechain flag extraction
# ---------------------------------------------------------------------------


class TestSidechainExtraction:
    """parse_jsonl extracts isSidechain flag."""

    def test_is_sidechain_extracted(self, sidechain_session):
        records = parse_jsonl(sidechain_session)
        sc_recs = [r for r in records if r.get("is_sidechain") is not None]
        assert len(sc_recs) > 0, "No records with is_sidechain found"

    def test_sidechain_true_detected(self, sidechain_session):
        records = parse_jsonl(sidechain_session)
        sc_recs = [r for r in records if r.get("is_sidechain") is True]
        assert len(sc_recs) > 0, "No sidechain=True records found"

    def test_sidechain_false_detected(self, sidechain_session):
        records = parse_jsonl(sidechain_session)
        sc_recs = [r for r in records if r.get("is_sidechain") is False]
        assert len(sc_recs) > 0, "No sidechain=False records found"


# ---------------------------------------------------------------------------
# Tests: Model extraction
# ---------------------------------------------------------------------------


class TestModelExtraction:
    """parse_jsonl extracts the model identifier."""

    def test_model_extracted(self, realistic_session):
        records = parse_jsonl(realistic_session)
        model_recs = [r for r in records if r.get("model") is not None]
        assert len(model_recs) > 0, "No records with model found"

    def test_model_value_is_string(self, realistic_session):
        records = parse_jsonl(realistic_session)
        model_recs = [r for r in records if r.get("model") is not None]
        for rec in model_recs:
            assert isinstance(rec["model"], str)

    def test_model_value_matches_source(self, realistic_session):
        records = parse_jsonl(realistic_session)
        model_recs = [r for r in records if r.get("model") is not None]
        models = {r["model"] for r in model_recs}
        assert "claude-opus-4-6-20250415" in models


# ---------------------------------------------------------------------------
# Tests: User messages have None for metadata fields
# ---------------------------------------------------------------------------


class TestUserMessagesHaveNoneMetadata:
    """Human/user messages should have None for all usage metadata fields."""

    META_FIELDS = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "cost_usd",
        "stop_reason",
        "is_sidechain",
        "model",
    )

    def test_user_messages_have_none_for_all_meta_fields(self, realistic_session):
        records = parse_jsonl(realistic_session)
        user_recs = [r for r in records if r["role"] == "user"]
        assert len(user_recs) > 0, "No user records found"
        for rec in user_recs:
            for field in self.META_FIELDS:
                assert rec.get(field) is None, (
                    f"User message should have None for '{field}', "
                    f"got {rec.get(field)}"
                )


# ---------------------------------------------------------------------------
# Tests: Backward compatibility with messages missing usage
# ---------------------------------------------------------------------------


class TestBackwardCompatibilityNoUsage:
    """Messages without usage objects still parse correctly with None metadata."""

    def test_legacy_human_has_none_metadata(self, tmp_path):
        path = tmp_path / "legacy.jsonl"
        _write_lines(path, [json.dumps(_legacy_human("Hello", _TS_BASE.format(0)))])
        records = parse_jsonl(path)
        assert len(records) == 1
        assert records[0]["role"] == "user"
        assert records[0].get("input_tokens") is None
        assert records[0].get("cost_usd") is None

    def test_legacy_tool_use_has_none_metadata(self, tmp_path):
        path = tmp_path / "legacy_tool.jsonl"
        _write_lines(
            path,
            [
                json.dumps(
                    _legacy_tool_use("Read", {"file_path": "/tmp/f.py"}, "content", _TS_BASE.format(0))
                )
            ],
        )
        records = parse_jsonl(path)
        assert len(records) == 1
        assert records[0]["tool_name"] == "Read"
        # Legacy format does not carry usage — all meta fields should be None
        assert records[0].get("input_tokens") is None
        assert records[0].get("output_tokens") is None
        assert records[0].get("cost_usd") is None
        assert records[0].get("stop_reason") is None
        assert records[0].get("is_sidechain") is None
        assert records[0].get("model") is None

    def test_assistant_without_usage_object(self, tmp_path):
        """An assistant message in real format but without a usage object."""
        obj = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "No usage data here.",
            },
            "timestamp": _TS_BASE.format(0),
        }
        path = tmp_path / "no_usage.jsonl"
        _write_lines(path, [json.dumps(obj)])
        records = parse_jsonl(path)
        assert len(records) == 1
        assert records[0]["content"] == "No usage data here."
        assert records[0].get("input_tokens") is None
        assert records[0].get("cost_usd") is None

    def test_mixed_legacy_and_enhanced_messages(self, tmp_path):
        """A file mixing legacy (no usage) and enhanced (with usage) messages."""
        lines = [
            json.dumps(_legacy_human("Read the file", _TS_BASE.format(0))),
            json.dumps(
                _assistant_with_usage(
                    "Sure, reading now.",
                    _TS_BASE.format(1),
                    input_tokens=1000,
                    cost_usd=0.002,
                )
            ),
            json.dumps(
                _legacy_tool_use("Read", {"file_path": "/tmp/x.py"}, "contents", _TS_BASE.format(2))
            ),
        ]
        path = tmp_path / "mixed.jsonl"
        _write_lines(path, lines)
        records = parse_jsonl(path)
        assert len(records) >= 2
        # The legacy human should have None tokens
        human_recs = [r for r in records if r["role"] == "user"]
        for h in human_recs:
            assert h.get("input_tokens") is None
        # The enhanced assistant should have tokens
        enhanced = [r for r in records if r.get("input_tokens") == 1000]
        assert len(enhanced) > 0


# ---------------------------------------------------------------------------
# Tests: All returned dicts have the new keys
# ---------------------------------------------------------------------------


class TestAllRecordsHaveMetaKeys:
    """Every record returned by parse_jsonl includes the new metadata keys."""

    EXPECTED_KEYS = {
        "role",
        "content",
        "tool_name",
        "tool_input",
        "tool_output",
        "error",
        "timestamp",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "cost_usd",
        "stop_reason",
        "is_sidechain",
        "model",
    }

    def test_all_records_have_all_keys(self, realistic_session):
        records = parse_jsonl(realistic_session)
        for idx, rec in enumerate(records):
            missing = self.EXPECTED_KEYS - set(rec.keys())
            assert not missing, (
                f"Record {idx} missing keys: {sorted(missing)}. "
                f"Has: {sorted(rec.keys())}"
            )


# ---------------------------------------------------------------------------
# Tests: camelCase wire format support
# ---------------------------------------------------------------------------


class TestCamelCaseWireFormat:
    """costUsd, stopReason, isSidechain in camelCase are parsed correctly."""

    def test_camel_case_cost_usd(self, tmp_path):
        obj = {
            "type": "assistant",
            "message": {"role": "assistant", "content": "test"},
            "costUsd": 0.0123,
            "timestamp": _TS_BASE.format(0),
        }
        path = tmp_path / "camel.jsonl"
        _write_lines(path, [json.dumps(obj)])
        records = parse_jsonl(path)
        cost_recs = [r for r in records if r.get("cost_usd") is not None]
        assert len(cost_recs) > 0
        assert abs(cost_recs[0]["cost_usd"] - 0.0123) < 1e-6

    def test_camel_case_stop_reason(self, tmp_path):
        obj = {
            "type": "assistant",
            "message": {"role": "assistant", "content": "test"},
            "stopReason": "max_tokens",
            "timestamp": _TS_BASE.format(0),
        }
        path = tmp_path / "camel_sr.jsonl"
        _write_lines(path, [json.dumps(obj)])
        records = parse_jsonl(path)
        sr_recs = [r for r in records if r.get("stop_reason") is not None]
        assert len(sr_recs) > 0
        assert sr_recs[0]["stop_reason"] == "max_tokens"

    def test_camel_case_is_sidechain(self, tmp_path):
        obj = {
            "type": "assistant",
            "message": {"role": "assistant", "content": "test"},
            "isSidechain": True,
            "timestamp": _TS_BASE.format(0),
        }
        path = tmp_path / "camel_sc.jsonl"
        _write_lines(path, [json.dumps(obj)])
        records = parse_jsonl(path)
        sc_recs = [r for r in records if r.get("is_sidechain") is True]
        assert len(sc_recs) > 0
