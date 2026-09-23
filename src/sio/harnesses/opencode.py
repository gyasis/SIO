"""OpenCode harness adapter (`opencode-ai`, verified against opencode 1.18.4).

opencode's global config dir is ``$XDG_CONFIG_HOME/opencode`` (default
``~/.config/opencode``; ``opencode debug paths`` prints the resolved value).
The layout, from the ``customize-opencode`` skill compiled into the binary:

    skills/<name>/SKILL.md   global skills (``skill/`` singular also read)
    command(s)/<name>.md     global slash commands (prompt templates)
    agent(s)/<name>.md       global agents / subagents
    plugin(s)/*.ts|*.js      auto-discovered plugins (opencode's hook surface)
    opencode.json            the user's config — never touched
    AGENTS.md / instructions the user's standing context — never touched

Two more facts were established by running ``opencode debug skill`` against a
temporary HOME (see ``tests/unit/test_harnesses.py::TestOpenCodeAdapter``):

* ``OPENCODE_CONFIG_DIR`` is an ADDITIONAL config root opencode also scans;
  it does not relocate the XDG dir (``debug paths`` is unchanged), so this
  adapter installs into the XDG dir and honours only ``XDG_CONFIG_HOME``;
* the loader enforces neither a name pattern nor a description length, and a
  skill whose frontmatter name differs from its folder still loads under the
  frontmatter name. Its embedded doc says a skill without a ``description``
  is never surfaced to the model, so that is the one thing conformed.

opencode ALSO auto-loads ``~/.claude/skills/<name>/SKILL.md`` and
``~/.agents/skills/`` as "external skills" (disable with
``OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`` / ``OPENCODE_DISABLE_EXTERNAL_SKILLS=1``),
so on a machine with a Claude Code install the SIO skills are already
visible to it. On a name clash the global copy wins (verified), which is why
installing here is still worthwhile: it survives that env var and machines
without Claude Code. The install report says so when it applies.

This adapter stages ONLY the bundled skills. Bundled rules have no opencode
equivalent (standing context is AGENTS.md / ``instructions`` in
opencode.json, both the user's) and SIO's telemetry hooks target Claude
Code's hooks system; opencode has plugins instead. Both are reported as
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

XDG_CONFIG_HOME_ENV = "XDG_CONFIG_HOME"


def default_config_dir() -> Path:
    env = os.environ.get(XDG_CONFIG_HOME_ENV)
    base = Path(env).expanduser() if env else Path.home() / ".config"
    return base / "opencode"


def conform_skill_for_opencode(dir_name: str, text: str) -> tuple[str, list[str]]:
    """SKILL.md rewritten only if opencode would never surface it (no description)."""
    return ensure_frontmatter_description(dir_name, text)


class OpenCodeAdapter(HarnessAdapter):
    name: ClassVar[str] = "opencode"

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or default_config_dir()

    @property
    def skills_root(self) -> Path:
        return self.config_dir / "skills"

    # ------------------------------------------------------------------ detect
    def detect(self) -> bool:
        # opencode creates its config dir on first run; require it so a bare
        # `sio init` does not bootstrap opencode on machines that never ran it.
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
        # 1:1 under config_dir because opencode's skills dir is also `skills/`.
        return self.config_dir / key

    def _staged_files(self) -> tuple[list, list[str]]:
        staged = stage_bundled_skills(
            self.skills_root, harness=self.name, conform=conform_skill_for_opencode
        )
        notes = list(staged.notes)

        # opencode's external-skill scan reads ~/.claude/skills directly, so a
        # Claude Code install of SIO is already visible to it. Say so, and why
        # a global copy is still installed.
        claude_skills = Path.home() / ".claude" / "skills"
        sio_names = {Path(k).parts[1] for k, _t, _x in staged.files}
        if os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS") != "1" and claude_skills.is_dir():
            shared = sorted(n for n in sio_names if (claude_skills / n / "SKILL.md").exists())
            if shared:
                notes.append(
                    f"{len(shared)} SIO skill(s) are already visible to opencode through "
                    f"its external scan of {claude_skills}; the copies in "
                    f"{self.skills_root} take precedence on a name clash and stay "
                    "available with OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 or without "
                    "Claude Code"
                )

        if staged.unsupported_rules:
            notes.append(
                f"{staged.unsupported_rules} bundled rule file(s) not supported on opencode "
                "(no rules dir; standing context is the user's AGENTS.md / `instructions` "
                "in opencode.json, which SIO does not modify) — not installed"
            )
        notes.append(
            "hook telemetry not registered on opencode (opencode has plugins, not a "
            "hooks system); use `sio mine --agent opencode` to ingest opencode sessions"
        )
        return staged.files, notes
