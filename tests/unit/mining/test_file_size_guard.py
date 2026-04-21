"""Unit tests for T089: file-size guard and missing-dir warning in pipeline.py.

FR-027: files > 1 GB must be skipped with a WARNING log entry and return None.
FR-028: missing session directories must emit a WARN log.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from sio.mining.pipeline import _file_hash


class TestFileSizeGuard:
    """_file_hash skips files larger than 1 GB."""

    def test_large_file_returns_none(self, tmp_path: Path, caplog):
        """A file reported as > 1 GB must return None and log a WARNING."""
        large_file = tmp_path / "huge_session.jsonl"
        large_file.write_text("x")  # Tiny content; we mock stat().st_size

        one_gb_plus = 1_073_741_825  # 1 GiB + 1 byte
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = one_gb_plus
            with caplog.at_level(logging.WARNING, logger="sio.mining.pipeline"):
                result = _file_hash(large_file)

        assert result is None, "_file_hash must return None for files > 1 GB"
        assert any(
            "1 GB" in record.message or "too large" in record.message.lower()
            for record in caplog.records
        ), "Expected a WARNING log mentioning 1 GB or 'too large'"

    def test_normal_file_returns_hash(self, tmp_path: Path):
        """A file under 1 GB must still return a valid SHA-256 hex string."""
        normal_file = tmp_path / "session.jsonl"
        normal_file.write_text('{"type":"user","message":{"role":"user","content":"hi"}}')
        result = _file_hash(normal_file)
        assert isinstance(result, str)
        assert len(result) == 64
