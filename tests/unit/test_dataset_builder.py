"""T028 [US3] Unit tests for sio.datasets.builder — positive/negative example construction.

Tests cover:
- build_dataset(pattern, all_errors, db_conn) -> dict | None
    Finds successful calls (positive) and failed calls (negative) from all_errors
    for a given pattern's tool_name, writes a JSON dataset file, and returns
    metadata or None when the example count falls below the minimum threshold.
- collect_dataset(db_conn, since, error_type, sessions) -> dict
    On-demand collection from user-specified criteria.

These tests are intentionally RED until the implementation is written.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sio.datasets.builder import build_dataset, collect_dataset

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


_NOW = "2026-02-25T10:00:00Z"


def _insert_pattern(
    conn: sqlite3.Connection,
    *,
    pattern_id: str = "p-builder-001",
    tool_name: str = "Read",
    description: str = "test pattern for builder",
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


def _insert_error(
    conn: sqlite3.Connection,
    *,
    session_id: str = "sess-001",
    tool_name: str = "Read",
    error_text: str = "FileNotFoundError: /tmp/missing.py",
    error_type: str = "tool_failure",
    timestamp: str = _NOW,
    source_file: str = "2026-02-25_10-00-00Z-test.md",
    user_message: str = "Read the config file.",
) -> int:
    """Insert a minimal error_records row and return its rowid."""
    cursor = conn.execute(
        """
        INSERT INTO error_records
            (session_id, timestamp, source_type, source_file,
             tool_name, error_text, user_message, error_type, mined_at)
        VALUES (?, ?, 'specstory', ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            timestamp,
            source_file,
            tool_name,
            error_text,
            user_message,
            error_type,
            _NOW,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _link_error_to_pattern(
    conn: sqlite3.Connection, pattern_row_id: int, error_row_id: int
) -> None:
    """Associate an error with a pattern via pattern_errors."""
    conn.execute(
        "INSERT OR IGNORE INTO pattern_errors (pattern_id, error_id) VALUES (?, ?)",
        (pattern_row_id, error_row_id),
    )
    conn.commit()


def _build_pattern_dict(
    *,
    pattern_id: str = "p-builder-001",
    tool_name: str = "Read",
    row_id: int = 1,
) -> dict:
    """Construct a minimal pattern dict as returned by the clustering step."""
    return {
        "id": row_id,
        "pattern_id": pattern_id,
        "tool_name": tool_name,
        "description": "test pattern",
        "error_count": 0,
        "session_count": 1,
        "first_seen": _NOW,
        "last_seen": _NOW,
        "rank_score": 0.5,
    }


def _make_errors_for_pattern(
    conn: sqlite3.Connection,
    pattern_row_id: int,
    *,
    tool_name: str = "Read",
    positive_count: int = 3,
    negative_count: int = 3,
    session_prefix: str = "sess",
) -> list[int]:
    """Insert positive and negative errors linked to a pattern, returning all row ids."""
    inserted_ids: list[int] = []

    # Positive examples — no error text (successful calls).
    for i in range(positive_count):
        eid = _insert_error(
            conn,
            session_id=f"{session_prefix}-pos-{i:03d}",
            tool_name=tool_name,
            error_text="",
            error_type="success",
            user_message=f"Positive call {i}",
        )
        _link_error_to_pattern(conn, pattern_row_id, eid)
        inserted_ids.append(eid)

    # Negative examples — carry a real error text.
    for i in range(negative_count):
        eid = _insert_error(
            conn,
            session_id=f"{session_prefix}-neg-{i:03d}",
            tool_name=tool_name,
            error_text=f"FileNotFoundError: /tmp/file_{i}.py",
            error_type="tool_failure",
            user_message=f"Negative call {i}",
        )
        _link_error_to_pattern(conn, pattern_row_id, eid)
        inserted_ids.append(eid)

    return inserted_ids


# ---------------------------------------------------------------------------
# T028-1: build_dataset — positive and negative examples are collected
# ---------------------------------------------------------------------------


class TestBuildsPositiveAndNegativeExamples:
    """build_dataset must collect both positive and negative examples for the pattern."""

    def test_builds_positive_and_negative_examples(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, tool_name="Read")
        pattern = _build_pattern_dict(row_id=pattern_row_id)

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=4, negative_count=4)

        # Collect ALL error records to pass as all_errors.
        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)

        assert result is not None
        assert result["positive_count"] > 0
        assert result["negative_count"] > 0

    def test_positive_examples_have_no_error_text(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, tool_name="Bash")
        pattern = _build_pattern_dict(row_id=pattern_row_id, tool_name="Bash")

        _make_errors_for_pattern(
            v2_db, pattern_row_id, tool_name="Bash", positive_count=5, negative_count=3
        )

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        file_path = Path(result["file_path"])
        payload = json.loads(file_path.read_text())
        positives = [ex for ex in payload["examples"] if ex["label"] == 1]
        # All positive examples must have an empty or absent error_text.
        for ex in positives:
            assert not ex.get("error_text"), (
                f"Positive example should have no error_text; got: {ex.get('error_text')!r}"
            )

    def test_negative_examples_have_error_text(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, tool_name="Edit")
        pattern = _build_pattern_dict(row_id=pattern_row_id, tool_name="Edit")

        _make_errors_for_pattern(
            v2_db, pattern_row_id, tool_name="Edit", positive_count=4, negative_count=4
        )

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        file_path = Path(result["file_path"])
        payload = json.loads(file_path.read_text())
        negatives = [ex for ex in payload["examples"] if ex["label"] == 0]
        for ex in negatives:
            assert ex.get("error_text"), "Negative example must carry a non-empty error_text"


