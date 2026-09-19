"""Manifest-tracked file staging shared by every file-copying harness adapter.

An adapter that stages SIO's bundled content into a harness config dir needs
the same four things regardless of the harness: a content hash per file, a
sidecar manifest (``.sio-managed.json``) recording what SIO installed, drift
detection (user-modified vs. SIO-managed), and a backup before any overwrite.

This module holds that logic once so ``claude_code.py`` and ``pi.py`` share
it instead of forking a ~90-line install/uninstall/status loop each.
Adapters stay responsible for *what* gets staged (which bootstrap files, any
per-harness transform) and *where* (the target path); this module handles
*how* (idempotence, manifest, drift, backup).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from sio.harnesses.base import InstallReport, StatusReport
from sio.core.paths import sio_home as _default_sio_home

MANIFEST_NAME = ".sio-managed.json"

#: One file to stage: (manifest key, absolute target path, text to write).
StagedFile = tuple[str, Path, str]


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "files": {}}


def save_manifest(path: Path, manifest: dict) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def backup_file(target: Path, config_dir: Path, report: InstallReport) -> None:
    """Copy ``target`` to ``~/.sio/backups/<utc-ts>/<path relative to config_dir>``."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = _default_sio_home() / "backups" / ts
    backup_path = backup_root / target.relative_to(config_dir)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup_path)
    report.add(backup_path, "backup", f"backup of {target}")


def stage_files(
    *,
    config_dir: Path,
    files: Iterable[StagedFile],
    report: InstallReport,
    dry_run: bool = False,
    force: bool = False,
    adopt_untracked: bool = True,
) -> None:
    """Write each staged file into place, tracked by the manifest in ``config_dir``.

    Per file:
      - absent            → create
      - identical         → skip ("already up-to-date")
      - tracked + drifted → user-modified: skip unless ``force`` (then backup + update)
      - untracked + differs:
          ``adopt_untracked=True``  → treat as stale SIO content: backup + update
                                      (Claude Code behaviour — survives a lost manifest)
          ``adopt_untracked=False`` → treat as foreign: skip unless ``force``
                                      (pi behaviour — a same-named skill the user or
                                      another tool put there is never overwritten)

    ``dry_run`` reports ``would-*`` actions and writes nothing, not even the
    manifest.
    """
    manifest_path = config_dir / MANIFEST_NAME
    manifest = load_manifest(manifest_path) if not dry_run else {"files": {}}

    if not dry_run:
        config_dir.mkdir(parents=True, exist_ok=True)

    for key, target, source_text in files:
        new_hash = hash_text(source_text)
        tracked = manifest.get("files", {}).get(key)

        # A symlink is never something SIO wrote, so a symlinked target
        # (possibly dangling) counts as "present" for the foreign check.
        present = target.exists() or target.is_symlink()
        if not present:
            action = "would-create" if dry_run else "create"
            report.add(target, action, "new")
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source_text, encoding="utf-8")
                _track(manifest, key, new_hash)
            continue

        existing_text = (
            target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        )
        existing_hash = hash_text(existing_text)
        if existing_hash == new_hash:
            report.add(target, "skip", "already up-to-date")
            continue

        if tracked is not None:
            user_modified = tracked.get("hash") != existing_hash
            if user_modified and not force:
                report.add(target, "skip", "user-modified (use --force to overwrite)")
                continue
        elif not adopt_untracked and not force:
            report.add(
                target,
                "skip",
                "exists but is not SIO-managed (use --force to overwrite)",
            )
            continue

        action = "would-update" if dry_run else "update"
        report.add(target, action, "content drift")
        if not dry_run:
            if target.exists():
                backup_file(target, config_dir, report)
            if target.is_symlink():
                # --force on a foreign symlink replaces the LINK, never the
                # file it points at (which SIO does not own either).
                target.unlink()
            target.write_text(source_text, encoding="utf-8")
            _track(manifest, key, new_hash)

    if not dry_run:
        save_manifest(manifest_path, manifest)


def remove_files(
    *,
    config_dir: Path,
    resolve_target: Callable[[str], Path],
    report: InstallReport,
    dry_run: bool = False,
) -> list[Path]:
    """Remove every manifest-tracked file that still matches its installed hash.

    Returns the paths removed (or that would be removed on dry-run) so the
    caller can prune now-empty directories it created. Files whose content
    no longer matches the manifest are left in place and reported.
    """
    manifest_path = config_dir / MANIFEST_NAME
    removed: list[Path] = []
    if not manifest_path.exists():
        report.errors.append(
            "no SIO manifest found — nothing to uninstall (was sio init ever run?)"
        )
        return removed
    manifest = load_manifest(manifest_path)

    for key, meta in manifest.get("files", {}).items():
        target = resolve_target(key)
        if not target.exists():
            continue
        existing_hash = hash_text(target.read_text(encoding="utf-8", errors="replace"))
        if existing_hash != meta.get("hash"):
            report.add(target, "skip", "user-modified — leaving in place")
            continue
        action = "would-remove" if dry_run else "remove"
        report.add(target, action)
        removed.append(target)
        if not dry_run:
            target.unlink()

    if not dry_run:
        manifest_path.unlink()
    return removed


def status_files(
    *,
    config_dir: Path,
    files: Iterable[StagedFile],
    report: StatusReport,
) -> None:
    """Classify each shipped file as installed / missing / drifted / foreign."""
    manifest = load_manifest(config_dir / MANIFEST_NAME)
    tracked = manifest.get("files", {})

    for key, target, source_text in files:
        new_hash = hash_text(source_text)
        if not target.exists():
            report.missing_files.append(target)
            continue
        existing_hash = hash_text(target.read_text(encoding="utf-8", errors="replace"))
        if existing_hash == new_hash:
            report.installed_files.append(target)
        elif key in tracked:
            report.drifted_files.append(target)
        else:
            report.notes.append(
                f"{target} exists but is not SIO-managed (created by user or another tool)"
            )


def _track(manifest: dict, key: str, new_hash: str) -> None:
    manifest.setdefault("files", {})[key] = {
        "hash": new_hash,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
