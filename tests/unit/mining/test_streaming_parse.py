"""T080 [P] [US5] Failing unit tests for streaming JSONL parser.

Tests assert that ``jsonl_parser.iter_events(path)`` streams line-by-line
without loading the entire file into memory (FR-009, R-6, Principle X).

These tests are EXPECTED RED until T081 implements ``iter_events`` in
``src/sio/mining/jsonl_parser.py``.

Run to confirm RED:
    uv run pytest tests/unit/mining/test_streaming_parse.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def jsonl_fixture(tmp_path: Path) -> Path:
    """Create a 10 000-line JSONL fixture file."""
    fixture_path = tmp_path / "session_10k.jsonl"
    lines = []
    for i in range(10_000):
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"message {i}",
                    },
                    "timestamp": f"2026-01-01T00:{i // 3600:02d}:{i % 60:02d}Z",
                }
            )
        )
    fixture_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fixture_path


@pytest.fixture
def tiny_jsonl(tmp_path: Path) -> Path:
    """Create a 5-line JSONL fixture with one malformed line."""
    fixture_path = tmp_path / "tiny.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "a"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "b"}}),
        "THIS IS NOT JSON }{",
        json.dumps({"type": "user", "message": {"role": "user", "content": "c"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "d"}}),
    ]
    fixture_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fixture_path


# ---------------------------------------------------------------------------
# T080-1: iter_events exists and is a generator (streams, not list)
# ---------------------------------------------------------------------------


def test_iter_events_is_generator(jsonl_fixture: Path) -> None:
    """``iter_events(path)`` must return a generator (lazy iterator), not a list."""
    from sio.mining.jsonl_parser import iter_events  # type: ignore[import]

    result = iter_events(jsonl_fixture)

    # A generator is NOT a list — it must support __next__
    import types
    assert isinstance(result, types.GeneratorType), (
        "iter_events() must return a generator (lazy streaming), not a list or other iterable"
    )


# ---------------------------------------------------------------------------
# T080-2: iter_events NEVER calls Path.read_text() or open().read()
# ---------------------------------------------------------------------------


def test_iter_events_does_not_call_read_text(jsonl_fixture: Path) -> None:
    """``iter_events`` must NOT call ``Path.read_text()`` (full-file load forbidden)."""
    from sio.mining.jsonl_parser import iter_events  # type: ignore[import]

    with patch.object(Path, "read_text", side_effect=AssertionError(
        "iter_events must NOT call Path.read_text() — use streaming open(path, 'rb') instead"
    )):
        # Consume the generator — if read_text is called, AssertionError propagates
        list(iter_events(jsonl_fixture))


# ---------------------------------------------------------------------------
# T080-3: iter_events(path, start_offset=N) resumes from byte offset
# ---------------------------------------------------------------------------


def test_iter_events_respects_start_offset(jsonl_fixture: Path) -> None:
    """``iter_events(path, start_offset=N)`` must skip the first N bytes."""
    from sio.mining.jsonl_parser import iter_events  # type: ignore[import]

    # Consume all events from offset 0 to get the total count
    all_events = list(iter_events(jsonl_fixture, start_offset=0))

    # Consume events starting midway — should be strictly fewer
    mid_offset = jsonl_fixture.stat().st_size // 2
    mid_events = list(iter_events(jsonl_fixture, start_offset=mid_offset))

    assert len(mid_events) < len(all_events), (
        f"iter_events with start_offset={mid_offset} should yield fewer events "
        f"than start_offset=0 ({len(mid_events)} vs {len(all_events)})"
    )
    assert len(mid_events) > 0, (
        "iter_events with start_offset at midpoint should still yield some events"
    )


# ---------------------------------------------------------------------------
# T080-4: iter_events returns (event_dict, end_offset) tuples
# ---------------------------------------------------------------------------


def test_iter_events_yields_tuples_with_offset(tiny_jsonl: Path) -> None:
    """``iter_events`` must yield ``(event_dict, byte_offset_after_this_line)`` tuples."""
    from sio.mining.jsonl_parser import iter_events  # type: ignore[import]

    results = list(iter_events(tiny_jsonl))

    assert len(results) > 0, "iter_events should yield at least one result"

    for item in results:
        assert isinstance(item, tuple), (
            f"iter_events must yield tuples, got {type(item)}"
        )
        assert len(item) == 2, (
            f"Each yielded tuple must have exactly 2 elements (event_dict, offset), "
            f"got {len(item)}"
        )
        event_dict, offset = item
        assert isinstance(event_dict, dict), (
            f"First element of tuple must be a dict, got {type(event_dict)}"
        )
        assert isinstance(offset, int), (
            f"Second element of tuple must be an int (byte offset), got {type(offset)}"
        )
        assert offset > 0, (
            f"Byte offset must be > 0 after any line, got {offset}"
        )


# ---------------------------------------------------------------------------
# T080-5: Malformed JSON lines are skipped gracefully
# ---------------------------------------------------------------------------


def test_iter_events_skips_malformed_lines(tiny_jsonl: Path) -> None:
    """Malformed JSON lines must be silently skipped without crashing."""
    from sio.mining.jsonl_parser import iter_events  # type: ignore[import]

    # tiny_jsonl has 5 lines, 1 malformed → expect 4 valid event dicts
    results = list(iter_events(tiny_jsonl))

    assert len(results) == 4, (
        f"Expected 4 valid events (1 malformed line skipped), got {len(results)}"
    )


# ---------------------------------------------------------------------------
# T080-6: last_offset matches file size after complete read
# ---------------------------------------------------------------------------


def test_iter_events_last_offset_matches_file_size(tiny_jsonl: Path) -> None:
    """The last offset returned must equal ``os.path.getsize(path)``."""
    from sio.mining.jsonl_parser import iter_events  # type: ignore[import]

    results = list(iter_events(tiny_jsonl))
    assert len(results) > 0

    _, last_offset = results[-1]
    file_size = os.path.getsize(tiny_jsonl)

    assert last_offset == file_size, (
        f"Last offset {last_offset} must equal file size {file_size} after "
        f"complete read of {tiny_jsonl}"
    )
