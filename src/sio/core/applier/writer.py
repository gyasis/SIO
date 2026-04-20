"""Atomic file writer with path allowlist enforcement — FR-004, FR-019, R-4, R-14.

All writes to CLAUDE.md and other config files MUST go through ``atomic_write()``
to ensure:
  1. Pre-write backup with UTC timestamp
  2. Atomic rename (tmp → target) via ``os.replace`` — POSIX + NTFS safe
  3. Post-write size verification (< 90% of intended → restore + raise)
  4. Backup retention (keep=10 most recent)
  5. Path allowlist enforcement (``~/.claude/`` + SIO_APPLY_EXTRA_ROOTS)

Research: research.md R-4 (atomic write), R-14 (path allowlist).
Constitution: Principle XI (no stubs — every function performs real work).
"""

from __future__ import annotations

import os
from pathlib import Path

from sio.core.util.time import utc_now_iso


# ---------------------------------------------------------------------------
# Allowlist roots (FR-019, R-14)
# ---------------------------------------------------------------------------

def _build_allowlist_roots() -> list[Path]:
    """Build the list of allowed target root directories.

    Always includes ``~/.claude/``.  Additional roots may be appended via the
    ``SIO_APPLY_EXTRA_ROOTS`` environment variable (colon-separated list of
    absolute paths).
    """
    roots: list[Path] = [Path.home() / ".claude"]
    extra = os.environ.get("SIO_APPLY_EXTRA_ROOTS", "").strip()
    if extra:
        for part in extra.split(":"):
            stripped = part.strip()
            if stripped:
                roots.append(Path(stripped))
    return roots


# Module-level default; tests may monkeypatch this directly.
ALLOWLIST_ROOTS: list[Path] = _build_allowlist_roots()

# Backup storage root
BACKUP_ROOT: Path = Path.home() / ".sio" / "backups"


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------

class WriteIntegrityError(Exception):
    """Raised when post-write size check fails (potential file-watcher race).

    When raised, the target file has been restored from the pre-write backup.
    """


class UnauthorizedApplyTarget(Exception):
    """Raised when the target path is not under an allowlisted root."""


class BackupRequired(Exception):
    """Raised when a write is attempted without backup capability (future use)."""


# ---------------------------------------------------------------------------
# Path validation (R-14)
# ---------------------------------------------------------------------------

def _validate_target_path(target: Path) -> None:
    """Validate that *target* is under an allowlisted root.

    Uses ``Path.resolve()`` to follow symlinks and catch directory traversal
    attacks (``~/.claude/../etc/hosts`` resolves to ``/etc/hosts``).

    Args:
        target: The proposed write target.

    Raises:
        UnauthorizedApplyTarget: If the resolved path is not under any
            allowlisted root.
    """
    resolved = target.resolve()
    for root in ALLOWLIST_ROOTS:
        root_resolved = root.resolve()
        try:
            resolved.relative_to(root_resolved)
            return  # Path is under this root — allowed
        except ValueError:
            continue  # Not under this root — try next

    raise UnauthorizedApplyTarget(
        f"Target path {target!r} (resolved: {resolved!r}) is not under any "
        f"allowlisted root. Allowlist: {[str(r) for r in ALLOWLIST_ROOTS]}\n"
        f"Add a root via SIO_APPLY_EXTRA_ROOTS env var if needed."
    )


# ---------------------------------------------------------------------------
# Backup retention
# ---------------------------------------------------------------------------

def _prune_backups(backup_dir: Path, keep: int = 10) -> None:
    """Delete old backup files, keeping the *keep* most recent.

    Only ``*.bak`` files in *backup_dir* (non-recursive) are considered.

    Args:
        backup_dir: Directory containing ``.bak`` files.
        keep: Number of most-recent backups to retain.
    """
    bak_files = sorted(
        backup_dir.glob("*.bak"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old_file in bak_files[keep:]:
        try:
            old_file.unlink()
        except OSError:
            pass  # Best-effort; don't fail the write for a pruning error


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def _ts() -> str:
    """Return a compact UTC timestamp suitable for backup filenames.

    Format: ``YYYYMMDDTHHMMSSZ`` (e.g., ``20260420T143211Z``).
    """
    iso = utc_now_iso()
    # iso is like "2026-04-20T14:32:11.123456+00:00"
    # Compact to "20260420T143211Z"
    compact = iso[:19].replace("-", "").replace(":", "")
    # compact is "20260420T143211"
    return compact + "Z"


# ---------------------------------------------------------------------------
# Atomic write (R-4)
# ---------------------------------------------------------------------------

def atomic_write(target: Path, new_content: str) -> Path:
    """Write *new_content* to *target* atomically, with backup and size check.

    Implements the full R-4 pattern:
    1. Validate target against allowlist (R-14).
    2. Read current content (in-memory pre-state).
    3. Write timestamped backup to ``~/.sio/backups/`` + fsync.
    4. Write *new_content* to a same-directory tmp file, fsync, ``os.replace``.
    5. Post-write size check: if ``len(after) < len(new_content) * 0.9``,
       restore from backup and raise :class:`WriteIntegrityError`.
    6. Prune old backups (keep=10).

    Args:
        target: Absolute path of the file to update.
        new_content: The full replacement content to write.

    Returns:
        Path to the pre-write backup file (``*.bak``).

    Raises:
        UnauthorizedApplyTarget: If *target* is outside the allowlist.
        WriteIntegrityError: If the post-write size check fails.
    """
    _validate_target_path(target)

    # Step 1: Read current content for backup
    prev: str | None = target.read_text(encoding="utf-8") if target.exists() else None

    # Step 2: Pre-write backup with timestamp
    backup_path: Path | None = None
    if prev is not None:
        # Compute backup dir: BACKUP_ROOT / <path-relative-to-home>
        try:
            rel = target.resolve().relative_to(Path.home().resolve())
            backup_dir = BACKUP_ROOT / rel.parent
        except ValueError:
            # Target outside home — use its resolved parent under BACKUP_ROOT
            backup_dir = BACKUP_ROOT / "extra" / target.resolve().parent.name

        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{target.name}.{_ts()}.bak"
        backup_path.write_text(prev, encoding="utf-8")
        # fsync the backup to guarantee it survives a crash
        with open(backup_path, "rb") as bak_f:
            os.fsync(bak_f.fileno())

    # Step 3: Write to same-dir tmp, fsync, atomic rename
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(new_content, encoding="utf-8")
        with open(tmp, "rb") as tmp_f:
            os.fsync(tmp_f.fileno())
        os.replace(tmp, target)
    except Exception:
        # Clean up tmp if rename fails
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise

    # Step 4: Post-write size verification
    after = target.read_text(encoding="utf-8")
    if len(after) < len(new_content) * 0.9:
        # File-watcher race corrupted output — restore from backup
        if prev is not None:
            target.write_text(prev, encoding="utf-8")
        raise WriteIntegrityError(
            f"{target}: post-write size check failed. "
            f"Expected >= {int(len(new_content) * 0.9)} chars, got {len(after)}. "
            + (f"Restored from backup: {backup_path}" if backup_path else "No backup available.")
        )

    # Step 5: Prune old backups
    if backup_path is not None:
        _prune_backups(backup_path.parent, keep=10)

    return backup_path if backup_path is not None else target
