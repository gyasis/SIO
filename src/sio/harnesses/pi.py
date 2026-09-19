"""pi coding-agent harness adapter (`@earendil-works/pi-coding-agent`).

pi keeps user config under `~/.pi/agent/` (overridable with the
``PI_CODING_AGENT_DIR`` env var — see pi's ``config.js::getAgentDir``):

    skills/<name>/SKILL.md   user skills, one folder per skill
    AGENTS.md                the user's standing context (pi has NO rules dir)
    extensions/              pi's plugin mechanism (pi has NO hooks system)

Skill discovery (pi v0.85.1, ``dist/core/skills.js`` +
``dist/core/package-manager.js``):

* directories scanned: ``<agentDir>/skills/`` and ``~/.agents/skills/`` for
  user skills, ``<cwd>/.pi/skills/`` and every ancestor ``.agents/skills/``
  for project skills (project dirs only when the project is trusted);
* a directory containing ``SKILL.md`` is a skill root and is not recursed;
  otherwise pi recurses into subdirectories and also loads bare ``*.md``
  files sitting directly in the root;
* ``name`` comes from the frontmatter, falling back to the parent directory
  name, and must be 1-64 chars of ``[a-z0-9-]``, not start/end with ``-`` and
  not contain ``--``;
* ``description`` is required, non-empty, and at most 1024 characters — a
  skill with no description is NOT loaded at all;
* frontmatter must be valid YAML; unknown keys are tolerated.

This adapter stages ONLY the bundled skills. Bundled rules have no pi
equivalent (no rules dir, and SIO must not write into the user's AGENTS.md)
and SIO's telemetry hooks target Claude Code's hooks system, which pi does
not have. Both are reported as unsupported in the InstallReport rather than
silently dropped.

Every staged SKILL.md is passed through :func:`conform_skill_for_pi` so it
satisfies pi's validator even if the Claude copy does not; any change is
recorded in the report. Files SIO did not install (e.g. a same-named skill
another tool put in ``~/.pi/agent/skills/``) are never overwritten without
``--force``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import ClassVar

from sio.harnesses.base import HarnessAdapter, InstallReport, StatusReport
from sio.harnesses.bootstrap import iter_bootstrap_files
from sio.harnesses.managed import remove_files, stage_files, status_files

PI_AGENT_DIR_ENV = "PI_CODING_AGENT_DIR"

# From pi's skills.js — keep in sync with its validateName / validateDescription.
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
_NAME_RE = re.compile(r"^[a-z0-9-]+$")

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)


def default_config_dir() -> Path:
    env = os.environ.get(PI_AGENT_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".pi" / "agent"


# --------------------------------------------------------------- conformance
def _js_len(text: str) -> int:
    """Length as JavaScript's ``String.length`` counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def _yaml_str(text: str) -> str:
    """A YAML 1.2 double-quoted scalar (JSON string syntax is a strict subset)."""
    return json.dumps(text, ensure_ascii=False)


def _valid_name(name: str) -> bool:
    return (
        0 < len(name) <= MAX_NAME_LENGTH
        and bool(_NAME_RE.match(name))
        and not name.startswith("-")
        and not name.endswith("-")
        and "--" not in name
    )


def _normalise_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", raw.lower())
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:MAX_NAME_LENGTH].rstrip("-")


def _scalar_value(lines: list[str], idx: int) -> tuple[str | None, int]:
    """Read the value of the ``key:`` line at ``idx``.

    Returns ``(value, end_idx)`` where ``end_idx`` is the index one past the
    last line the value occupies. Handles plain and quoted single-line
    scalars and ``|`` / ``>`` block scalars (continuation lines indented
    deeper than the key). ``None`` means "no value on this line".
    """
    key_line = lines[idx]
    _key, _, rest = key_line.partition(":")
    rest = rest.strip()
    key_indent = len(key_line) - len(key_line.lstrip(" "))

    if rest and rest[0] in "|>":
        fold = rest[0] == ">"
        body: list[str] = []
        j = idx + 1
        while j < len(lines):
            line = lines[j]
            if line.strip() == "":
                body.append("")
                j += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent <= key_indent:
                break
            body.append(line.strip())
            j += 1
        while body and body[-1] == "":
            body.pop()
        text = " ".join(b for b in body if b) if fold else "\n".join(body)
        return text, j

    if not rest:
        return None, idx + 1
    if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
        inner = rest[1:-1]
        if rest[0] == '"':
            try:
                inner = json.loads(rest)
            except ValueError:
                pass
        else:
            inner = inner.replace("''", "'")
        return inner, idx + 1
    return rest, idx + 1


