"""Claude Code harness adapter.

Claude Code keeps user config under `~/.claude/`:
    skills/         per-skill directories (one folder per skill, with SKILL.md)
    rules/tools/    tool-specific rule markdown files
    hooks/          shell hook scripts (PreToolUse, PostToolUse, etc.)
    settings.json   the user's settings, including a hooks block

The adapter copies SIO's bundled bootstrap content into those locations.
File operations are idempotent: re-running `install()` is safe.
Drift detection uses a sidecar manifest at `~/.claude/.sio-managed.json`
so we can tell SIO-managed files from user-modified ones at uninstall time.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from sio.harnesses.base import HarnessAdapter, InstallReport, StatusReport
from sio.harnesses.bootstrap import iter_bootstrap_files

_MANIFEST_NAME = ".sio-managed.json"

# Hook events SIO registers in ~/.claude/settings.json. Each entry is
# (event_name, module_path); the command is dispatched via
# `<sys.executable> -m <module_path>` so the right Python interpreter
# fires regardless of which env the user installed SIO into.
_SIO_HOOK_DEFS: list[tuple[str, str]] = [
    ("PostToolUse", "sio.adapters.claude_code.hooks.post_tool_use"),
    ("PreCompact", "sio.adapters.claude_code.hooks.pre_compact"),
    ("Stop", "sio.adapters.claude_code.hooks.stop"),
    ("UserPromptSubmit", "sio.adapters.claude_code.hooks.user_prompt_submit"),
    ("SessionStart", "sio.adapters.claude_code.hooks.session_start"),
]


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "files": {}}


def _save_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


class ClaudeCodeAdapter(HarnessAdapter):
    name: ClassVar[str] = "claude-code"

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or (Path.home() / ".claude")

    # ------------------------------------------------------------------ detect
    def detect(self) -> bool:
        # Claude Code creates ~/.claude on first run. We require it to exist
        # so `sio init` (no --harness) doesn't auto-detect Claude Code on
        # systems that have never launched it; users on a fresh machine
        # can pass --harness claude-code explicitly to bootstrap eagerly.
        return self.config_dir.exists()

    # ------------------------------------------------------------------ install
    def install(self, *, dry_run: bool = False, force: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        manifest_path = self.config_dir / _MANIFEST_NAME
        manifest = _load_manifest(manifest_path) if not dry_run else {"files": {}}

        if not dry_run:
            self.config_dir.mkdir(parents=True, exist_ok=True)

        for src_path, rel_path, source_text in iter_bootstrap_files():
            target = self._resolve_target(rel_path)
            new_hash = _hash_text(source_text)
            tracked = manifest.get("files", {}).get(str(rel_path))

            if not target.exists():
                action = "would-create" if dry_run else "create"
                report.add(target, action, "new")
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source_text, encoding="utf-8")
                    manifest.setdefault("files", {})[str(rel_path)] = {
                        "hash": new_hash,
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    }
                continue

            existing_text = target.read_text(encoding="utf-8", errors="replace")
            existing_hash = _hash_text(existing_text)
            if existing_hash == new_hash:
                report.add(target, "skip", "already up-to-date")
                continue

            user_modified = (
                tracked is not None
                and tracked.get("hash") != existing_hash
            )
            if user_modified and not force:
                report.add(target, "skip", "user-modified (use --force to overwrite)")
                continue

            action = "would-update" if dry_run else "update"
            report.add(target, action, "content drift")
            if not dry_run:
                self._backup(target, report)
                target.write_text(source_text, encoding="utf-8")
                manifest.setdefault("files", {})[str(rel_path)] = {
                    "hash": new_hash,
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                }

        if not dry_run:
            _save_manifest(manifest_path, manifest)
        return report

    # ----------------------------------------------------------------- pre_install
    def pre_install(self, *, dry_run: bool = False) -> InstallReport:
        """Initialize the per-platform ``behavior_invocations.db``.

        Each harness writes its hook telemetry to a per-platform DB at
        ``~/.sio/<platform>/behavior_invocations.db`` (canonical
        ``~/.sio/sio.db`` is bootstrapped harness-agnostically before
        the adapter loop runs).

        Idempotent: re-running re-applies the in-place ALTER block in
        ``schema.py``, so any new column from a SIO upgrade lands on
        next install.
        """
        report = InstallReport(harness=self.name, dry_run=dry_run)

        sio_home = Path(
            os.environ.get("SIO_HOME", str(Path.home() / ".sio"))
        )
        platform_db_dir = sio_home / self.name  # e.g. ~/.sio/claude-code
        platform_db_path = platform_db_dir / "behavior_invocations.db"
        existed_before = platform_db_path.exists()

        if dry_run:
            action = "skip" if existed_before else "would-create"
            reason = (
                "per-platform DB already exists"
                if existed_before
                else "would initialize per-platform DB"
            )
            report.add(platform_db_path, action, reason)
            return report

        platform_db_dir.mkdir(parents=True, exist_ok=True)
        from sio.core.db.schema import init_db  # noqa: PLC0415

        conn = init_db(str(platform_db_path))
        conn.close()

        if existed_before:
            report.add(
                platform_db_path,
                "skip",
                "per-platform DB already exists — schema reverified",
            )
        else:
            report.add(
                platform_db_path,
                "create",
                "per-platform behavior_invocations DB initialized",
            )
        return report

    # ---------------------------------------------------------------- post_install
    def post_install(self, *, dry_run: bool = False) -> InstallReport:
        """Register SIO hooks in ``~/.claude/settings.json``.

        Merges with existing hook entries — never overwrites a user's
        unrelated hooks. Migrates legacy bare-format hook entries
        (``{"type": "command", "command": "..."}``) to the current
        wrapped format (``{"matcher": "", "hooks": [{...}]}``) on
        contact, so users upgrading from a pre-wrapped install pick up
        the right shape automatically.

        Idempotent: re-running this skips any hook whose module_path is
        already registered (matched in either format).

        On corrupt JSON, the existing settings.json is backed up to
        ``settings.json.bak`` before being replaced. Atomic write
        (tmp + rename) so an interrupted write cannot leave a
        zero-byte settings.json.
        """
        report = InstallReport(harness=self.name, dry_run=dry_run)
        settings_path = self.config_dir / "settings.json"

        # Read existing settings, recover from corruption with a backup
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                if not dry_run:
                    backup_path = settings_path.with_suffix(".json.bak")
                    shutil.copy2(settings_path, backup_path)
                    report.add(
                        backup_path,
                        "backup",
                        "settings.json was malformed JSON — backed up before reset",
                    )
                settings = {}
        else:
            settings = {}

        hooks = settings.setdefault("hooks", {})

        # Migrate legacy bare-format entries → wrapped format
        for event_name, event_hooks in list(hooks.items()):
            if not isinstance(event_hooks, list):
                continue
            for i, entry in enumerate(event_hooks):
                if (
                    isinstance(entry, dict)
                    and "type" in entry
                    and "command" in entry
                    and "hooks" not in entry
                ):
                    event_hooks[i] = {"matcher": "", "hooks": [entry]}

        # Register each SIO hook if not already present (match on
        # module_path so hooks reinstalled from a different Python env
        # are recognised as duplicates rather than appended).
        added = 0
        for event_name, module_path in _SIO_HOOK_DEFS:
            sio_hook_entry = {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{sys.executable} -m {module_path}",
                    }
                ],
            }
            event_hooks = hooks.setdefault(event_name, [])

            already = False
            for h in event_hooks:
                if not isinstance(h, dict):
                    continue
                if module_path in h.get("command", ""):
                    already = True
                    break
                for inner in h.get("hooks", []):
                    if isinstance(inner, dict) and module_path in inner.get(
                        "command", ""
                    ):
                        already = True
                        break
                if already:
                    break

            if not already:
                event_hooks.append(sio_hook_entry)
                added += 1
                action = "would-create" if dry_run else "create"
                report.add(
                    settings_path,
                    action,
                    f"register {event_name} hook → {module_path}",
                )

        if added == 0:
            report.add(
                settings_path,
                "skip",
                f"all {len(_SIO_HOOK_DEFS)} SIO hooks already registered",
            )
        elif not dry_run:
            # Atomic write — tmp + rename so an interrupt can't leave
            # a zero-byte settings.json
            tmp_path = settings_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(settings, indent=2), encoding="utf-8"
            )
            tmp_path.replace(settings_path)

        # Record install metadata in platform_config (per-platform DB).
        # This row is what `sio status` and the doctor / health surfaces
        # read to know "claude-code is installed, hooks=1, skills=1, etc."
        # INSERT OR REPLACE so re-running is idempotent and updates the
        # installed_at timestamp on every successful install.
        sio_home = Path(
            os.environ.get("SIO_HOME", str(Path.home() / ".sio"))
        )
        platform_db_path = sio_home / self.name / "behavior_invocations.db"

        if dry_run:
            report.add(
                platform_db_path,
                "would-update",
                f"would record platform_config row for {self.name}",
            )
        elif platform_db_path.exists():
            try:
                from sio.core.db.connect import open_db  # noqa: PLC0415

                with open_db(platform_db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO platform_config "
                        "(platform, db_path, hooks_installed, skills_installed, "
                        "config_updated, capability_tier, installed_at) "
                        "VALUES (?, ?, 1, 1, 1, 1, ?)",
                        (
                            self.name,
                            str(platform_db_path),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                report.add(
                    platform_db_path,
                    "update",
                    f"platform_config row recorded for {self.name}",
                )
            except Exception as exc:  # noqa: BLE001
                report.errors.append(
                    f"platform_config write failed for {self.name}: {exc}"
                )
        else:
            # Per-platform DB doesn't exist — pre_install was likely
            # skipped (e.g. tests stubbing it out). Surface, don't fail.
            report.errors.append(
                f"platform_config write skipped: {platform_db_path} does not exist "
                f"(was pre_install run?)"
            )

        return report

    # ---------------------------------------------------------------- uninstall
    def uninstall(self, *, dry_run: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        manifest_path = self.config_dir / _MANIFEST_NAME
        if not manifest_path.exists():
            report.errors.append(
                "no SIO manifest found — nothing to uninstall (was sio init ever run?)"
            )
            return report
        manifest = _load_manifest(manifest_path)

        for rel_path_str, meta in manifest.get("files", {}).items():
            target = self._resolve_target(Path(rel_path_str))
            if not target.exists():
                continue
            existing_hash = _hash_text(target.read_text(encoding="utf-8", errors="replace"))
            if existing_hash != meta.get("hash"):
                report.add(target, "skip", "user-modified — leaving in place")
                continue
            action = "would-remove" if dry_run else "remove"
            report.add(target, action)
            if not dry_run:
                target.unlink()

        if not dry_run:
            manifest_path.unlink()
        return report

    # -------------------------------------------------------------------- status
    def status(self) -> StatusReport:
        report = StatusReport(
            harness=self.name,
            detected=self.detect(),
            config_dir=self.config_dir,
        )
        manifest_path = self.config_dir / _MANIFEST_NAME
        manifest = _load_manifest(manifest_path)
        tracked = manifest.get("files", {})

        for _src, rel_path, source_text in iter_bootstrap_files():
            target = self._resolve_target(rel_path)
            new_hash = _hash_text(source_text)
            if not target.exists():
                report.missing_files.append(target)
                continue
            existing_hash = _hash_text(target.read_text(encoding="utf-8", errors="replace"))
            if existing_hash == new_hash:
                report.installed_files.append(target)
            else:
                if str(rel_path) in tracked:
                    report.drifted_files.append(target)
                else:
                    report.notes.append(
                        f"{target} exists but is not SIO-managed (created by user or another tool)"
                    )

        return report

    # --------------------------------------------------------------- internals
    def _resolve_target(self, rel_path: Path) -> Path:
        """Map a bootstrap relative path to its location under ~/.claude/."""
        # Bootstrap layout (inside the package) mirrors the target layout:
        #   _bootstrap/skills/sio-recall/SKILL.md   →  ~/.claude/skills/sio-recall/SKILL.md
        #   _bootstrap/rules/tools/sio.md           →  ~/.claude/rules/tools/sio.md
        return self.config_dir / rel_path

    def _backup(self, target: Path, report: InstallReport) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root = Path.home() / ".sio" / "backups" / ts
        backup_path = backup_root / target.relative_to(self.config_dir)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        report.add(backup_path, "backup", f"backup of {target}")
