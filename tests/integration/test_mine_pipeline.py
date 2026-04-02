"""T060 [US5] Integration tests for the full mining pipeline.

Tests the end-to-end flow: source files -> parsers -> extractor -> DB storage.

Pipeline entry point under test:

    from sio.mining.pipeline import run_mine

    run_mine(
        db_conn: sqlite3.Connection,
        source_dirs: list[Path],
        since: str,
        source_type: str = "both",
        project: str | None = None,
    ) -> dict

The returned dict must contain:
    total_files_scanned  int   — number of source files examined
    errors_found         int   — number of ErrorRecord rows inserted
    error_records        list  — the auto-assigned row IDs of inserted records
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sio.mining.pipeline import run_mine

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Use today's date so the "7 days" filter always includes test files.
_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


_SPECSTORY_ERRORS = [
    "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/missing.py'",
    "PermissionError: [Errno 13] Permission denied: '/etc/secret'",
    "TimeoutError: tool execution exceeded 30s limit",
    "JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
    "AttributeError: 'NoneType' object has no attribute 'read'",
]

_JSONL_ERRORS = [
    "CalledProcessError: command 'pytest' returned non-zero exit status 1",
    "ConnectionRefusedError: [Errno 111] Connection refused",
    "ValueError: invalid literal for int() with base 10: 'not-a-number'",
    "KeyError: 'tool_output' missing from response payload",
    "RuntimeError: event loop is already running",
]

# ---------------------------------------------------------------------------
# Test 1: SpecStory files only
# ---------------------------------------------------------------------------


class TestMineSpecstoryFiles:
    """Five SpecStory files with one error each -> five error records in DB."""

    def test_mine_specstory_files(self, v2_db, sample_specstory_file, tmp_path: Path):
        """Creating 5 SpecStory files with known errors produces the correct
        number of error records in the database."""
        source_dir = tmp_path / "specstory"
        source_dir.mkdir()

        today = _TODAY

        # Create five SpecStory files, one distinct error each.
        for i, err in enumerate(_SPECSTORY_ERRORS):
            sample_specstory_file(
                filename=f"{today}_10-0{i}-00Z-session-{i}.md",
                errors=[err],
            )
            # Move the written file from tmp_path root into source_dir.
            written = tmp_path / f"{today}_10-0{i}-00Z-session-{i}.md"
            written.rename(source_dir / written.name)

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        assert isinstance(result, dict), "run_mine must return a dict"
        assert result["total_files_scanned"] == 5, (
            f"Expected 5 files scanned, got {result['total_files_scanned']}"
        )
        # Each file has exactly one injected error block.
        assert result["errors_found"] >= 5, (
            f"Expected at least 5 errors found, got {result['errors_found']}"
        )

        # Verify rows exist in the database.
        row_count = v2_db.execute(
            "SELECT COUNT(*) FROM error_records WHERE source_type = 'specstory'"
        ).fetchone()[0]
        assert row_count >= 5, (
            f"Expected at least 5 specstory rows in DB, got {row_count}"
        )

    def test_mine_specstory_error_records_have_ids(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """The error_records list in the return dict contains valid integer IDs."""
        source_dir = tmp_path / "specstory_ids"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-single.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-single.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-single.md"
        )

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        assert isinstance(result["error_records"], list)
        for record_id in result["error_records"]:
            assert isinstance(record_id, int), (
                f"Expected int ID, got {type(record_id)}: {record_id}"
            )


# ---------------------------------------------------------------------------
# Test 2: JSONL files only
# ---------------------------------------------------------------------------


class TestMineJsonlFiles:
    """Five JSONL files with one error each -> five error records in DB."""

    def test_mine_jsonl_files(self, v2_db, sample_jsonl_file, tmp_path: Path):
        """Creating 5 JSONL files with known errors produces the correct
        number of error records in the database."""
        source_dir = tmp_path / "jsonl"
        source_dir.mkdir()

        for i, err in enumerate(_JSONL_ERRORS):
            sample_jsonl_file(
                filename=f"session_{i}.jsonl",
                errors=[err],
            )
            written = tmp_path / f"session_{i}.jsonl"
            written.rename(source_dir / written.name)

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="jsonl",
        )

        assert isinstance(result, dict)
        assert result["total_files_scanned"] == 5, (
            f"Expected 5 files scanned, got {result['total_files_scanned']}"
        )
        assert result["errors_found"] >= 5, (
            f"Expected at least 5 errors found, got {result['errors_found']}"
        )

        row_count = v2_db.execute(
            "SELECT COUNT(*) FROM error_records WHERE source_type = 'jsonl'"
        ).fetchone()[0]
        assert row_count >= 5, (
            f"Expected at least 5 jsonl rows in DB, got {row_count}"
        )

    def test_mine_jsonl_return_dict_keys_present(
        self, v2_db, sample_jsonl_file, tmp_path: Path
    ):
        """The return dict always contains the three required top-level keys."""
        source_dir = tmp_path / "jsonl_keys"
        source_dir.mkdir()

        sample_jsonl_file(filename="session_0.jsonl", errors=[_JSONL_ERRORS[0]])
        (tmp_path / "session_0.jsonl").rename(source_dir / "session_0.jsonl")

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="jsonl",
        )

        required_keys = {"total_files_scanned", "errors_found", "error_records"}
        missing = required_keys - result.keys()
        assert not missing, f"run_mine result missing keys: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Test 3: Mixed sources (source_type="both")
# ---------------------------------------------------------------------------


class TestMineBothSources:
    """Mix of SpecStory + JSONL files -> all errors extracted with source_type='both'."""

    def test_mine_both_sources(
        self, v2_db, sample_specstory_file, sample_jsonl_file, tmp_path: Path
    ):
        """Mining with source_type='both' extracts errors from both file types."""
        source_dir = tmp_path / "mixed"
        source_dir.mkdir()

        # Three SpecStory files.
        for i in range(3):
            sample_specstory_file(
                filename=f"{_TODAY}_10-0{i}-00Z-ss-{i}.md",
                errors=[_SPECSTORY_ERRORS[i]],
            )
            written = tmp_path / f"{_TODAY}_10-0{i}-00Z-ss-{i}.md"
            written.rename(source_dir / written.name)

        # Two JSONL files.
        for i in range(2):
            sample_jsonl_file(
                filename=f"jsonl_session_{i}.jsonl",
                errors=[_JSONL_ERRORS[i]],
            )
            written = tmp_path / f"jsonl_session_{i}.jsonl"
            written.rename(source_dir / written.name)

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="both",
        )

        assert result["total_files_scanned"] == 5, (
            f"Expected 5 total files, got {result['total_files_scanned']}"
        )
        assert result["errors_found"] >= 5, (
            f"Expected at least 5 errors, got {result['errors_found']}"
        )

        # Both source types should appear in the DB.
        ss_count = v2_db.execute(
            "SELECT COUNT(*) FROM error_records WHERE source_type = 'specstory'"
        ).fetchone()[0]
        jsonl_count = v2_db.execute(
            "SELECT COUNT(*) FROM error_records WHERE source_type = 'jsonl'"
        ).fetchone()[0]

        assert ss_count >= 3, (
            f"Expected at least 3 specstory records, got {ss_count}"
        )
        assert jsonl_count >= 2, (
            f"Expected at least 2 jsonl records, got {jsonl_count}"
        )

    def test_mine_both_error_records_list_matches_db(
        self, v2_db, sample_specstory_file, sample_jsonl_file, tmp_path: Path
    ):
        """IDs in error_records must correspond to actual rows in the DB."""
        source_dir = tmp_path / "mixed_ids"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-only.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-only.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-only.md"
        )

        sample_jsonl_file(filename="only.jsonl", errors=[_JSONL_ERRORS[0]])
        (tmp_path / "only.jsonl").rename(source_dir / "only.jsonl")

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="both",
        )

        returned_ids = result["error_records"]
        assert len(returned_ids) > 0, "Expected at least one ID in error_records list"

        for row_id in returned_ids:
            row = v2_db.execute(
                "SELECT id FROM error_records WHERE id = ?", (row_id,)
            ).fetchone()
            assert row is not None, (
                f"ID {row_id} in error_records but not found in DB"
            )


# ---------------------------------------------------------------------------
# Test 4: Time filter
# ---------------------------------------------------------------------------


class TestMineWithTimeFilter:
    """Files with modification times outside the since window are skipped."""

    def test_mine_with_time_filter_recent_only(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """Only files modified within the since window are processed.

        Three files are created. Two are back-dated to 10 days ago (outside
        the 1-day window) and one is left at current mtime (inside the window).
        Mining with since='1 day' should scan exactly 1 file.
        """
        source_dir = tmp_path / "time_filter"
        source_dir.mkdir()

        now = time.time()
        ten_days_ago = now - (10 * 24 * 60 * 60)

        # Two old files.
        for i in range(2):
            sample_specstory_file(
                filename=f"2026-02-15_10-0{i}-00Z-old-{i}.md",
                errors=[_SPECSTORY_ERRORS[i]],
            )
            old_path = tmp_path / f"2026-02-15_10-0{i}-00Z-old-{i}.md"
            dest = source_dir / old_path.name
            old_path.rename(dest)
            # Back-date the file modification time.
            os.utime(dest, (ten_days_ago, ten_days_ago))

        # One recent file — use today's date so the filename-based filter
        # always considers it "recent" regardless of when the test runs.
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        recent_fname = f"{today_str}_10-00-00Z-recent.md"
        sample_specstory_file(
            filename=recent_fname,
            errors=[_SPECSTORY_ERRORS[2]],
        )
        recent_path = tmp_path / recent_fname
        recent_path.rename(source_dir / recent_path.name)

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="1 day",
            source_type="specstory",
        )

        assert result["total_files_scanned"] == 1, (
            f"Expected only 1 recent file scanned, got {result['total_files_scanned']}"
        )

    def test_mine_with_time_filter_all_old(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """When every file is older than the since window, nothing is processed."""
        source_dir = tmp_path / "all_old"
        source_dir.mkdir()

        now = time.time()
        thirty_days_ago = now - (30 * 24 * 60 * 60)

        for i in range(3):
            sample_specstory_file(
                filename=f"2026-01-01_10-0{i}-00Z-ancient-{i}.md",
                errors=[_SPECSTORY_ERRORS[i]],
            )
            p = tmp_path / f"2026-01-01_10-0{i}-00Z-ancient-{i}.md"
            dest = source_dir / p.name
            p.rename(dest)
            os.utime(dest, (thirty_days_ago, thirty_days_ago))

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        assert result["total_files_scanned"] == 0, (
            f"Expected 0 files scanned (all old), got {result['total_files_scanned']}"
        )
        assert result["errors_found"] == 0

    def test_mine_time_filter_jsonl(
        self, v2_db, sample_jsonl_file, tmp_path: Path
    ):
        """Time filtering also applies to JSONL files."""
        source_dir = tmp_path / "jsonl_time"
        source_dir.mkdir()

        now = time.time()
        eight_days_ago = now - (8 * 24 * 60 * 60)

        # One old JSONL file.
        sample_jsonl_file(filename="old_session.jsonl", errors=[_JSONL_ERRORS[0]])
        old = tmp_path / "old_session.jsonl"
        dest_old = source_dir / "old_session.jsonl"
        old.rename(dest_old)
        os.utime(dest_old, (eight_days_ago, eight_days_ago))

        # One recent JSONL file.
        sample_jsonl_file(filename="new_session.jsonl", errors=[_JSONL_ERRORS[1]])
        new = tmp_path / "new_session.jsonl"
        new.rename(source_dir / "new_session.jsonl")

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="jsonl",
        )

        assert result["total_files_scanned"] == 1, (
            f"Expected 1 recent jsonl file, got {result['total_files_scanned']}"
        )


# ---------------------------------------------------------------------------
# Test 5: Database storage verification
# ---------------------------------------------------------------------------


class TestMineStoresToDb:
    """After mining, ErrorRecord rows in the DB must have correct metadata."""

    def test_mine_stores_correct_source_type(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """source_type column in DB must match the mined file format."""
        source_dir = tmp_path / "db_meta"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-check.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-check.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-check.md"
        )

        run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        rows = v2_db.execute(
            "SELECT source_type FROM error_records"
        ).fetchall()
        assert len(rows) >= 1
        for row in rows:
            assert row["source_type"] == "specstory", (
                f"Unexpected source_type: {row['source_type']}"
            )

    def test_mine_stores_source_file_path(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """source_file column must contain the path to the source file."""
        source_dir = tmp_path / "db_path"
        source_dir.mkdir()

        filename = f"{_TODAY}_10-00-00Z-path-check.md"
        sample_specstory_file(filename=filename, errors=[_SPECSTORY_ERRORS[0]])
        expected_path = source_dir / filename
        (tmp_path / filename).rename(expected_path)

        run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        rows = v2_db.execute(
            "SELECT source_file FROM error_records"
        ).fetchall()
        assert len(rows) >= 1
        # At least one row must reference the expected file.
        source_files = {row["source_file"] for row in rows}
        assert any(filename in sf for sf in source_files), (
            f"Expected source file containing '{filename}' in {source_files}"
        )

    def test_mine_stores_error_text(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """error_text column must contain content from the injected error string."""
        source_dir = tmp_path / "db_errtext"
        source_dir.mkdir()

        injected_error = "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/missing.py'"
        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-errtext.md",
            errors=[injected_error],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-errtext.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-errtext.md"
        )

        run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        rows = v2_db.execute(
            "SELECT error_text FROM error_records"
        ).fetchall()
        assert len(rows) >= 1
        error_texts = [row["error_text"] for row in rows]
        assert any("FileNotFoundError" in et for et in error_texts), (
            f"Expected 'FileNotFoundError' in error_text, got: {error_texts}"
        )

    def test_mine_stores_tool_name(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """tool_name column must be populated when the error is attributed to a tool."""
        source_dir = tmp_path / "db_toolname"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-tool.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-tool.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-tool.md"
        )

        run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        rows = v2_db.execute(
            "SELECT tool_name FROM error_records"
        ).fetchall()
        assert len(rows) >= 1
        # At least one row must carry a non-null tool_name.
        tool_names = [row["tool_name"] for row in rows if row["tool_name"] is not None]
        assert len(tool_names) >= 1, (
            f"Expected at least one row with a tool_name, got: {[r['tool_name'] for r in rows]}"
        )

    def test_mine_stores_mined_at_timestamp(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """mined_at column must be a non-empty ISO-format string."""
        source_dir = tmp_path / "db_minedat"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-minedat.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-minedat.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-minedat.md"
        )

        run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        rows = v2_db.execute(
            "SELECT mined_at FROM error_records"
        ).fetchall()
        assert len(rows) >= 1
        for row in rows:
            assert row["mined_at"] is not None
            assert len(row["mined_at"]) > 0, "mined_at must not be empty"

    def test_mine_jsonl_stores_error_type(
        self, v2_db, sample_jsonl_file, tmp_path: Path
    ):
        """error_type column must be populated for JSONL-sourced error records."""
        source_dir = tmp_path / "db_errtype"
        source_dir.mkdir()

        sample_jsonl_file(filename="session_errtype.jsonl", errors=[_JSONL_ERRORS[0]])
        (tmp_path / "session_errtype.jsonl").rename(source_dir / "session_errtype.jsonl")

        run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="jsonl",
        )

        rows = v2_db.execute(
            "SELECT error_type FROM error_records WHERE source_type = 'jsonl'"
        ).fetchall()
        assert len(rows) >= 1
        for row in rows:
            assert row["error_type"] is not None, (
                "error_type must be classified, not None"
            )

    def test_mine_all_required_columns_non_null(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """Every NOT NULL column in error_records must be populated after mining."""
        source_dir = tmp_path / "db_notnull"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-notnull.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-notnull.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-notnull.md"
        )

        run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        rows = v2_db.execute("SELECT * FROM error_records").fetchall()
        assert len(rows) >= 1, "Expected at least one row after mining"

        required_non_null = ("session_id", "timestamp", "source_type", "source_file",
                              "error_text", "mined_at")
        for row in rows:
            for col in required_non_null:
                assert row[col] is not None, (
                    f"Column '{col}' must not be NULL, got NULL for row id={row['id']}"
                )


# ---------------------------------------------------------------------------
# Test 6: Empty directory
# ---------------------------------------------------------------------------


class TestMineEmptyDirectory:
    """An empty source directory produces zero counts."""

    def test_mine_empty_directory_returns_zero_counts(self, v2_db, tmp_path: Path):
        """No files -> total_files_scanned=0 and errors_found=0."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = run_mine(
            v2_db,
            source_dirs=[empty_dir],
            since="7 days",
            source_type="both",
        )

        assert result["total_files_scanned"] == 0, (
            f"Expected 0 files scanned, got {result['total_files_scanned']}"
        )
        assert result["errors_found"] == 0, (
            f"Expected 0 errors found, got {result['errors_found']}"
        )
        assert result["error_records"] == [], (
            f"Expected empty list, got {result['error_records']}"
        )

    def test_mine_empty_directory_db_unchanged(self, v2_db, tmp_path: Path):
        """Mining an empty directory must not insert any rows into the DB."""
        empty_dir = tmp_path / "empty_db_check"
        empty_dir.mkdir()

        run_mine(
            v2_db,
            source_dirs=[empty_dir],
            since="7 days",
            source_type="both",
        )

        row_count = v2_db.execute(
            "SELECT COUNT(*) FROM error_records"
        ).fetchone()[0]
        assert row_count == 0, (
            f"Expected 0 DB rows after empty-dir mine, got {row_count}"
        )

    def test_mine_multiple_empty_directories(self, v2_db, tmp_path: Path):
        """Passing multiple empty directories still yields zero counts."""
        dirs = [tmp_path / f"empty_{i}" for i in range(3)]
        for d in dirs:
            d.mkdir()

        result = run_mine(
            v2_db,
            source_dirs=dirs,
            since="7 days",
            source_type="both",
        )

        assert result["total_files_scanned"] == 0
        assert result["errors_found"] == 0


