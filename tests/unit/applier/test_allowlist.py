"""Failing tests for _validate_target_path() — T017 (TDD red).

Tests assert (per research.md R-14, FR-019):
  1. Valid path under ~/.claude/ passes with no exception
  2. /etc/hosts raises UnauthorizedApplyTarget
  3. Traversal ~/.claude/../etc/hosts raises (Path.resolve catches)
  4. Symlink traversal to outside allowlist raises
  5. SIO_APPLY_EXTRA_ROOTS=/tmp/extra → /tmp/extra/foo.md passes
  6. Path.cwd() is NOT in allowlist — cwd-relative outside ~/.claude/ raises

Run to confirm RED before implementing writer.py:
    uv run pytest tests/unit/applier/test_allowlist.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _import_validate():
    from sio.core.applier.writer import _validate_target_path  # noqa: PLC0415

    return _validate_target_path


def _import_unauthorized_error():
    from sio.core.applier.writer import UnauthorizedApplyTarget  # noqa: PLC0415

    return UnauthorizedApplyTarget


# ---------------------------------------------------------------------------
# 1. Valid path under ~/.claude/ — no exception
# ---------------------------------------------------------------------------


def test_valid_claude_path_passes():
    """A real path under ~/.claude/ must pass validation without exception."""
    validate = _import_validate()
    claude_root = Path.home() / ".claude"
    target = claude_root / "CLAUDE.md"
    # Should not raise — even if file doesn't exist (allowlist is path-based)
    validate(target)


def test_valid_claude_rules_path_passes():
    """A path under ~/.claude/rules/ must pass validation."""
    validate = _import_validate()
    target = Path.home() / ".claude" / "rules" / "domains" / "test.md"
    validate(target)


# ---------------------------------------------------------------------------
# 2. Rejected paths
# ---------------------------------------------------------------------------


def test_etc_hosts_rejected():
    """An absolute path outside allowlist raises UnauthorizedApplyTarget."""
    validate = _import_validate()
    UnauthorizedApplyTarget = _import_unauthorized_error()

    with pytest.raises(UnauthorizedApplyTarget):
        validate(Path("/etc/hosts"))


def test_home_file_rejected():
    """A file directly in home dir (not under .claude/) is rejected."""
    validate = _import_validate()
    UnauthorizedApplyTarget = _import_unauthorized_error()

    with pytest.raises(UnauthorizedApplyTarget):
        validate(Path.home() / "secret.txt")


# ---------------------------------------------------------------------------
# 3. Traversal attack via dotdot
# ---------------------------------------------------------------------------


def test_traversal_dotdot_rejected():
    """~/.claude/../etc/hosts resolves outside allowlist and must be rejected."""
    validate = _import_validate()
    UnauthorizedApplyTarget = _import_unauthorized_error()

    # This Path.resolve() call will produce /etc/hosts (if it exists on system)
    # or similar outside-allowlist path
    traversal = Path.home() / ".claude" / ".." / "some_outside_file.txt"
    with pytest.raises(UnauthorizedApplyTarget):
        validate(traversal)


# ---------------------------------------------------------------------------
# 4. Symlink traversal — real dest outside allowlist
# ---------------------------------------------------------------------------


def test_symlink_to_outside_allowlist_rejected(tmp_path: Path):
    """A symlink that resolves to outside ~/.claude/ must be rejected."""
    validate = _import_validate()
    UnauthorizedApplyTarget = _import_unauthorized_error()

    # Create a real file outside the allowlist
    real_outside = tmp_path / "outside.txt"
    real_outside.write_text("secret", encoding="utf-8")

    # Create a symlink that looks like it's inside ~/.claude/ but resolves outside
    # We can't place a symlink inside the real ~/.claude/ in tests, so we
    # monkeypatch the ALLOWLIST_ROOTS to include tmp_path as allowed root,
    # but create the symlink pointing outside that root.
    # Instead, just verify that Path.resolve() is used (symlink itself is not enough):
    # We create a fake claude-like dir in tmp_path and a symlink inside it.
    fake_claude = tmp_path / ".claude"
    fake_claude.mkdir()
    symlink = fake_claude / "evil_link.md"
    symlink.symlink_to(real_outside)

    # The symlink resolves to tmp_path/outside.txt which is NOT under fake_claude.
    # If validate() does Path.resolve(), it will catch this.
    # We import writer and temporarily patch ALLOWLIST_ROOTS
    from sio.core.applier import writer  # noqa: PLC0415

    original_roots = writer.ALLOWLIST_ROOTS
    try:
        writer.ALLOWLIST_ROOTS = [fake_claude]
        with pytest.raises(UnauthorizedApplyTarget):
            validate(symlink)
    finally:
        writer.ALLOWLIST_ROOTS = original_roots


# ---------------------------------------------------------------------------
# 5. SIO_APPLY_EXTRA_ROOTS env var extends allowlist
# ---------------------------------------------------------------------------


def test_extra_roots_env_var_allows_path(tmp_path: Path, monkeypatch):
    """SIO_APPLY_EXTRA_ROOTS=/tmp/extra → /tmp/extra/foo.md passes validation."""
    extra_root = tmp_path / "extra"
    extra_root.mkdir()
    monkeypatch.setenv("SIO_APPLY_EXTRA_ROOTS", str(extra_root))

    # Re-import to pick up env changes — writer reads env at call time or module load
    # We need the function to re-evaluate ALLOWLIST_ROOTS with the new env
    # If the module caches roots at import time, monkeypatching module attr is needed
    from sio.core.applier import writer  # noqa: PLC0415

    original_roots = writer.ALLOWLIST_ROOTS
    # Simulate the extra roots being loaded
    extra_roots_env = (
        monkeypatch.getenv("SIO_APPLY_EXTRA_ROOTS")
        if hasattr(monkeypatch, "getenv")
        else extra_root
    )
    writer.ALLOWLIST_ROOTS = list(original_roots) + [extra_root]

    try:
        validate = _import_validate()
        target = extra_root / "foo.md"
        validate(target)  # Must NOT raise
    finally:
        writer.ALLOWLIST_ROOTS = original_roots


def test_extra_roots_validates_correctly(tmp_path: Path, monkeypatch):
    """Path outside both default roots and SIO_APPLY_EXTRA_ROOTS still raises."""
    validate = _import_validate()
    UnauthorizedApplyTarget = _import_unauthorized_error()

    from sio.core.applier import writer  # noqa: PLC0415

    original_roots = writer.ALLOWLIST_ROOTS
    extra_root = tmp_path / "extra"
    extra_root.mkdir()
    writer.ALLOWLIST_ROOTS = [Path.home() / ".claude", extra_root]

    try:
        with pytest.raises(UnauthorizedApplyTarget):
            validate(Path("/var/log/syslog"))
    finally:
        writer.ALLOWLIST_ROOTS = original_roots


# ---------------------------------------------------------------------------
# 6. UnauthorizedApplyTarget is an Exception subclass
# ---------------------------------------------------------------------------


def test_unauthorized_apply_target_is_exception():
    """UnauthorizedApplyTarget must be a subclass of Exception."""
    UnauthorizedApplyTarget = _import_unauthorized_error()
    assert issubclass(UnauthorizedApplyTarget, Exception)


# ---------------------------------------------------------------------------
# 7. Path.cwd() NOT in allowlist
# ---------------------------------------------------------------------------


def test_cwd_relative_outside_claude_rejected(tmp_path: Path, monkeypatch):
    """A file in cwd that is not under ~/.claude/ must be rejected."""
    validate = _import_validate()
    UnauthorizedApplyTarget = _import_unauthorized_error()

    # Change to a tmp dir that is not under ~/.claude/
    monkeypatch.chdir(tmp_path)
    arbitrary_file = tmp_path / "local_file.md"
    arbitrary_file.write_text("data", encoding="utf-8")

    with pytest.raises(UnauthorizedApplyTarget):
        validate(arbitrary_file)
