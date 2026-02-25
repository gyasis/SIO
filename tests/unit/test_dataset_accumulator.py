"""T029b [US3] Unit tests for sio.datasets.accumulator — incremental dataset growth.

Tests cover:
- accumulate(errors, patterns, db_conn) -> dict
    Feeds new errors into existing pattern datasets (appending) and creates
    fresh datasets for patterns that have no dataset yet.  Returns a summary
    dict with `updated_count` and `created_count`.

These tests are intentionally RED until the implementation is written.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sio.datasets.accumulator import accumulate

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


_NOW = "2026-02-25T10:00:00Z"


def _insert_pattern(
    conn: sqlite3.Connection,
    *,
    pattern_id: str = "p-accum-001",
    tool_name: str = "Read",
    description: str = "accumulator test pattern",
) -> int:
    """Insert a minimal patterns row and return its rowid."""
    cursor = conn.execute(
        """
        INSERT INTO patterns
            (pattern_id, description, tool_name, error_count, session_count,
             first_seen, last_seen, rank_score, created_at, updated_at)
        VALUES (?, ?, ?, 1, 1, ?, ?, 0.5, ?, ?)
        """,
        (pattern_id, description, tool_name, _NOW, _NOW, _NOW, _NOW),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_dataset(
    conn: sqlite3.Connection,
    pattern_row_id: int,
    *,
    file_path: str,
    positive_count: int = 3,
    negative_count: int = 3,
) -> int:
    """Insert a minimal datasets row and return its rowid."""
    cursor = conn.execute(
        """
        INSERT INTO datasets
            (pattern_id, file_path, positive_count, negative_count,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (pattern_row_id, file_path, positive_count, negative_count, _NOW, _NOW),
    )
    conn.commit()
    return cursor.lastrowid