def conform_skill_for_pi(dir_name: str, text: str) -> tuple[str, list[str]]:
    """Return ``(text_for_pi, notes)`` — SKILL.md rewritten to pass pi's validator.

    Only the ``name`` and ``description`` frontmatter keys are ever touched,
    and only when pi would flag them. A conforming file comes back verbatim
    with an empty notes list. Replacement values are emitted as JSON strings
    (valid YAML 1.2 double-quoted scalars) so the rewrite cannot break the
    YAML pi parses. Lengths are measured in UTF-16 code units, which is what
    pi's ``String.length`` check counts. Notes describe each transformation.
    """
    notes: list[str] = []
    m = _FRONTMATTER_RE.match(text)
    if not m:
        # No frontmatter → no description → pi would not load it. Synthesize
        # a minimal one from the directory name and first body line.
        first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
        desc = first[:MAX_DESCRIPTION_LENGTH] or f"SIO skill {dir_name}"
        name = dir_name if _valid_name(dir_name) else _normalise_name(dir_name)
        fm = f"---\nname: {name}\ndescription: {_yaml_str(desc)}\n---\n"
        notes.append("added missing frontmatter (name + description) for pi")
        return fm + text, notes

    fm_lines = m.group(1).split("\n")
    body = text[m.end():]

    name: str | None = None
    name_idx = desc_idx = -1
    desc: str | None = None
    desc_end = -1
    i = 0
    while i < len(fm_lines):
        line = fm_lines[i]
        if line.startswith("name:"):
            name, end = _scalar_value(fm_lines, i)
            name_idx = i
            i = end
            continue
        if line.startswith("description:"):
            desc, end = _scalar_value(fm_lines, i)
            desc_idx, desc_end = i, end
            i = end
            continue
        i += 1

    effective_name = name or dir_name
    new_name: str | None = None
    if not _valid_name(effective_name):
        candidate = dir_name if _valid_name(dir_name) else _normalise_name(effective_name)
        if not _valid_name(candidate):
            candidate = _normalise_name(dir_name) or "sio-skill"
        new_name = candidate
        notes.append(f"name {effective_name!r} invalid for pi → {new_name!r}")

    new_desc: str | None = None
    if desc is None or not desc.strip():
        first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "")
        new_desc = first[:MAX_DESCRIPTION_LENGTH] or f"SIO skill {effective_name}"
        notes.append("description missing → synthesized from first body line for pi")
    elif _js_len(desc) > MAX_DESCRIPTION_LENGTH:
        cut = desc
        while _js_len(cut) > MAX_DESCRIPTION_LENGTH - 1:
            cut = cut[:-1]
        new_desc = cut.rstrip() + "…"
        notes.append(
            f"description {_js_len(desc)} chars > pi max {MAX_DESCRIPTION_LENGTH} → truncated"
        )

    if new_name is None and new_desc is None:
        return text, notes

    out = list(fm_lines)
    # Replace from the bottom up so earlier indices stay valid.
    edits: list[tuple[int, int, str]] = []
    if new_desc is not None:
        if desc_idx >= 0:
            edits.append((desc_idx, desc_end, f"description: {_yaml_str(new_desc)}"))
        else:
            edits.append((len(out), len(out), f"description: {_yaml_str(new_desc)}"))
    if new_name is not None:
        if name_idx >= 0:
            edits.append((name_idx, name_idx + 1, f"name: {new_name}"))
        else:
            edits.append((0, 0, f"name: {new_name}"))
    for start, end, replacement in sorted(edits, reverse=True):
        out[start:end] = [replacement]

    return "---\n" + "\n".join(out) + "\n---\n" + body, notes


# ------------------------------------------------------------------- adapter
class PiAdapter(HarnessAdapter):
    name: ClassVar[str] = "pi"

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or default_config_dir()

    # ------------------------------------------------------------------ detect
    def detect(self) -> bool:
        # pi creates ~/.pi/agent on first run; require it so `sio init`
        # (no --harness) does not bootstrap pi on machines that never ran it.
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
            resolve_target=lambda key: self.config_dir / key,
            report=report,
            dry_run=dry_run,
        )
        if not dry_run:
            self._prune_empty_dirs(removed)
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
    def _staged_files(self) -> tuple[list[tuple[str, Path, str]], list[str]]:
        """Bundled files mapped to pi targets, plus notes on what is NOT staged."""
        files: list[tuple[str, Path, str]] = []
        notes: list[str] = []
        unsupported_rules = 0
        transformed: list[str] = []

        for _src, rel_path, text in iter_bootstrap_files():
            top = rel_path.parts[0] if rel_path.parts else ""
            if top != "skills":
                # rules/** — no pi equivalent (no rules dir; AGENTS.md is the
                # user's own file and SIO must not write into it).
                unsupported_rules += 1
                continue
            if len(rel_path.parts) < 3:
                # A bare skills/<file>.md — pi loads root-level .md files as
                # skills, so never stage README-style files there.
                notes.append(f"{rel_path}: not staged (pi treats root-level .md as a skill)")
                continue
            skill_dir = rel_path.parts[1]
            if rel_path.name == "SKILL.md":
                text, changes = conform_skill_for_pi(skill_dir, text)
                for change in changes:
                    transformed.append(f"{skill_dir}: {change}")
            files.append((str(rel_path), self.config_dir / rel_path, text))

        # Say out loud which existing skills SIO is leaving alone, so a user
        # with their own ~/.pi/agent/skills/ can see the boundary in the output.
        skills_root = self.config_dir / "skills"
        if skills_root.is_dir():
            sio_names = {Path(key).parts[1] for key, _t, _x in files}
            foreign = sorted(
                p.name
                for p in skills_root.iterdir()
                if not p.name.startswith(".") and p.name not in sio_names
            )
            if foreign:
                notes.append(
                    f"{len(foreign)} existing skill(s) not managed by SIO — left "
                    f"untouched: {', '.join(foreign)}"
                )

        if unsupported_rules:
            notes.append(
                f"{unsupported_rules} bundled rule file(s) not supported on pi "
                "(pi has no rules dir; standing context is the user's AGENTS.md, "
                "which SIO does not modify) — not installed"
            )
        notes.append(
            "hook telemetry not supported on pi (pi has extensions, no hooks "
            "system) — no hooks registered; use `sio mine --agent pi` to ingest "
            "pi sessions"
        )
        notes.extend(f"pi-conformance transform: {t}" for t in transformed)
        return files, notes

    def _prune_empty_dirs(self, removed: list[Path]) -> None:
        """Remove skill directories left empty by an uninstall (rmdir is no-op otherwise)."""
        skills_root = self.config_dir / "skills"
        for path in removed:
            parent = path.parent
            while parent != skills_root and skills_root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
