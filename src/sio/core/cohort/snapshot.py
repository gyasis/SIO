"""Config-hash snapshotter for the cohort primitive.

PRD: ``~/dev/prd/scratch/sio_autotag_experiments_2026-05-23.md`` §5 Q2.

Produces a JSON manifest of the four config dimensions that materially
affect agent behavior, then returns a sha256 hash of that manifest. Two
experiments with the same hash were run under identical config; a hash
diff is a structural signal that something the agent reads changed.

The four dimensions (Q2 decision — all four, not just CLAUDE.md):
  1. ``~/.claude/CLAUDE.md``                  (Tier 1 core rules)
  2. ``~/.claude/skills/`` (filenames + hash) (active skills)
  3. ``~/.claude/rules/`` (filenames + hash)  (Tier 2/3 rules)
  4. ``~/.claude/settings.json`` `hooks` key  (runtime hook wiring)

Missing files are recorded as ``null`` content rather than skipped so a
diff that adds/removes a file is still visible.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CLAUDE_HOME = Path.home() / ".claude"


def _hash_file(path: Path) -> str | None:
    """Return sha256 hex of file content, or None if missing/unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None


def _hash_dir(path: Path, suffixes: tuple[str, ...] = (".md",)) -> dict[str, str | None]:
    """Return {relative_path: sha256_hex} for files under ``path``.

    Recurses; sorts for deterministic JSON output. Suffix-filtered so that
    transient backups / .DS_Store don't perturb the hash.
    """
    if not path.is_dir():
        return {}
    out: dict[str, str | None] = {}
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        if suffixes and child.suffix not in suffixes:
            continue
        rel = str(child.relative_to(path))
        out[rel] = _hash_file(child)
    return out


def _settings_hooks_block(settings_path: Path) -> Any:
    """Extract the ``hooks`` key from settings.json, or None if absent."""
    try:
        data = json.loads(settings_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None
    return data.get("hooks")


def build_manifest(claude_home: Path | None = None) -> dict[str, Any]:
    """Build the JSON-serializable config manifest.

    Args:
        claude_home: Override ~/.claude lookup (test seam).

    Returns:
        A dict with four keys (``claude_md``, ``skills``, ``rules``,
        ``settings_hooks``). Stable across runs given the same inputs.
    """
    base = claude_home or CLAUDE_HOME
    return {
        "claude_md": _hash_file(base / "CLAUDE.md"),
        "skills": _hash_dir(base / "skills", suffixes=(".md",)),
        "rules": _hash_dir(base / "rules", suffixes=(".md",)),
        "settings_hooks": _settings_hooks_block(base / "settings.json"),
    }


def snapshot_hash(claude_home: Path | None = None) -> tuple[str, dict[str, Any]]:
    """Build the manifest and return (sha256_hex, manifest).

    The manifest is returned alongside the hash so callers may persist it
    for forensic diffs without recomputing.
    """
    manifest = build_manifest(claude_home=claude_home)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest, manifest
