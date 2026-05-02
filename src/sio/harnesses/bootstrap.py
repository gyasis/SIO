"""Locate and read SIO's bundled bootstrap content (skills + rules).

The bootstrap files are bundled inside the `sio` package at install time
(see `[tool.hatch.build.targets.wheel.force-include]` in pyproject.toml).
At runtime we read them via `importlib.resources` so installs work
regardless of whether the wheel was unpacked into site-packages, run from
a zipapp, or imported from the source tree during development.

Layout inside the installed package:
    sio/_bootstrap/skills/<skill-name>/SKILL.md  (and any sibling files)
    sio/_bootstrap/rules/tools/sio.md            (and other tool rules)

The relative paths returned mirror the layout the harness adapter writes
into the user's config dir, so adapters can stay layout-aware without
re-encoding the conventions.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib import resources
from pathlib import Path

_BOOTSTRAP_PKG = "sio._bootstrap"


def _walk_resources(anchor) -> Iterator[tuple[str, str]]:
    """Recursively yield (rel_path_string, text_content) under a Traversable anchor."""
    for entry in anchor.iterdir():
        if entry.is_dir():
            for child_rel, child_text in _walk_resources(entry):
                yield f"{entry.name}/{child_rel}", child_text
        else:
            try:
                text = entry.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Skip binary or unreadable files — bootstrap is text-only.
                continue
            yield entry.name, text


def _iter_from_disk(root: Path) -> Iterator[tuple[Path, Path, str]]:
    """Walk a real on-disk source tree (used in dev / editable installs).

    Yields paths relative to `root.parent`, so the caller passing
    ``repo / "skills"`` gets back ``skills/<sub>/<file>`` — matching the
    layout the bundled package version produces.
    """
    if not root.exists():
        return
    prefix = root.name
    for src in sorted(root.rglob("*")):
        if not src.is_file():
            continue
        try:
            text = src.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = Path(prefix) / src.relative_to(root)
        yield src, rel, text


def _repo_root_fallback() -> Path | None:
    """Locate the repo's ./skills and ./rules trees when running editably.

    The wheel build copies these into `sio/_bootstrap/` via hatch's
    `force-include`, but in an editable install or running straight from
    a clone the bundled package data isn't present. Walking up from this
    file's location finds the repo root and lets `sio init` work in dev.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "skills").is_dir() and (parent / "rules").is_dir():
            return parent
    return None


def iter_bootstrap_files() -> Iterator[tuple[Path, Path, str]]:
    """Yield `(absolute_source_path, relative_target_path, text_content)` tuples.

    `relative_target_path` is the path *under the harness config dir* where
    the file should land — e.g. `Path("skills/sio-recall/SKILL.md")` maps
    to `~/.claude/skills/sio-recall/SKILL.md` for the Claude Code adapter.

    Resolution order:
        1. Bundled package data at `sio._bootstrap` (the wheel/sdist case)
        2. Repo-root `skills/` + `rules/` (the editable / from-clone case)
    """
    yielded_any = False
    try:
        pkg_root = resources.files(_BOOTSTRAP_PKG)
        for top in pkg_root.iterdir():
            if not top.is_dir():
                continue
            for rel, text in _walk_resources(top):
                rel_path = Path(top.name) / rel
                try:
                    src_path = Path(str(top / rel))
                except (TypeError, ValueError):
                    src_path = Path("<bundled>") / rel_path
                yielded_any = True
                yield src_path, rel_path, text
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    if yielded_any:
        return

    # Editable / from-clone fallback.
    repo = _repo_root_fallback()
    if repo is None:
        return
    for top_name in ("skills", "rules"):
        yield from _iter_from_disk(repo / top_name) if (repo / top_name).is_dir() else []
