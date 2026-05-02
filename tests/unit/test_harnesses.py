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


class TestSeedSioHome:
    """Verify ~/.sio/ data dir + config.toml seeding (gap from v0.1.0 fresh install)."""

    def test_creates_data_dir_subdirs_and_config(self, tmp_path: Path) -> None:
        from sio.harnesses.bootstrap import seed_sio_home

        sio_home = tmp_path / ".sio"
        report = seed_sio_home(sio_home=sio_home)

        assert sio_home.is_dir()
        for sub in ("datasets", "previews", "backups", "ground_truth", "optimized"):
            assert (sio_home / sub).is_dir(), f"missing {sub}/"
        cfg = sio_home / "config.toml"
        assert cfg.is_file()
        text = cfg.read_text()
        assert "Quick start" in text
        # All provider lines must be commented out by default — installs
        # should never silently dispatch to a provider the user didn't
        # explicitly opt into.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("model =") or stripped.startswith("api_key_env ="):
                pytest.fail(f"shipped config.toml has un-commented provider line: {line!r}")
        # 7 actions: data dir + 5 subdirs + config.toml
        assert len(report.actions) == 7

    def test_does_not_overwrite_existing_config(self, tmp_path: Path) -> None:
        from sio.harnesses.bootstrap import seed_sio_home

        sio_home = tmp_path / ".sio"
        sio_home.mkdir()
        cfg = sio_home / "config.toml"
        cfg.write_text("# user-edited content — do not clobber\n")

        report = seed_sio_home(sio_home=sio_home)
        assert cfg.read_text() == "# user-edited content — do not clobber\n"
        # The config skip must be reported.
        assert any(action == "skip" and path == cfg for action, path, _ in report.actions)

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        from sio.harnesses.bootstrap import seed_sio_home

        sio_home = tmp_path / ".sio"
        report = seed_sio_home(sio_home=sio_home, dry_run=True)
        assert report.dry_run is True
        # Nothing should hit disk on a dry run.
        assert not sio_home.exists()
        # Each action must be tagged 'would-create'.
        for action, _, _ in report.actions:
            assert action == "would-create" or action == "skip"

    def test_idempotent_re_run(self, tmp_path: Path) -> None:
        from sio.harnesses.bootstrap import seed_sio_home

        sio_home = tmp_path / ".sio"
        seed_sio_home(sio_home=sio_home)
        first_config = (sio_home / "config.toml").read_text()
        report2 = seed_sio_home(sio_home=sio_home)
        # Second run is all skips.
        assert all(action == "skip" for action, _, _ in report2.actions)
        assert (sio_home / "config.toml").read_text() == first_config


class TestPathLink:
    """`sio init --link-path` shell PATH integration."""

    def test_link_creates_managed_block(self, tmp_path: Path) -> None:
        from sio.harnesses.path_link import link_path

        rc = tmp_path / ".zshrc"
        rc.write_text("# pre-existing user content\n")
        scripts = tmp_path / "venv" / "bin"
        scripts.mkdir(parents=True)

        report = link_path(rc_file=rc, scripts_dir=scripts)
        assert report.action == "create"
        text = rc.read_text()
        assert "# pre-existing user content" in text
        assert "# >>> sio managed-path >>>" in text
        assert f'export PATH="{scripts}:$PATH"' in text
        assert "# <<< sio managed-path <<<" in text

    def test_link_is_idempotent(self, tmp_path: Path) -> None:
        from sio.harnesses.path_link import link_path

        rc = tmp_path / ".bashrc"
        rc.write_text("")
        scripts = tmp_path / "venv" / "bin"
        scripts.mkdir(parents=True)

        link_path(rc_file=rc, scripts_dir=scripts)
        first = rc.read_text()
        report2 = link_path(rc_file=rc, scripts_dir=scripts)
        assert report2.action == "skip"
        assert rc.read_text() == first

    def test_unlink_removes_block_only(self, tmp_path: Path) -> None:
        from sio.harnesses.path_link import link_path, unlink_path

        rc = tmp_path / ".zshrc"
        rc.write_text("# user content above\n")
        scripts = tmp_path / "venv" / "bin"
        scripts.mkdir(parents=True)

        link_path(rc_file=rc, scripts_dir=scripts)
        # Add user content AFTER the block
        with rc.open("a") as f:
            f.write("# user content below\n")

        report = unlink_path(rc_file=rc)
        assert report.action == "remove"
        text = rc.read_text()
        assert "# user content above" in text
        assert "# user content below" in text
        assert "# >>> sio managed-path >>>" not in text
        assert "managed-path" not in text

    def test_unlink_when_no_block_present(self, tmp_path: Path) -> None:
        from sio.harnesses.path_link import unlink_path

        rc = tmp_path / ".zshrc"
        rc.write_text("# user-only file\n")
        report = unlink_path(rc_file=rc)
        assert report.action == "skip-not-managed"
        # File untouched.
        assert rc.read_text() == "# user-only file\n"

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        from sio.harnesses.path_link import link_path

        rc = tmp_path / ".zshrc"
        scripts = tmp_path / "venv" / "bin"
        scripts.mkdir(parents=True)

        report = link_path(rc_file=rc, scripts_dir=scripts, dry_run=True)
        assert report.action == "would-create"
        assert not rc.exists()


class TestBootstrapMissingError:
    """C2 — `sio init` must hard-fail rather than silently no-op."""

    def test_iter_bootstrap_files_raises_when_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sio.harnesses import bootstrap as bs

        monkeypatch.setattr(bs, "_BOOTSTRAP_PKG", "non.existent.pkg")
        # Force the dev-fallback to also fail by pointing it at empty space.
        monkeypatch.setattr(bs, "_repo_root_fallback", lambda: tmp_path / "nope")
        with pytest.raises(bs.BootstrapMissingError) as exc_info:
            list(bs.iter_bootstrap_files())
        assert "force-reinstall" in str(exc_info.value)

    def test_collect_bootstrap_files_returns_empty_without_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sio.harnesses import bootstrap as bs

        monkeypatch.setattr(bs, "_BOOTSTRAP_PKG", "non.existent.pkg")
        monkeypatch.setattr(bs, "_repo_root_fallback", lambda: tmp_path / "nope")
        # The internal API used by tests stays generator-empty rather than raising.
        assert list(bs._collect_bootstrap_files()) == []


class TestDetectAdapters:
    def test_detect_returns_only_present_harnesses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Point HOME at an empty tmp dir so none of the real harness dirs exist.
        monkeypatch.setenv("HOME", str(tmp_path))
        # Re-evaluate: with HOME=tmp_path and no harness dirs created, none
        # of the detect() calls should return True.
        detected = detect_adapters()
        assert detected == []
