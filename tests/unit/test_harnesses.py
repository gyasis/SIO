"""Unit tests for the harness adapter layer.

Covers:
- registry / get_adapter / detect_adapters
- ClaudeCodeAdapter install / uninstall / status / dry-run / force
- Stub adapters (cursor, windsurf, opencode) report not-implemented gracefully
- Drift detection: user-modified files are preserved without --force
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sio.harnesses import (
    ALL_ADAPTERS,
    ClaudeCodeAdapter,
    CursorAdapter,
    OpenCodeAdapter,
    WindsurfAdapter,
    detect_adapters,
    get_adapter,
)


class TestRegistry:
    def test_all_four_adapters_registered(self) -> None:
        names = {cls.name for cls in ALL_ADAPTERS}
        assert names == {"claude-code", "cursor", "windsurf", "opencode"}

    def test_get_adapter_by_name(self) -> None:
        a = get_adapter("claude-code")
        assert isinstance(a, ClaudeCodeAdapter)

    def test_get_adapter_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown harness"):
            get_adapter("not-a-harness")


class TestStubAdapters:
    @pytest.mark.parametrize("cls", [CursorAdapter, WindsurfAdapter, OpenCodeAdapter])
    def test_stub_install_reports_not_implemented(self, cls, tmp_path: Path) -> None:
        adapter = cls(config_dir=tmp_path / "cfg")
        report = adapter.install()
        assert not report.success
        assert any("not yet implemented" in e for e in report.errors)

    @pytest.mark.parametrize("cls", [CursorAdapter, WindsurfAdapter, OpenCodeAdapter])
    def test_stub_status_notes_stub(self, cls, tmp_path: Path) -> None:
        adapter = cls(config_dir=tmp_path / "cfg")
        sr = adapter.status()
        assert any("not yet implemented" in n for n in sr.notes)


class TestClaudeCodeAdapterInstall:
    def _adapter(self, tmp_path: Path) -> ClaudeCodeAdapter:
        return ClaudeCodeAdapter(config_dir=tmp_path / ".claude")

    def test_install_creates_files_and_manifest(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        report = adapter.install()
        assert report.success
        assert report.changes, "expected at least one bootstrap file to be staged"
        # All non-skip changes should be 'create' on a fresh install.
        for ch in report.changes:
            assert ch.action in ("create", "skip"), ch
        manifest = tmp_path / ".claude" / ".sio-managed.json"
        assert manifest.exists()
        data = json.loads(manifest.read_text())
        assert data["files"], "manifest must record installed files"

    def test_dry_run_makes_no_writes(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        report = adapter.install(dry_run=True)
        assert report.dry_run is True
        # No actual files should land in the config dir.
        config_dir = tmp_path / ".claude"
        if config_dir.exists():
            assert not (config_dir / ".sio-managed.json").exists()
        # Each change must be tagged as 'would-...'.
        for ch in report.changes:
            assert ch.action.startswith("would-"), ch

    def test_install_is_idempotent(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        report2 = adapter.install()
        # Second run must not re-create or update anything that hasn't drifted.
        for ch in report2.changes:
            assert ch.action == "skip", f"expected skip on idempotent re-run, got {ch}"

    def test_user_modified_file_is_skipped_without_force(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        # Pick the first installed file and tamper with it.
        target = next(
            iter(p for p in (tmp_path / ".claude").rglob("*") if p.is_file() and p.name != ".sio-managed.json")
        )
        target.write_text("USER EDITED CONTENT — do not overwrite\n")
        report = adapter.install()
        skip_for_target = [
            ch for ch in report.changes if ch.path == target and ch.action == "skip"
        ]
        assert skip_for_target, f"expected user-modified {target} to be skipped"
        # Content must not have been overwritten.
        assert target.read_text().startswith("USER EDITED CONTENT")

    def test_force_overwrites_user_modified_with_backup(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        target = next(
            iter(p for p in (tmp_path / ".claude").rglob("*") if p.is_file() and p.name != ".sio-managed.json")
        )
        target.write_text("USER EDITED — will be overwritten with --force\n")
        report = adapter.install(force=True)
        update_for_target = [
            ch for ch in report.changes if ch.path == target and ch.action == "update"
        ]
        assert update_for_target, f"expected --force to update {target}"
        # A backup change must have been recorded.
        assert any(ch.action == "backup" for ch in report.changes)

    def test_status_reports_after_install(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        sr = adapter.status()
        assert sr.detected
        assert sr.installed_files, "expected installed_files to be non-empty after install"
        assert not sr.missing_files
        assert not sr.drifted_files

    def test_uninstall_removes_managed_files_only(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        # Add an unrelated user file in the same dir tree — must NOT be touched.
        user_file = tmp_path / ".claude" / "skills" / "user-only" / "SKILL.md"
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text("user owns this")

        report = adapter.uninstall()
        assert report.success
        # Manifest gone, user file untouched.
        assert not (tmp_path / ".claude" / ".sio-managed.json").exists()
        assert user_file.exists()
        assert user_file.read_text() == "user owns this"

    def test_uninstall_with_no_manifest_errors_clearly(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        report = adapter.uninstall()
        assert not report.success
        assert any("no SIO manifest" in e for e in report.errors)


class TestDetectAdapters:
    def test_detect_returns_only_present_harnesses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Point HOME at an empty tmp dir so none of the real harness dirs exist.
        monkeypatch.setenv("HOME", str(tmp_path))
        # Re-evaluate: with HOME=tmp_path and no harness dirs created, none
        # of the detect() calls should return True.
        detected = detect_adapters()
        assert detected == []
