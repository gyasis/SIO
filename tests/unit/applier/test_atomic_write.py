"""Failing tests for atomic_write() — T016 (TDD red).

Tests assert (per research.md R-4, FR-004):
  1. atomic_write() returns a backup Path
  2. Target file has new content after write
  3. Backup file exists with pre-write content
  4. Backup filename matches <basename>.<UTC_ts>.bak timestamp format
  5. Post-write size check: if size < 90% of intended, restore + raise WriteIntegrityError
  6. _prune_backups keeps most recent 10, deletes older

Run to confirm RED before implementing writer.py:
    uv run pytest tests/unit/applier/test_atomic_write.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _import_writer():
    from sio.core.applier import writer  # noqa: PLC0415

    return writer


def _import_atomic_write():
    from sio.core.applier.writer import atomic_write  # noqa: PLC0415

    return atomic_write


def _import_prune():
    from sio.core.applier.writer import _prune_backups  # noqa: PLC0415

    return _prune_backups


def _import_errors():
    from sio.core.applier.writer import WriteIntegrityError  # noqa: PLC0415

    return WriteIntegrityError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def target_file(tmp_path: Path) -> Path:
    """A target file under a tmp dir that simulates ~/.claude/."""
    # Create a fake ~/.claude subtree to satisfy allowlist
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True)
    f = claude_dir / "CLAUDE.md"
    f.write_text("original content\n", encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# 1. Happy path: returns backup path, target updated, backup exists
# ---------------------------------------------------------------------------


def test_atomic_write_returns_path(target_file: Path, monkeypatch):
    """atomic_write() returns a Path object pointing to the backup."""
    atomic_write = _import_atomic_write()
    # Monkeypatch ALLOWLIST_ROOTS to allow tmp_path targets
    writer = _import_writer()
    monkeypatch.setattr(writer, "ALLOWLIST_ROOTS", [target_file.parent])

    result = atomic_write(target_file, "new content\n")
    assert isinstance(result, Path), f"Expected Path, got {type(result)}"


def test_atomic_write_target_has_new_content(target_file: Path, monkeypatch):
    """After atomic_write(), the target file contains the new content."""
    atomic_write = _import_atomic_write()
    writer = _import_writer()
    monkeypatch.setattr(writer, "ALLOWLIST_ROOTS", [target_file.parent])

    atomic_write(target_file, "brand new content\n")
    assert target_file.read_text(encoding="utf-8") == "brand new content\n"


def test_atomic_write_backup_exists(target_file: Path, monkeypatch):
    """After atomic_write(), a backup file with the original content exists."""
    atomic_write = _import_atomic_write()
    writer = _import_writer()
    monkeypatch.setattr(writer, "ALLOWLIST_ROOTS", [target_file.parent])

    original = target_file.read_text(encoding="utf-8")
    backup_path = atomic_write(target_file, "replacement\n")

    assert backup_path.exists(), f"Backup file not found at {backup_path}"
    assert backup_path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# 2. Backup filename timestamp format
# ---------------------------------------------------------------------------

_BAK_PATTERN = re.compile(r"\.\d{8}T\d{6}Z\.bak$")


def test_atomic_write_backup_filename_format(target_file: Path, monkeypatch):
    """Backup filename ends with .<YYYYMMDD>T<HHMMSS>Z.bak."""
    atomic_write = _import_atomic_write()
    writer = _import_writer()
    monkeypatch.setattr(writer, "ALLOWLIST_ROOTS", [target_file.parent])

    backup_path = atomic_write(target_file, "updated\n")
    assert _BAK_PATTERN.search(backup_path.name), (
        f"Backup filename {backup_path.name!r} does not match expected pattern "
        r"'.<YYYYMMDD>T<HHMMSS>Z.bak'"
    )


# ---------------------------------------------------------------------------
# 3. Post-write size check — simulated corruption triggers WriteIntegrityError
# ---------------------------------------------------------------------------


def test_atomic_write_size_check_triggers_integrity_error(target_file: Path, monkeypatch):
    """If post-write target is < 90% of intended size, WriteIntegrityError is raised."""
    atomic_write = _import_atomic_write()
    WriteIntegrityError = _import_errors()
    writer = _import_writer()
    monkeypatch.setattr(writer, "ALLOWLIST_ROOTS", [target_file.parent])

    original = target_file.read_text(encoding="utf-8")
    new_content = "x" * 1000  # Large intended content

    # Monkeypatch os.replace to write truncated content instead
    import os as _os

    real_replace = _os.replace

    def corrupt_replace(src, dst):
        # Write only 10 bytes (far below 90% of 1000)
        Path(dst).write_text("corrupted\n", encoding="utf-8")

    monkeypatch.setattr(_os, "replace", corrupt_replace)

    with pytest.raises(WriteIntegrityError):
        atomic_write(target_file, new_content)

    # Target should be restored to original content
    restored = target_file.read_text(encoding="utf-8")
    assert restored == original, (
        f"Target not restored after WriteIntegrityError: {restored!r} != {original!r}"
    )


# ---------------------------------------------------------------------------
# 4. WriteIntegrityError is Exception subclass
# ---------------------------------------------------------------------------


def test_write_integrity_error_is_exception():
    """WriteIntegrityError must be a subclass of Exception."""
    WriteIntegrityError = _import_errors()
    assert issubclass(WriteIntegrityError, Exception)


# ---------------------------------------------------------------------------
# 5. _prune_backups — keeps most recent 10, deletes older
# ---------------------------------------------------------------------------


def test_prune_backups_keeps_most_recent_10(tmp_path: Path):
    """_prune_backups(dir, keep=10) deletes all but the 10 newest .bak files."""
    _prune_backups = _import_prune()
    bak_dir = tmp_path / "backups"
    bak_dir.mkdir()

    # Create 15 .bak files with different mtimes
    files = []
    for i in range(15):
        f = bak_dir / f"CLAUDE.md.2026040{i:02d}T120000Z.bak"
        f.write_text(f"backup {i}", encoding="utf-8")
        # Spread mtimes by 1 second each
        mtime = 1_700_000_000 + i
        import os

        os.utime(f, (mtime, mtime))
        files.append((mtime, f))

    _prune_backups(bak_dir, keep=10)

    remaining = list(bak_dir.glob("*.bak"))
    assert len(remaining) == 10, f"Expected 10 backups after pruning 15, got {len(remaining)}"

    # The 10 most recent (highest mtime) must survive
    files.sort(key=lambda x: x[0], reverse=True)
    surviving_names = {f.name for f in remaining}
    for _, expected_survivor in files[:10]:
        assert expected_survivor.name in surviving_names, (
            f"{expected_survivor.name} should have survived pruning (recent)"
        )


def test_prune_backups_fewer_than_keep_is_noop(tmp_path: Path):
    """_prune_backups does nothing when fewer than keep files exist."""
    _prune_backups = _import_prune()
    bak_dir = tmp_path / "backups"
    bak_dir.mkdir()

    for i in range(5):
        (bak_dir / f"f{i}.bak").write_text(f"b{i}", encoding="utf-8")

    _prune_backups(bak_dir, keep=10)
    remaining = list(bak_dir.glob("*.bak"))
    assert len(remaining) == 5


# ---------------------------------------------------------------------------
# 6. No partial state — target contains either old or new content
# ---------------------------------------------------------------------------


def test_atomic_write_no_partial_state(target_file: Path, monkeypatch):
    """If write succeeds, target contains exactly new_content (no partial state)."""
    atomic_write = _import_atomic_write()
    writer = _import_writer()
    monkeypatch.setattr(writer, "ALLOWLIST_ROOTS", [target_file.parent])

    new_content = "complete replacement\n" * 100
    atomic_write(target_file, new_content)

    result = target_file.read_text(encoding="utf-8")
    assert result == new_content, "Target content is not exactly new_content"
