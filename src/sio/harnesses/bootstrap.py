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

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

_BOOTSTRAP_PKG = "sio._bootstrap"

# ---------------------------------------------------------------------------
# ~/.sio/ data dir bootstrap (config.toml + subdirs)
# ---------------------------------------------------------------------------

# Default config.toml seeded into a fresh install. Every provider block is
# commented out so the install can never silently dispatch to the wrong LM
# — the user must uncomment one block before `sio suggest` will succeed.
# `lm_factory` raises a clear "set llm.model in ~/.sio/config.toml" when
# nothing is configured, so the failure mode is loud.
_DEFAULT_CONFIG_TEMPLATE = """\
# SIO configuration — ~/.sio/config.toml
#
# Quick start: uncomment ONE of the [llm] provider blocks below and set
# the matching API key in your shell environment. `sio suggest` will fail
# loudly until a provider is configured.

# Instruction-file budget caps (lines of meaningful content).
budget_cap_primary = 200
budget_cap_supplementary = 150

[llm]

# --- OpenAI ---
# model = "openai/gpt-4o"
# api_key_env = "OPENAI_API_KEY"

# --- Anthropic ---
# model = "anthropic/claude-sonnet-4-20250514"
# api_key_env = "ANTHROPIC_API_KEY"

# --- Azure OpenAI ---
# model = "azure/<deployment-name>"
# api_key_env = "AZURE_OPENAI_API_KEY"
# api_base_env = "AZURE_OPENAI_ENDPOINT"

# --- Local Ollama (free, private; needs `ollama` running on this host) ---
# model = "ollama/qwen3-coder:30b"
# api_base_env = "OLLAMA_HOST"

temperature = 0.7
max_tokens = 16000

[llm.sub]
# Cheaper sub-LM for non-critical secondary calls. OpenAI shown; flip to
# any provider above for full-local mode.
# model = "openai/gpt-4o-mini"
# api_key_env = "OPENAI_API_KEY"
"""

# Subdirectories every fresh `~/.sio/` install needs. The DB itself
# (`sio.db`) is created lazily by `init_db` on first use; subdirs that
# downstream code expects to find are created eagerly.
_SIO_HOME_SUBDIRS = (
    "datasets",
    "previews",
    "backups",
    "ground_truth",
    "optimized",
)


@dataclass
class HomeSeedReport:
    """Result of seeding the `~/.sio/` data dir.

    Mirrors the shape of `harnesses.base.InstallReport` so the CLI can
    render both with one rendering loop.
    """

    sio_home: Path
    dry_run: bool = False
    actions: list[tuple[str, Path, str]] = field(default_factory=list)

    def add(self, action: str, path: Path, reason: str = "") -> None:
        self.actions.append((action, path, reason))


def seed_sio_home(
    *,
    sio_home: Path | None = None,
    dry_run: bool = False,
) -> HomeSeedReport:
    """Create `~/.sio/` + subdirs and seed `~/.sio/config.toml` if absent.

    Idempotent: re-running on an already-bootstrapped home is a no-op
    except for re-emitting "skip" actions in the report. Existing
    `config.toml` is never overwritten by this function — even with
    `force` semantics that's a destructive change reserved for explicit
    `sio init --force-config` (not yet wired).

    Honors the `SIO_HOME` env var so tests and alternate-config users
    can redirect without monkeypatching `Path.home()`.
    """
    if sio_home is None:
        sio_home = Path(os.environ.get("SIO_HOME", str(Path.home() / ".sio")))

    report = HomeSeedReport(sio_home=sio_home, dry_run=dry_run)

    # Top-level dir
    if sio_home.exists():
        report.add("skip", sio_home, "already exists")
    else:
        report.add("would-create" if dry_run else "create", sio_home, "data dir")
        if not dry_run:
            sio_home.mkdir(parents=True, exist_ok=True)

    # Subdirs
    for sub in _SIO_HOME_SUBDIRS:
        target = sio_home / sub
        if target.exists():
            report.add("skip", target, "already exists")
        else:
            report.add("would-create" if dry_run else "create", target, "subdir")
            if not dry_run:
                target.mkdir(parents=True, exist_ok=True)

    # config.toml — never clobber a user-edited file
    cfg_path = sio_home / "config.toml"
    if cfg_path.exists():
        report.add("skip", cfg_path, "config.toml already present — preserved")
    else:
        report.add(
            "would-create" if dry_run else "create",
            cfg_path,
            "seeded template — UNCOMMENT one [llm] provider before running suggest",
        )
        if not dry_run:
            cfg_path.write_text(_DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")

    return report


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