# ---------------------------------------------------------------------------
# T028-2: minimum threshold — patterns with fewer than 5 examples return None
# ---------------------------------------------------------------------------


class TestMinimumThresholdEnforced:
    """build_dataset must return None when total examples are below the threshold."""

    def test_minimum_threshold_returns_none_when_below(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-sparse-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-sparse-001")

        # Only 2 errors total — well below the default threshold of 5.
        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=1, negative_count=1)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)

        assert result is None

    def test_exactly_at_threshold_succeeds(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-threshold-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-threshold-001")

        # Exactly 5 total examples — should pass the threshold.
        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=2)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)

        assert result is not None

    def test_custom_threshold_parameter(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-custom-thresh-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-custom-thresh-001")

        # 3 examples — below default of 5 but above a custom threshold of 2.
        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=2, negative_count=1)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path, min_threshold=2)

        assert result is not None


# ---------------------------------------------------------------------------
# T028-3: incremental update — calling build_dataset twice appends, not rebuilds
# ---------------------------------------------------------------------------


class TestIncrementalUpdateAppends:
    """A second call to build_dataset appends examples rather than rebuilding."""

    def test_incremental_update_appends(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-incremental-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-incremental-001")

        # First batch — 5 errors.
        _make_errors_for_pattern(
            v2_db,
            pattern_row_id,
            positive_count=3,
            negative_count=2,
            session_prefix="first",
        )

        all_errors_first = [
            dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()
        ]

        result_first = build_dataset(pattern, all_errors_first, v2_db, dataset_dir=tmp_path)
        assert result_first is not None
        first_total = result_first["positive_count"] + result_first["negative_count"]

        # Second batch — 5 more errors with different sessions.
        _make_errors_for_pattern(
            v2_db,
            pattern_row_id,
            positive_count=3,
            negative_count=2,
            session_prefix="second",
        )

        all_errors_second = [
            dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()
        ]

        result_second = build_dataset(pattern, all_errors_second, v2_db, dataset_dir=tmp_path)
        assert result_second is not None
        second_total = result_second["positive_count"] + result_second["negative_count"]

        assert second_total > first_total, (
            f"Expected example count to grow after second build; "
            f"first={first_total}, second={second_total}"
        )

    def test_incremental_update_file_path_stable(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """The dataset file path must not change between incremental builds."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-stable-path-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-stable-path-001")

        _make_errors_for_pattern(
            v2_db,
            pattern_row_id,
            positive_count=3,
            negative_count=2,
            session_prefix="batch-a",
        )

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result_a = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result_a is not None

        _make_errors_for_pattern(
            v2_db,
            pattern_row_id,
            positive_count=2,
            negative_count=3,
            session_prefix="batch-b",
        )

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result_b = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result_b is not None

        assert result_a["file_path"] == result_b["file_path"], (
            "File path must be stable across incremental builds"
        )


# ---------------------------------------------------------------------------
# T028-4: JSON file structure
# ---------------------------------------------------------------------------


class TestDatasetJsonStructure:
    """The written JSON file must conform to the documented schema."""

    def test_dataset_json_structure(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-json-struct-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-json-struct-001")

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=3)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        file_path = Path(result["file_path"])
        assert file_path.exists(), "Dataset JSON file must exist on disk"

        payload = json.loads(file_path.read_text())

        # Top-level keys.
        assert "examples" in payload, "JSON payload must contain 'examples' key"
        assert "metadata" in payload, "JSON payload must contain 'metadata' key"

    def test_examples_list_contains_dicts(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-json-list-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-json-list-001")

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=2)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        payload = json.loads(Path(result["file_path"]).read_text())

        assert isinstance(payload["examples"], list), "'examples' must be a list"
        assert len(payload["examples"]) >= 5, "Must have at least 5 examples"
        for ex in payload["examples"]:
            assert isinstance(ex, dict), "Each example must be a dict"

    def test_each_example_has_label_field(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-json-label-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-json-label-001")

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=3)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        payload = json.loads(Path(result["file_path"]).read_text())
        for ex in payload["examples"]:
            assert "label" in ex, f"Example missing 'label' field: {ex}"
            assert ex["label"] in (0, 1), f"label must be 0 or 1; got {ex['label']!r}"

    def test_metadata_contains_pattern_id(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-json-meta-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-json-meta-001")

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=3)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        payload = json.loads(Path(result["file_path"]).read_text())
        assert "pattern_id" in payload["metadata"], "metadata must include 'pattern_id'"


# ---------------------------------------------------------------------------
# T028-5: on-demand collection — by time range
# ---------------------------------------------------------------------------


class TestOnDemandByTimeRange:
    """collect_dataset(since=...) must filter errors by timestamp."""

    def test_on_demand_by_time_range(self, v2_db: sqlite3.Connection) -> None:
        # Insert errors at two different timestamps.
        old_ts = "2026-01-01T00:00:00Z"
        new_ts = "2026-02-20T12:00:00Z"

        for i in range(3):
            _insert_error(
                v2_db,
                session_id=f"old-{i}",
                timestamp=old_ts,
                error_text=f"OldError {i}",
            )
        for i in range(3):
            _insert_error(
                v2_db,
                session_id=f"new-{i}",
                timestamp=new_ts,
                error_text=f"NewError {i}",
            )

        # Collect with a cutoff that only includes the newer errors.
        result = collect_dataset(v2_db, since="2026-02-15")

        assert "errors" in result, "collect_dataset must return a dict with 'errors'"
        returned_timestamps = [e["timestamp"] for e in result["errors"]]
        for ts in returned_timestamps:
            assert ts >= "2026-02-15", (
                f"Returned error has timestamp {ts!r} older than since cutoff"
            )
        # Older errors must not be present.
        assert not any(ts == old_ts for ts in returned_timestamps), (
            "Errors older than 'since' cutoff must be excluded"
        )

    def test_on_demand_by_time_range_empty_when_all_old(self, v2_db: sqlite3.Connection) -> None:
        """When all errors predate 'since', the result errors list must be empty."""
        for i in range(3):
            _insert_error(
                v2_db,
                session_id=f"very-old-{i}",
                timestamp="2025-06-01T00:00:00Z",
                error_text=f"OldError {i}",
            )

        result = collect_dataset(v2_db, since="2026-01-01")

        assert result["errors"] == [], (
            "No errors should be returned when all predate the since cutoff"
        )


# ---------------------------------------------------------------------------
# T028-6: on-demand collection — by error type
# ---------------------------------------------------------------------------


class TestOnDemandByErrorType:
    """collect_dataset(error_type=...) must filter by the error_type column."""

    def test_on_demand_by_error_type(self, v2_db: sqlite3.Connection) -> None:
        # Insert mixed error types.
        for i in range(4):
            _insert_error(
                v2_db,
                session_id=f"tf-{i}",
                error_type="tool_failure",
                error_text=f"ToolFailure {i}",
            )
        for i in range(3):
            _insert_error(
                v2_db,
                session_id=f"pe-{i}",
                error_type="parse_error",
                error_text=f"ParseError {i}",
            )

        result = collect_dataset(v2_db, error_type="tool_failure")

        assert "errors" in result
        for error in result["errors"]:
            assert error["error_type"] == "tool_failure", (
                f"Expected error_type='tool_failure', got {error['error_type']!r}"
            )

    def test_on_demand_by_error_type_excludes_others(self, v2_db: sqlite3.Connection) -> None:
        """Errors with a non-matching error_type must not appear in results."""
        for i in range(2):
            _insert_error(
                v2_db,
                session_id=f"skip-{i}",
                error_type="parse_error",
                error_text=f"parse {i}",
            )
        _insert_error(
            v2_db,
            session_id="keep-0",
            error_type="network_error",
            error_text="connection refused",
        )

        result = collect_dataset(v2_db, error_type="network_error")

        assert len(result["errors"]) == 1
        assert result["errors"][0]["error_type"] == "network_error"


# ---------------------------------------------------------------------------
# T028-7: metadata returned from build_dataset
# ---------------------------------------------------------------------------


class TestDatasetMetadataReturned:
    """build_dataset must return a dict with the documented metadata keys."""

    _REQUIRED_METADATA_KEYS = frozenset(
        {"pattern_id", "positive_count", "negative_count", "file_path"}
    )

    def test_dataset_metadata_returned(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-meta-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-meta-001")

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=3)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)

        assert result is not None
        missing = self._REQUIRED_METADATA_KEYS - set(result.keys())
        assert not missing, f"build_dataset result missing keys: {sorted(missing)}"

    def test_metadata_pattern_id_matches_input(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        pid = "p-meta-id-check-001"
        pattern_row_id = _insert_pattern(v2_db, pattern_id=pid)
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id=pid)

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=2)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)

        assert result is not None
        assert result["pattern_id"] == pid

    def test_metadata_counts_are_ints(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-meta-types-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-meta-types-001")

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=3)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        assert isinstance(result["positive_count"], int), "positive_count must be an int"
        assert isinstance(result["negative_count"], int), "negative_count must be an int"

    def test_metadata_file_path_is_str(self, v2_db: sqlite3.Connection, tmp_path: Path) -> None:
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-meta-fpath-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-meta-fpath-001")

        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=2)

        all_errors = [dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()]

        result = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert result is not None

        assert isinstance(result["file_path"], str), "file_path in metadata must be a string"
        assert Path(result["file_path"]).exists(), (
            "file_path must point to an existing file after build"
        )


class TestCycleIsolation:
    """B1 regression — per-cycle dataset rebuild prevents stale-file pollution.

    The DSPy SuggestionGenerator grounds rule generation on the dataset JSON
    file at ``~/.sio/datasets/<pattern_id>.json``. Pattern_id is centroid-hash
    stable, so a Hop-2 narrowed run can collide with a prior wider-grep run's
    pattern_id. Without per-cycle isolation, ``build_dataset`` appends the
    narrowed examples after the prior wide examples — and ``examples[:20]``
    in the generator then sees stale generic errors first, producing generic
    rule text. Passing ``cycle_id`` rebuilds from scratch.
    """

    def test_cycle_id_rebuilds_dataset_dropping_stale_examples(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        # Wide-grep run: 6 errors linked to pattern.
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-cycle-iso-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-cycle-iso-001")
        wide_ids = _make_errors_for_pattern(
            v2_db, pattern_row_id, positive_count=3, negative_count=3
        )
        all_errors_wide = [
            dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()
        ]

        # Cycle 1 — write the dataset.
        result_1 = build_dataset(
            pattern, all_errors_wide, v2_db, dataset_dir=tmp_path, cycle_id=1
        )
        assert result_1 is not None
        file_1 = Path(result_1["file_path"])
        payload_1 = json.loads(file_1.read_text())
        assert len(payload_1["examples"]) == len(wide_ids)
        assert payload_1["metadata"]["cycle_id"] == 1

        # Hop-2 narrowed run: simulate the user iterating with --refine.
        # The DB now has the same 6 errors but the all_errors slice handed to
        # build_dataset reflects only the 2 errors that survived narrowing.
        narrowed_subset = all_errors_wide[:2]

        # Cycle 2 — rebuild from the narrowed set; MUST NOT carry over stale 4.
        result_2 = build_dataset(
            pattern, narrowed_subset, v2_db, dataset_dir=tmp_path, cycle_id=2
        )
        # narrowed_subset has only 2 examples — below the default min_threshold=5
        # so result_2 will be None unless we lower the threshold for the test.
        result_2 = build_dataset(
            pattern,
            narrowed_subset,
            v2_db,
            dataset_dir=tmp_path,
            min_threshold=1,
            cycle_id=2,
        )
        assert result_2 is not None
        payload_2 = json.loads(Path(result_2["file_path"]).read_text())

        # The B1 assertion: cycle 2's file contains EXACTLY the narrowed
        # subset, not the wide-cycle leftovers.
        assert len(payload_2["examples"]) == len(narrowed_subset), (
            f"cycle_id=2 rebuild leaked stale examples — got "
            f"{len(payload_2['examples'])}, expected {len(narrowed_subset)}"
        )
        assert payload_2["metadata"]["cycle_id"] == 2

        narrowed_ids = {e["id"] for e in narrowed_subset}
        actual_ids = {e["id"] for e in payload_2["examples"]}
        assert actual_ids == narrowed_ids, (
            "cycle 2 examples must match exactly the narrowed input ids; "
            f"unexpected: {actual_ids - narrowed_ids}, missing: {narrowed_ids - actual_ids}"
        )

    def test_context_fields_round_trip_through_example(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """B2 regression — context_before/context_after must reach the dataset.

        These are the most discriminating signal for theme-specific rule
        generation (the agent's pre-error action). Until B2 they were dropped
        between mining and DSPy grounding.
        """
        from sio.datasets.builder import _error_to_example

        error = {
            "id": 42,
            "error_text": "FileNotFoundError: missing.py",
            "tool_name": "Read",
            "session_id": "sess-ctx-001",
            "timestamp": _NOW,
            "user_message": "open the config",
            "error_type": "tool_failure",
            "source_file": "x.md",
            "context_before": "agent attempted to ZENO_DIR-switch but cwd was BAS-2",
            "context_after": "user said no, that's wrong project",
        }
        example = _error_to_example(error)
        assert example["context_before"] == error["context_before"]
        assert example["context_after"] == error["context_after"]

    def test_context_fields_truncated_to_bounded_size(self) -> None:
        """B2 — long contexts must be truncated to keep grounding payloads bounded."""
        from sio.datasets.builder import _CONTEXT_TRUNC_CHARS, _error_to_example

        long_ctx = "X" * (_CONTEXT_TRUNC_CHARS + 200)
        example = _error_to_example(
            {
                "id": 1,
                "error_text": "boom",
                "context_before": long_ctx,
                "context_after": long_ctx,
            }
        )
        assert example["context_before"] is not None
        assert len(example["context_before"]) <= _CONTEXT_TRUNC_CHARS + 3  # +"…"
        assert example["context_before"].endswith("…")
        assert example["context_after"].endswith("…")

    def test_context_fields_none_when_absent(self) -> None:
        """B2 — missing context fields must roundtrip as None, not empty string."""
        from sio.datasets.builder import _error_to_example

        example = _error_to_example({"id": 1, "error_text": "boom"})
        assert example["context_before"] is None
        assert example["context_after"] is None

    def test_cycle_id_none_preserves_legacy_append_semantics(
        self, v2_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Backwards compat — callers passing no cycle_id keep the merge behavior."""
        pattern_row_id = _insert_pattern(v2_db, pattern_id="p-cycle-legacy-001")
        pattern = _build_pattern_dict(row_id=pattern_row_id, pattern_id="p-cycle-legacy-001")
        _make_errors_for_pattern(v2_db, pattern_row_id, positive_count=3, negative_count=3)

        all_errors = [
            dict(row) for row in v2_db.execute("SELECT * FROM error_records").fetchall()
        ]

        # First call without cycle_id — writes 6 examples.
        first = build_dataset(pattern, all_errors, v2_db, dataset_dir=tmp_path)
        assert first is not None
        first_payload = json.loads(Path(first["file_path"]).read_text())
        assert len(first_payload["examples"]) == 6
        assert "cycle_id" not in first_payload["metadata"]

        # Second call without cycle_id and only 2 of the 6 — merges, total stays 6.
        # (legacy "incremental update" semantics; no dedup re-introduces anything.)
        subset_errors = all_errors[:2]
        second = build_dataset(
            pattern, subset_errors, v2_db, dataset_dir=tmp_path, min_threshold=1
        )
        assert second is not None
        second_payload = json.loads(Path(second["file_path"]).read_text())
        assert len(second_payload["examples"]) == 6, (
            "without cycle_id the file must retain merged historical examples"
        )