def _write_initial_dataset_file(path: Path, examples: list[dict] | None = None) -> None:
    """Write a minimal JSON dataset file at *path*."""
    payload = {
        "examples": examples or [],
        "metadata": {
            "pattern_id": "p-accum-001",
            "positive_count": 0,
            "negative_count": 0,
            "created_at": _NOW,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_error(
    *,
    id: int,
    session_id: str = "sess-001",
    tool_name: str = "Read",
    error_text: str = "FileNotFoundError: /tmp/x.py",
    error_type: str = "tool_failure",
    timestamp: str = _NOW,
) -> dict:
    """Build a minimal error record dict matching the error_records schema."""
    return {
        "id": id,
        "session_id": session_id,
        "timestamp": timestamp,
        "source_type": "specstory",
        "source_file": "2026-02-25_10-00-00Z-test.md",
        "tool_name": tool_name,
        "error_text": error_text,
        "user_message": "Do something useful.",
        "context_before": None,
        "context_after": None,
        "error_type": error_type,
        "mined_at": _NOW,
    }


def _make_pattern(
    *,
    id: int = 1,
    pattern_id: str = "p-accum-001",
    tool_name: str = "Read",
    description: str = "accumulator test pattern",
) -> dict:
    """Build a minimal pattern dict matching the patterns schema."""
    return {
        "id": id,
        "pattern_id": pattern_id,
        "tool_name": tool_name,
        "description": description,
        "error_count": 1,
        "session_count": 1,
        "first_seen": _NOW,
        "last_seen": _NOW,
        "rank_score": 0.5,
    }


# ---------------------------------------------------------------------------
# T029b-1: accumulate feeds new errors into an existing dataset
# ---------------------------------------------------------------------------


class TestAccumulateFeedsIntoExistingDatasets:
    """When a pattern already has a dataset, new errors are appended."""

    def test_accumulate_feeds_into_existing_datasets(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-existing-001")

        # Pre-create a dataset file on disk and a matching DB row.
        ds_file = tmp_path / "p_existing_001.json"
        initial_examples = [
            {"id": 1, "label": 1, "error_text": "", "tool_name": "Read"},
            {"id": 2, "label": 0, "error_text": "OldError", "tool_name": "Read"},
            {"id": 3, "label": 1, "error_text": "", "tool_name": "Read"},
            {"id": 4, "label": 0, "error_text": "AnotherOldError", "tool_name": "Read"},
            {"id": 5, "label": 1, "error_text": "", "tool_name": "Read"},
        ]
        _write_initial_dataset_file(ds_file, examples=initial_examples)
        _insert_dataset(
            v2_db, pattern_row_id,
            file_path=str(ds_file),
            positive_count=3,
            negative_count=2,
        )

        # New errors for the same pattern.
        new_errors = [
            _make_error(id=10, session_id="new-sess-1", error_text="NewError 1"),
            _make_error(id=11, session_id="new-sess-2", error_text=""),
        ]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-existing-001")

        accumulate(new_errors, [pattern], v2_db, dataset_dir=tmp_path)

        payload = json.loads(ds_file.read_text())
        assert len(payload["examples"]) > len(initial_examples), (
            "New errors must be appended to the existing dataset file"
        )

    def test_existing_examples_preserved(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Accumulation must not remove or overwrite pre-existing examples."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-preserve-001")

        ds_file = tmp_path / "p_preserve_001.json"
        initial_examples = [
            {"id": i, "label": i % 2, "error_text": f"err {i}", "tool_name": "Read"}
            for i in range(6)
        ]
        _write_initial_dataset_file(ds_file, examples=initial_examples)
        _insert_dataset(
            v2_db, pattern_row_id,
            file_path=str(ds_file),
            positive_count=3,
            negative_count=3,
        )

        new_errors = [
            _make_error(id=100, session_id="new-100", error_text="BrandNewError"),
        ]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-preserve-001")

        accumulate(new_errors, [pattern], v2_db, dataset_dir=tmp_path)

        payload = json.loads(ds_file.read_text())
        existing_ids = {ex["id"] for ex in payload["examples"]}
        for original in initial_examples:
            assert original["id"] in existing_ids, (
                f"Original example id={original['id']} was lost during accumulation"
            )

    def test_db_counts_updated_after_accumulation(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """After accumulation, datasets row counts must reflect the new total."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-dbcounts-001")

        ds_file = tmp_path / "p_dbcounts_001.json"
        _write_initial_dataset_file(
            ds_file,
            examples=[
                {"id": 1, "label": 1, "error_text": "", "tool_name": "Read"},
                {"id": 2, "label": 0, "error_text": "Err", "tool_name": "Read"},
                {"id": 3, "label": 1, "error_text": "", "tool_name": "Read"},
                {"id": 4, "label": 0, "error_text": "Err2", "tool_name": "Read"},
                {"id": 5, "label": 1, "error_text": "", "tool_name": "Read"},
            ],
        )
        ds_id = _insert_dataset(
            v2_db, pattern_row_id,
            file_path=str(ds_file),
            positive_count=3,
            negative_count=2,
        )

        new_errors = [
            _make_error(id=20, session_id="new-20", error_text=""),    # positive
            _make_error(id=21, session_id="new-21", error_text="Err"), # negative
        ]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-dbcounts-001")

        accumulate(new_errors, [pattern], v2_db, dataset_dir=tmp_path)

        row = v2_db.execute(
            "SELECT positive_count + negative_count FROM datasets WHERE id = ?",
            (ds_id,),
        ).fetchone()
        assert row is not None
        assert row[0] >= 7, (
            f"datasets counts should be at least 7 after adding 2 new errors; got {row[0]}"
        )


# ---------------------------------------------------------------------------
# T029b-2: accumulate creates a new dataset for a pattern that has none
# ---------------------------------------------------------------------------


class TestAccumulateCreatesNewDatasets:
    """When a pattern has no existing dataset, accumulate must create one."""

    def test_accumulate_creates_new_datasets(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-new-ds-001")
        # No dataset row or file pre-created.

        errors = [_make_error(id=i, session_id=f"s-{i}") for i in range(6)]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-new-ds-001")

        accumulate(errors, [pattern], v2_db, dataset_dir=tmp_path)

        row = v2_db.execute(
            "SELECT id FROM datasets WHERE pattern_id = ?",
            (pattern_row_id,),
        ).fetchone()
        assert row is not None, (
            "A new datasets row must be created for a pattern that had none"
        )

    def test_new_dataset_file_written_to_disk(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-new-file-001")

        errors = [_make_error(id=i, session_id=f"ns-{i}") for i in range(6)]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-new-file-001")

        accumulate(errors, [pattern], v2_db, dataset_dir=tmp_path)

        # Retrieve the file_path from the newly-created dataset row.
        row = v2_db.execute(
            "SELECT file_path FROM datasets WHERE pattern_id = ?",
            (pattern_row_id,),
        ).fetchone()
        assert row is not None
        assert Path(row[0]).exists(), (
            f"Dataset file must exist on disk at {row[0]!r}"
        )

    def test_new_dataset_file_has_valid_json(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-new-json-001")

        errors = [_make_error(id=i, session_id=f"js-{i}") for i in range(6)]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-new-json-001")

        accumulate(errors, [pattern], v2_db, dataset_dir=tmp_path)

        row = v2_db.execute(
            "SELECT file_path FROM datasets WHERE pattern_id = ?",
            (pattern_row_id,),
        ).fetchone()
        assert row is not None

        content = Path(row[0]).read_text(encoding="utf-8")
        payload = json.loads(content)  # Must not raise.
        assert "examples" in payload
        assert "metadata" in payload

    def test_new_dataset_not_created_below_threshold(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """If a new pattern has fewer errors than the threshold, no dataset is created."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-below-thresh-001")

        # Only 2 errors — below the default threshold of 5.
        errors = [_make_error(id=i, session_id=f"bt-{i}") for i in range(2)]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-below-thresh-001")

        accumulate(errors, [pattern], v2_db, dataset_dir=tmp_path)

        row = v2_db.execute(
            "SELECT id FROM datasets WHERE pattern_id = ?",
            (pattern_row_id,),
        ).fetchone()
        assert row is None, (
            "No dataset should be created when error count is below the threshold"
        )


# ---------------------------------------------------------------------------
# T029b-3: accumulate returns a summary dict
# ---------------------------------------------------------------------------


class TestAccumulateReturnsSummary:
    """accumulate must return a dict with updated_count and created_count."""

    _REQUIRED_KEYS = frozenset({"updated_count", "created_count"})

    def test_accumulate_returns_summary(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-summary-001")
        errors = [_make_error(id=i, session_id=f"su-{i}") for i in range(6)]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-summary-001")

        result = accumulate(errors, [pattern], v2_db, dataset_dir=tmp_path)

        assert isinstance(result, dict), (
            f"accumulate must return a dict; got {type(result).__name__!r}"
        )
        missing = self._REQUIRED_KEYS - set(result.keys())
        assert not missing, f"Summary missing required keys: {sorted(missing)}"

    def test_summary_created_count_reflects_new_datasets(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """created_count must equal the number of newly-created datasets."""
        # Two brand-new patterns — each should produce a new dataset.
        p1_id = _insert_pattern(v2_db, pattern_id="p-sum-new-001", tool_name="Read")
        p2_id = _insert_pattern(v2_db, pattern_id="p-sum-new-002", tool_name="Bash")

        errors_p1 = [
            _make_error(id=i, session_id=f"p1-{i}", tool_name="Read")
            for i in range(6)
        ]
        errors_p2 = [
            _make_error(id=100 + i, session_id=f"p2-{i}", tool_name="Bash",
                        error_text=f"BashErr {i}")
            for i in range(6)
        ]

        patterns = [
            _make_pattern(id=p1_id, pattern_id="p-sum-new-001", tool_name="Read"),
            _make_pattern(id=p2_id, pattern_id="p-sum-new-002", tool_name="Bash"),
        ]

        result = accumulate(errors_p1 + errors_p2, patterns, v2_db, dataset_dir=tmp_path)

        assert result["created_count"] == 2, (
            f"Expected created_count=2 for two new patterns; got {result['created_count']}"
        )

    def test_summary_updated_count_reflects_existing_datasets(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """updated_count must equal the number of existing datasets that received new errors."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-sum-update-001")

        # Pre-create a dataset.
        ds_file = tmp_path / "p_sum_update_001.json"
        _write_initial_dataset_file(
            ds_file,
            examples=[
                {"id": i, "label": i % 2, "error_text": f"e{i}", "tool_name": "Read"}
                for i in range(6)
            ],
        )
        _insert_dataset(
            v2_db, pattern_row_id,
            file_path=str(ds_file),
            positive_count=3,
            negative_count=3,
        )

        new_errors = [
            _make_error(id=50, session_id="upd-50", error_text="UpdatedError"),
        ]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-sum-update-001")

        result = accumulate(new_errors, [pattern], v2_db, dataset_dir=tmp_path)

        assert result["updated_count"] == 1, (
            f"Expected updated_count=1 for the one existing dataset; "
            f"got {result['updated_count']}"
        )
        assert result["created_count"] == 0, (
            f"Expected created_count=0; got {result['created_count']}"
        )

    def test_summary_counts_are_ints(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-sum-types-001")
        errors = [_make_error(id=i, session_id=f"ty-{i}") for i in range(6)]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-sum-types-001")

        result = accumulate(errors, [pattern], v2_db, dataset_dir=tmp_path)

        assert isinstance(result["updated_count"], int), (
            "updated_count must be an int"
        )
        assert isinstance(result["created_count"], int), (
            "created_count must be an int"
        )

    def test_summary_zero_when_all_below_threshold(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Both counts must be 0 when no patterns have enough errors."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-sum-zero-001")
        # Only 1 error — well below threshold.
        errors = [_make_error(id=1, session_id="zero-1")]
        pattern = _make_pattern(id=pattern_row_id, pattern_id="p-sum-zero-001")

        result = accumulate(errors, [pattern], v2_db, dataset_dir=tmp_path)

        assert result["created_count"] == 0, (
            "created_count must be 0 when threshold is not met"
        )
        assert result["updated_count"] == 0, (
            "updated_count must be 0 when no existing dataset was touched"
        )

    def test_summary_mixed_new_and_existing(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Summary must correctly split counts when one pattern is new and one exists."""
        # Existing pattern with a pre-created dataset.
        existing_p_id = _insert_pattern(v2_db, pattern_id="p-sum-mix-existing-001")
        ds_file = tmp_path / "p_sum_mix_existing_001.json"
        _write_initial_dataset_file(
            ds_file,
            examples=[
                {"id": i, "label": i % 2, "error_text": f"e{i}", "tool_name": "Read"}
                for i in range(6)
            ],
        )
        _insert_dataset(
            v2_db, existing_p_id,
            file_path=str(ds_file),
            positive_count=3,
            negative_count=3,
        )

        # New pattern — no dataset.
        new_p_id = _insert_pattern(v2_db, pattern_id="p-sum-mix-new-001")

        errors_existing = [
            _make_error(id=200 + i, session_id=f"mix-ex-{i}", tool_name="Read")
            for i in range(2)
        ]
        errors_new = [
            _make_error(id=300 + i, session_id=f"mix-new-{i}", tool_name="Read")
            for i in range(6)
        ]

        patterns = [
            _make_pattern(id=existing_p_id, pattern_id="p-sum-mix-existing-001"),
            _make_pattern(id=new_p_id, pattern_id="p-sum-mix-new-001"),
        ]

        result = accumulate(
            errors_existing + errors_new, patterns, v2_db, dataset_dir=tmp_path
        )

        assert result["updated_count"] == 1, (
            f"Expected updated_count=1; got {result['updated_count']}"
        )
        assert result["created_count"] == 1, (
            f"Expected created_count=1; got {result['created_count']}"
        )
