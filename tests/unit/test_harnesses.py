"""Unit tests for the harness adapter layer.

Covers:
- registry / get_adapter / detect_adapters
- ClaudeCodeAdapter install / uninstall / status / dry-run / force
- PiAdapter: skills-only install, foreign skills untouched, rules/hooks
  reported unsupported, SKILL.md conformance transform for pi's validator
- Stub adapters (cursor, windsurf, opencode) report not-implemented gracefully
- Drift detection: user-modified files are preserved without --force

No test here may write to the real ~/.pi, ~/.claude or ~/.sio: every adapter
gets a ``config_dir`` under ``tmp_path`` and anything that reaches
``Path.home()`` (backups) runs under a monkeypatched HOME.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sio.harnesses import (
    ALL_ADAPTERS,
    ClaudeCodeAdapter,
    CursorAdapter,
    OpenCodeAdapter,
    PiAdapter,
    WindsurfAdapter,
    detect_adapters,
    get_adapter,
)


class TestRegistry:
    def test_all_adapters_registered(self) -> None:
        names = {cls.name for cls in ALL_ADAPTERS}
        assert names == {"claude-code", "pi", "cursor", "windsurf", "opencode"}

    def test_get_pi_adapter_by_name(self) -> None:
        assert isinstance(get_adapter("pi"), PiAdapter)

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

    def test_force_overwrites_user_modified_with_backup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _backup() writes to Path.home()/.sio/backups/<ts>/ -- without this the test
        # left a real backup of its fixture in the user's ~/.sio on every run.
        fake_home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(fake_home))
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
        # A backup change must have been recorded, holding the user's edit, and it
        # must live under the (fake) home -- never the real ~/.sio.
        backups = [ch.path for ch in report.changes if ch.action == "backup"]
        assert backups, "expected --force to record a backup"
        assert all(fake_home / ".sio" / "backups" in b.parents for b in backups), backups
        assert any(b.read_text().startswith("USER EDITED") for b in backups)

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


_PI_SKILLS_JS = (
    Path.home()
    / ".local/lib/node_modules/@earendil-works/pi-coding-agent/dist/core/skills.js"
)


def _fake_bootstrap(entries: list[tuple[str, str]]):
    """Build an ``iter_bootstrap_files`` stand-in from ``(rel_path, text)`` pairs."""

    def _iter():
        for rel, text in entries:
            yield Path("/bundled") / rel, Path(rel), text

    return _iter


class TestPiAdapter:
    def _adapter(self, tmp_path: Path) -> PiAdapter:
        return PiAdapter(config_dir=tmp_path / ".pi" / "agent")

    def _first_skill_md(self, adapter: PiAdapter) -> Path:
        return next(iter(sorted((adapter.config_dir / "skills").rglob("SKILL.md"))))

    def test_default_config_dir_honours_pi_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PI_CODING_AGENT_DIR", "/tmp/elsewhere/agent")
        assert PiAdapter().config_dir == Path("/tmp/elsewhere/agent")
        monkeypatch.delenv("PI_CODING_AGENT_DIR")
        monkeypatch.setenv("HOME", "/tmp/fake-home")
        assert PiAdapter().config_dir == Path("/tmp/fake-home/.pi/agent")

    def test_install_stages_skills_only_with_manifest(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        report = adapter.install()
        assert report.success
        created = [ch.path for ch in report.changes if ch.action == "create"]
        assert created, "expected bundled skills to be staged"
        skills_root = adapter.config_dir / "skills"
        for p in created:
            assert skills_root in p.parents, f"{p} staged outside skills/"
        # Every staged skill is <skills>/<name>/SKILL.md (+ optional siblings).
        assert all((d / "SKILL.md").is_file() for d in skills_root.iterdir() if d.is_dir())
        # Nothing rules-shaped, no root-level README, no AGENTS.md touched.
        assert not (adapter.config_dir / "rules").exists()
        assert not (skills_root / "README.md").exists()
        assert not (adapter.config_dir / "AGENTS.md").exists()
        manifest = json.loads((adapter.config_dir / ".sio-managed.json").read_text())
        assert manifest["files"]
        assert all(k.startswith("skills/") for k in manifest["files"])

    def test_rules_and_hooks_reported_unsupported_not_silent(self, tmp_path: Path) -> None:
        report = self._adapter(tmp_path).install()
        joined = "\n".join(report.notes)
        assert "rule file(s) not supported on pi" in joined
        assert "hook telemetry not supported on pi" in joined
        assert "AGENTS.md" in joined  # says WHY rules are not installed

    def test_dry_run_makes_no_writes(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        report = adapter.install(dry_run=True)
        assert report.dry_run is True
        assert not adapter.config_dir.exists()
        assert report.changes
        assert all(ch.action.startswith("would-") for ch in report.changes)

    def test_install_is_idempotent(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        report2 = adapter.install()
        assert report2.changes
        assert all(ch.action == "skip" for ch in report2.changes), report2.changes

    def test_preexisting_foreign_skill_dirs_untouched(self, tmp_path: Path) -> None:
        """Skills SIO did not install — real files AND symlinks — are never modified."""
        adapter = self._adapter(tmp_path)
        skills = adapter.config_dir / "skills"
        foreign = skills / "promptchain" / "SKILL.md"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("---\nname: promptchain\ndescription: theirs\n---\nbody\n")
        linked_src = tmp_path / "elsewhere" / "SKILL.md"
        linked_src.parent.mkdir(parents=True)
        linked_src.write_text("---\nname: toolbelt\ndescription: linked\n---\n")
        (skills / "toolbelt").mkdir()
        (skills / "toolbelt" / "SKILL.md").symlink_to(linked_src)

        report = adapter.install()
        assert report.success
        assert foreign.read_text().startswith("---\nname: promptchain")
        assert (skills / "toolbelt" / "SKILL.md").is_symlink()
        assert linked_src.read_text() == "---\nname: toolbelt\ndescription: linked\n---\n"
        touched = {ch.path for ch in report.changes}
        assert foreign not in touched
        assert (skills / "toolbelt" / "SKILL.md") not in touched
        # ...and the report names them, so the boundary is visible (also on dry-run).
        assert any(
            "2 existing skill(s) not managed by SIO" in n and "promptchain, toolbelt" in n
            for n in report.notes
        ), report.notes
        dry = adapter.install(dry_run=True)
        assert any("promptchain, toolbelt" in n for n in dry.notes)

    def test_same_named_untracked_skill_is_skipped_without_force(self, tmp_path: Path) -> None:
        """A SKILL.md with an SIO name that SIO did NOT install is foreign, not stale."""
        adapter = self._adapter(tmp_path)
        preview = adapter.install(dry_run=True)
        target = next(ch.path for ch in preview.changes if ch.path.name == "SKILL.md")
        target.parent.mkdir(parents=True)
        target.write_text("---\nname: mine\ndescription: user-authored\n---\n")
        report = adapter.install()
        skip = [ch for ch in report.changes if ch.path == target]
        assert skip and skip[0].action == "skip"
        assert "not SIO-managed" in skip[0].reason
        assert target.read_text().startswith("---\nname: mine")

    def test_user_modified_skipped_without_force(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        target = self._first_skill_md(adapter)
        target.write_text("USER EDITED CONTENT — do not overwrite\n")
        report = adapter.install()
        skip = [ch for ch in report.changes if ch.path == target and ch.action == "skip"]
        assert skip and "user-modified" in skip[0].reason
        assert target.read_text().startswith("USER EDITED CONTENT")

    def test_force_backs_up_under_fake_home_then_overwrites(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(fake_home))
        adapter = self._adapter(tmp_path)
        adapter.install()
        target = self._first_skill_md(adapter)
        target.write_text("USER EDITED — will be overwritten with --force\n")
        report = adapter.install(force=True)
        assert any(ch.path == target and ch.action == "update" for ch in report.changes)
        backups = [ch.path for ch in report.changes if ch.action == "backup"]
        assert backups
        assert all(fake_home / ".sio" / "backups" in b.parents for b in backups), backups
        assert any(b.read_text().startswith("USER EDITED") for b in backups)
        assert not target.read_text().startswith("USER EDITED")

    def test_status_reports_after_install(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        adapter.install()
        sr = adapter.status()
        assert sr.detected
        assert sr.installed_files
        assert not sr.missing_files
        assert not sr.drifted_files
        assert any("not supported on pi" in n for n in sr.notes)

    def test_uninstall_removes_tracked_files_only_and_prunes_dirs(self, tmp_path: Path) -> None:
        adapter = self._adapter(tmp_path)
        skills = adapter.config_dir / "skills"
        foreign = skills / "two-paragraph-takeaway" / "SKILL.md"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("theirs")
        adapter.install()
        edited = self._first_skill_md(adapter)
        edited.write_text("user edited after install\n")

        report = adapter.uninstall()
        assert report.success
        assert not (adapter.config_dir / ".sio-managed.json").exists()
        assert foreign.read_text() == "theirs"
        assert edited.read_text() == "user edited after install\n"
        remaining = {d.name for d in skills.iterdir()}
        assert remaining == {"two-paragraph-takeaway", edited.parent.name}, remaining

    def test_uninstall_without_manifest_errors_clearly(self, tmp_path: Path) -> None:
        report = self._adapter(tmp_path).uninstall()
        assert not report.success
        assert any("no SIO manifest" in e for e in report.errors)

    def test_bundled_skills_already_conform(self) -> None:
        """Ship-time guard: no bundled SKILL.md needs a transform for pi."""
        from sio.harnesses.bootstrap import iter_bootstrap_files
        from sio.harnesses.pi import conform_skill_for_pi

        for _src, rel, text in iter_bootstrap_files():
            if rel.name == "SKILL.md":
                out, notes = conform_skill_for_pi(rel.parts[1], text)
                assert notes == [], f"{rel}: {notes}"
                assert out == text

    def test_conform_transforms_and_reports(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from sio.harnesses import pi as pi_mod

        long_desc = "x" * 1100
        entries = [
            ("skills/sio-long/SKILL.md", f"---\nname: sio-long\ndescription: {long_desc}\n---\nbody\n"),
            ("skills/sio-badname/SKILL.md", "---\nname: SIO_Bad!\ndescription: ok\n---\nbody\n"),
            ("skills/sio-nodesc/SKILL.md", "---\nname: sio-nodesc\nrequires: sio\n---\n# First line\ntext\n"),
            ("skills/sio-nofm/SKILL.md", "# Heading only\nno frontmatter\n"),
            ("skills/sio-block/SKILL.md", "---\nname: sio-block\ndescription: >\n  " + " ".join(["w"] * 600) + "\n---\nb\n"),
            ("skills/sio-fine/SKILL.md", "---\nname: sio-fine\ndescription: fine\nrequires: sio\n---\nb\n"),
            ("rules/tools/sio.md", "# rule\n"),
        ]
        monkeypatch.setattr(pi_mod, "iter_bootstrap_files", _fake_bootstrap(entries))
        adapter = self._adapter(tmp_path)
        report = adapter.install()
        assert report.success
        skills = adapter.config_dir / "skills"

        def fm(name: str) -> str:
            return (skills / name / "SKILL.md").read_text().split("---")[1]

        assert 'description: "' in fm("sio-long") and fm("sio-long").rstrip().endswith('…"')
        assert "name: sio-badname" in fm("sio-badname")  # falls back to the dir name
        assert 'description: "First line"' in fm("sio-nodesc")
        assert "name: sio-nofm" in fm("sio-nofm") and "description:" in fm("sio-nofm")
        assert len(fm("sio-block")) < 1100
        assert (skills / "sio-fine" / "SKILL.md").read_text() == entries[5][1]  # verbatim
        transforms = [n for n in report.notes if n.startswith("pi-conformance transform:")]
        assert len(transforms) == 5, transforms
        assert any("sio-long: description 1100 chars" in n for n in transforms)
        assert any("sio-badname: name 'SIO_Bad!' invalid" in n for n in transforms)
        assert any("1 bundled rule file(s) not supported on pi" in n for n in report.notes)

    @pytest.mark.skipif(
        not _PI_SKILLS_JS.exists() or shutil.which("node") is None,
        reason="pi coding agent (or node) not installed here",
    )
    def test_transformed_skills_pass_pis_own_validator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Install synthetic non-conforming skills, then load them with pi's skills.js."""
        from sio.harnesses import pi as pi_mod

        entries = [
            ("skills/sio-long/SKILL.md", "---\nname: sio-long\ndescription: " + "y" * 2000 + "\n---\nb\n"),
            ("skills/sio-badname/SKILL.md", "---\nname: Bad Name\ndescription: ok\n---\nb\n"),
            ("skills/sio-nodesc/SKILL.md", "---\nname: sio-nodesc\n---\n# Only a heading\n"),
        ]
        monkeypatch.setattr(pi_mod, "iter_bootstrap_files", _fake_bootstrap(entries))
        adapter = self._adapter(tmp_path)
        adapter.install()
        script = tmp_path / "check.mjs"
        script.write_text(
            f'import {{ loadSkills }} from "{_PI_SKILLS_JS}";\n'
            f'const r = loadSkills({{ cwd: "{tmp_path / "empty-cwd"}", '
            f'agentDir: "{adapter.config_dir}", skillPaths: [], includeDefaults: true }});\n'
            "console.log(JSON.stringify({ names: r.skills.map(s => s.name), "
            "diagnostics: r.diagnostics }));\n"
        )
        out = subprocess.run(
            ["node", str(script)], capture_output=True, text=True, check=True, timeout=60
        )
        result = json.loads(out.stdout.strip().splitlines()[-1])
        assert result["diagnostics"] == [], result
        assert sorted(result["names"]) == ["sio-badname", "sio-long", "sio-nodesc"]


class TestSeedSioHome:
    """Verify ~/.sio/ data dir + config.toml seeding (gap from v0.1.0 fresh install)."""

    def test_creates_data_dir_subdirs_and_config(self, tmp_path: Path) -> None:
        from sio.harnesses.bootstrap import seed_sio_home

        sio_home = tmp_path / ".sio"
        report = seed_sio_home(sio_home=sio_home)

        assert sio_home.is_dir()
        # v0.1.4: docs/ subdir added for offline docs staging (~/.sio/docs/).
        for sub in (
            "datasets",
            "previews",
            "backups",
            "ground_truth",
            "optimized",
            "docs",
        ):
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
        # 8 actions: data dir + 6 subdirs + config.toml.
        # (Docs staging adds 0 actions in dev installs where docs aren't
        # bundled into sio/_bootstrap/docs/; the wheel ships them via
        # [tool.hatch.build.targets.wheel.force-include] and the staging
        # block then adds one action per docs file.)
        assert len(report.actions) == 8

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