# ---------------------------------------------------------------------------
# Test 7: Malformed files are skipped
# ---------------------------------------------------------------------------


class TestMineSkipsMalformedFiles:
    """Corrupt / malformed files are skipped; good files are still processed."""

    def _write_malformed_md(self, path: Path) -> None:
        """Write a file with a .md extension that is not valid SpecStory content."""
        path.write_text(
            "\x00\x01\x02\x03binary garbage\xFF\xFE\xFD content that cannot be parsed",
            encoding="latin-1",
        )

    def _write_malformed_jsonl(self, path: Path) -> None:
        """Write a .jsonl file whose every line is invalid JSON."""
        path.write_text(
            "{this is not json}\n{neither is this}\n[broken array\n",
            encoding="utf-8",
        )

    def test_malformed_specstory_skipped_good_processed(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """Good SpecStory files are mined even when some files are malformed."""
        source_dir = tmp_path / "mixed_good_bad_ss"
        source_dir.mkdir()

        # Two good files.
        for i in range(2):
            sample_specstory_file(
                filename=f"{_TODAY}_10-0{i}-00Z-good-{i}.md",
                errors=[_SPECSTORY_ERRORS[i]],
            )
            written = tmp_path / f"{_TODAY}_10-0{i}-00Z-good-{i}.md"
            written.rename(source_dir / written.name)

        # Two malformed .md files.
        for i in range(2):
            self._write_malformed_md(source_dir / f"bad_{i}.md")

        # run_mine must not raise; it must process the 2 good files.
        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        # At minimum the two good files must be accounted for.
        assert result["total_files_scanned"] >= 2, (
            f"Expected at least 2 files scanned, got {result['total_files_scanned']}"
        )
        assert result["errors_found"] >= 2, (
            f"Expected at least 2 errors extracted, got {result['errors_found']}"
        )

    def test_malformed_jsonl_skipped_good_processed(
        self, v2_db, sample_jsonl_file, tmp_path: Path
    ):
        """Good JSONL files are mined even when some .jsonl files are malformed."""
        source_dir = tmp_path / "mixed_good_bad_jsonl"
        source_dir.mkdir()

        # Two good JSONL files.
        for i in range(2):
            sample_jsonl_file(
                filename=f"good_session_{i}.jsonl",
                errors=[_JSONL_ERRORS[i]],
            )
            written = tmp_path / f"good_session_{i}.jsonl"
            written.rename(source_dir / written.name)

        # Two malformed .jsonl files.
        for i in range(2):
            self._write_malformed_jsonl(source_dir / f"bad_{i}.jsonl")

        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="jsonl",
        )

        assert result["total_files_scanned"] >= 2, (
            f"Expected at least 2 files scanned, got {result['total_files_scanned']}"
        )
        assert result["errors_found"] >= 2, (
            f"Expected at least 2 errors extracted, got {result['errors_found']}"
        )

    def test_all_malformed_files_yields_zero_errors(
        self, v2_db, tmp_path: Path
    ):
        """When every file is malformed, errors_found must be 0 (no crash)."""
        source_dir = tmp_path / "all_bad"
        source_dir.mkdir()

        for i in range(3):
            (source_dir / f"bad_{i}.md").write_text(
                f"<html><body>not specstory content {i}</body></html>",
                encoding="utf-8",
            )

        # Must not raise.
        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        assert result["errors_found"] == 0, (
            f"Expected 0 errors from malformed files, got {result['errors_found']}"
        )

    def test_mine_does_not_raise_on_mixed_corrupt_content(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """run_mine must never raise an exception regardless of file corruption."""
        source_dir = tmp_path / "no_raise"
        source_dir.mkdir()

        # One valid file.
        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-valid.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-valid.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-valid.md"
        )

        # Several corrupt files.
        (source_dir / "zero_bytes.md").write_text("", encoding="utf-8")
        (source_dir / "whitespace.md").write_text("\n\n   \n\t\n", encoding="utf-8")
        (source_dir / "json_not_md.md").write_text(
            json.dumps({"not": "specstory"}), encoding="utf-8"
        )

        # Must not raise.
        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )

        assert isinstance(result, dict)
        assert isinstance(result["total_files_scanned"], int)
        assert isinstance(result["errors_found"], int)
        assert isinstance(result["error_records"], list)


