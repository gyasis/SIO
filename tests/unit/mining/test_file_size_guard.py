"""Unit tests for T089: file-size guard and missing-dir warning in pipeline.py.

FR-027: files > 1 GB must be skipped with a WARNING log entry.
H-R1.2 fix: instead of returning None (which violates NOT NULL on processed_sessions),
_file_hash now returns a ``__size_exceeded__<hash>`` sentinel string so the DB
insert always has a non-NULL value.

FR-028: missing session directories must emit a WARN log.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from sio.mining.pipeline import _OVERSIZED_HASH_PREFIX, _file_hash


class TestFileSizeGuard:
    """_file_hash returns a sentinel for files larger than 1 GB."""

    def test_large_file_returns_sentinel_not_none(self, tmp_path: Path, caplog):
        """A file reported as > 1 GB must return a sentinel string and log a WARNING.

        H-R1.2 fix: None was returned previously, causing NOT NULL constraint
        failure on processed_sessions.file_hash.  Now a sentinel string is
        returned so the DB write can succeed.
        """
        large_file = tmp_path / "huge_session.jsonl"
        large_file.write_text("x")  # Tiny content; we mock stat().st_size

        one_gb_plus = 1_073_741_825  # 1 GiB + 1 byte
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = one_gb_plus
            mock_stat.return_value.st_mtime = 1714000000.0
            with caplog.at_level(logging.WARNING, logger="sio.mining.pipeline"):
                result = _file_hash(large_file)

        assert result is not None, "_file_hash must not return None for files > 1 GB"
        assert isinstance(result, str), "_file_hash must return a string"
        assert result.startswith(_OVERSIZED_HASH_PREFIX), (
            f"Expected sentinel prefix '{_OVERSIZED_HASH_PREFIX}', got: {result!r}"
        )
        assert any(
            "1 GB" in record.message or "exceeded" in record.message.lower()
            for record in caplog.records
        ), "Expected a WARNING log mentioning 1 GB or 'exceeded'"

    def test_large_file_sentinel_is_unique_per_path(self, tmp_path: Path):
        """Two different oversized files must produce different sentinel hashes."""
        file_a = tmp_path / "session_a.jsonl"
        file_b = tmp_path / "session_b.jsonl"
        file_a.write_text("x")
        file_b.write_text("x")

        one_gb_plus = 1_073_741_825
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = one_gb_plus
            mock_stat.return_value.st_mtime = 1714000000.0
            hash_a = _file_hash(file_a)
            hash_b = _file_hash(file_b)

        assert hash_a != hash_b, "Different oversized files must get different sentinel hashes"

    def test_normal_file_returns_hash(self, tmp_path: Path):
        """A file under 1 GB must still return a valid SHA-256 hex string."""
        normal_file = tmp_path / "session.jsonl"
        normal_file.write_text('{"type":"user","message":{"role":"user","content":"hi"}}')
        result = _file_hash(normal_file)
        assert isinstance(result, str)
        assert len(result) == 64
        assert not result.startswith(_OVERSIZED_HASH_PREFIX)
