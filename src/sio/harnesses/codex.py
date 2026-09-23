"""Codex CLI harness adapter (`@openai/codex`, verified against codex-cli 0.154.0).

Codex keeps user config under ``~/.codex/`` — overridable with the
``CODEX_HOME`` env var, which the binary resolves itself (``codex app-server``
``initialize`` echoes the resolved dir as ``codexHome``):

    skills/<name>/SKILL.md   user skills, one folder per skill (+ siblings)
    skills/.system/          codex's own bundled skills, written by codex
    AGENTS.md                the user's global standing context
    rules/*.rules            execpolicy command-approval rules (Starlark
                             ``prefix_rule(...)``), NOT markdown context
    hooks/hooks.json         codex's hooks system (feature ``hooks``)
    config.toml              the user's config — never touched

What its loader accepts was established by driving ``codex app-server``
``skills/list`` against a temporary ``CODEX_HOME`` (see
``tests/unit/test_harnesses.py::TestCodexAdapter``):

* every ``<root>/skills/<dir>/SKILL.md`` is a skill; symlinked dirs are
  followed; a bare ``skills/<file>.md`` is ignored; ``~/.agents/skills/``
  is scanned too;
* YAML frontmatter is required (``missing YAML frontmatter delimited by
  ---``) and ``description`` is required and non-empty (``missing field
  `description```) — such a file is listed under ``errors`` and not loaded;
* ``name`` is optional (falls back to the directory name) and is NOT
  pattern-checked; a 2000-char description loads.

This adapter stages ONLY the bundled skills. Bundled rules have no codex
equivalent (its ``rules/`` dir is command policy, and SIO must not write into
the user's AGENTS.md) and SIO's telemetry hooks parse Claude Code's hook
payload, which codex's own hooks system does not send. Both are reported as
unsupported rather than silently dropped. Files SIO did not install are never
overwritten without ``--force``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from sio.harnesses.base import HarnessAdapter, InstallReport, StatusReport
from sio.harnesses.managed import remove_files, stage_files, status_files
from sio.harnesses.skills_dir import (
    ensure_frontmatter_description,
    prune_empty_dirs,
    stage_bundled_skills,
)

CODEX_HOME_ENV = "CODEX_HOME"


def default_config_dir() -> Path:
    env = os.environ.get(CODEX_HOME_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def conform_skill_for_codex(dir_name: str, text: str) -> tuple[str, list[str]]:
    """SKILL.md rewritten only if codex's loader would reject it (see module doc)."""
    return ensure_frontmatter_description(dir_name, text)


class CodexAdapter(HarnessAdapter):
    name: ClassVar[str] = "codex"

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or default_config_dir()

    @property
    def skills_root(self) -> Path:
        return self.config_dir / "skills"

    # ------------------------------------------------------------------ detect
    def detect(self) -> bool:
        # codex creates ~/.codex on first run; require it so a bare `sio init`
        # does not bootstrap codex on machines that never ran it.
        return self.config_dir.exists()

    # ------------------------------------------------------------------ install
    def install(self, *, dry_run: bool = False, force: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        files, notes = self._staged_files()
        stage_files(
            config_dir=self.config_dir,
            files=files,
            report=report,
            dry_run=dry_run,
            force=force,
            adopt_untracked=False,
        )
        report.notes.extend(notes)
        return report

    # ---------------------------------------------------------------- uninstall
    def uninstall(self, *, dry_run: bool = False) -> InstallReport:
        report = InstallReport(harness=self.name, dry_run=dry_run)
        removed = remove_files(
            config_dir=self.config_dir,
            resolve_target=self._resolve_target,
            report=report,
            dry_run=dry_run,
        )
        if not dry_run:
            prune_empty_dirs(self.skills_root, removed)
        return report

    # -------------------------------------------------------------------- status
    def status(self) -> StatusReport:
        report = StatusReport(
            harness=self.name,
            detected=self.detect(),
            config_dir=self.config_dir,
        )
        files, notes = self._staged_files()
        status_files(config_dir=self.config_dir, files=files, report=report)
        report.notes.extend(notes)
        return report

    # ----------------------------------------------------------------- internals
    def _resolve_target(self, key: str) -> Path:
        # Manifest keys are the bundled path (`skills/<name>/...`), which maps
        # 1:1 under config_dir because codex's skills dir is also `skills/`.
        return self.config_dir / key

    def _staged_files(self) -> tuple[list, list[str]]:
        staged = stage_bundled_skills(
            self.skills_root, harness=self.name, conform=conform_skill_for_codex
        )
        notes = list(staged.notes)
        if staged.unsupported_rules:
            notes.append(
                f"{staged.unsupported_rules} bundled rule file(s) not supported on codex "
                "(its rules/ dir holds execpolicy `.rules` command-approval files, not "
                "markdown; standing context is the user's AGENTS.md, which SIO does not "
                "modify) — not installed"
            )
        notes.append(
            "hook telemetry not registered on codex (codex has its own hooks.json "
            "system, but SIO's telemetry hooks parse Claude Code's payload); use "
            "`sio mine --agent codex` to ingest codex sessions"
        )
        return staged.files, notes