# ---------------------------------------------------------------------------
# Test: Multiple source directories
# ---------------------------------------------------------------------------


class TestMineMultipleSourceDirs:
    """run_mine accepts a list of directories and aggregates across all of them."""

    def test_mine_aggregates_across_two_dirs(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """Errors from multiple source directories are combined in the result."""
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        # Two files in dir_a.
        for i in range(2):
            sample_specstory_file(
                filename=f"{_TODAY}_10-0{i}-00Z-a-{i}.md",
                errors=[_SPECSTORY_ERRORS[i]],
            )
            written = tmp_path / f"{_TODAY}_10-0{i}-00Z-a-{i}.md"
            written.rename(dir_a / written.name)

        # Two files in dir_b.
        for i in range(2, 4):
            sample_specstory_file(
                filename=f"{_TODAY}_10-0{i}-00Z-b-{i}.md",
                errors=[_SPECSTORY_ERRORS[i]],
            )
            written = tmp_path / f"{_TODAY}_10-0{i}-00Z-b-{i}.md"
            written.rename(dir_b / written.name)

        result = run_mine(
            v2_db,
            source_dirs=[dir_a, dir_b],
            since="7 days",
            source_type="specstory",
        )

        assert result["total_files_scanned"] == 4, (
            f"Expected 4 files across two dirs, got {result['total_files_scanned']}"
        )
        assert result["errors_found"] >= 4, (
            f"Expected at least 4 errors, got {result['errors_found']}"
        )

    def test_mine_one_dir_empty_one_dir_populated(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """One empty dir + one dir with files -> counts only populated dir."""
        empty_dir = tmp_path / "empty_multi"
        populated_dir = tmp_path / "populated_multi"
        empty_dir.mkdir()
        populated_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-pop.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-pop.md").rename(
            populated_dir / f"{_TODAY}_10-00-00Z-pop.md"
        )

        result = run_mine(
            v2_db,
            source_dirs=[empty_dir, populated_dir],
            since="7 days",
            source_type="specstory",
        )

        assert result["total_files_scanned"] == 1
        assert result["errors_found"] >= 1


# ---------------------------------------------------------------------------
# Test: project filter
# ---------------------------------------------------------------------------


class TestMineProjectFilter:
    """When project is specified, it should be recorded or used to scope results."""

    def test_mine_with_project_does_not_raise(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """Passing a project name must not cause run_mine to raise."""
        source_dir = tmp_path / "project_filter"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-proj.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-proj.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-proj.md"
        )

        # Must not raise.
        result = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
            project="SIO",
        )

        assert isinstance(result, dict)
        assert result["total_files_scanned"] >= 1

    def test_mine_project_none_is_default(
        self, v2_db, sample_specstory_file, tmp_path: Path
    ):
        """Omitting project (None) must behave identically to passing project=None."""
        source_dir = tmp_path / "project_none"
        source_dir.mkdir()

        sample_specstory_file(
            filename=f"{_TODAY}_10-00-00Z-no-proj.md",
            errors=[_SPECSTORY_ERRORS[0]],
        )
        (tmp_path / f"{_TODAY}_10-00-00Z-no-proj.md").rename(
            source_dir / f"{_TODAY}_10-00-00Z-no-proj.md"
        )

        result_implicit = run_mine(
            v2_db,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
        )
        # A second db for the explicit None call to avoid interference.
        from sio.core.db.schema import init_db

        _V2_DDL = [
            """CREATE TABLE IF NOT EXISTS error_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, timestamp TEXT NOT NULL,
            source_type TEXT NOT NULL, source_file TEXT NOT NULL,
            tool_name TEXT, error_text TEXT NOT NULL,
            user_message TEXT, context_before TEXT, context_after TEXT,
            error_type TEXT, mined_at TEXT NOT NULL
            )""",
        ]
        conn2 = init_db(":memory:")
        for ddl in _V2_DDL:
            conn2.execute(ddl)
        conn2.commit()

        result_explicit = run_mine(
            conn2,
            source_dirs=[source_dir],
            since="7 days",
            source_type="specstory",
            project=None,
        )
        conn2.close()

        assert result_implicit["total_files_scanned"] == result_explicit["total_files_scanned"]
        assert result_implicit["errors_found"] == result_explicit["errors_found"]
