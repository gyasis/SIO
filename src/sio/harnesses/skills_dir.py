"""Shared staging for harnesses that take SIO's bundled SKILLS and nothing else.

codex and opencode both read user skills from one directory of
``<name>/SKILL.md`` folders and have no home for SIO's bundled rules or
Claude Code hook telemetry. This module holds the parts of that shape they
share — mapping the bundled ``skills/**`` tree onto a target ``skills/``
root, naming the foreign skills SIO leaves alone, the minimal frontmatter
conformance both loaders need, and pruning emptied dirs on uninstall — so
each adapter module is only the harness-specific facts (where the dir is,
which env var moves it, what it says about the unsupported asset types).

``pi.py`` predates this module and keeps its own copy of the same loop plus
pi's stricter validator; it is deliberately left as is.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sio.harnesses.bootstrap import iter_bootstrap_files
from sio.harnesses.managed import StagedFile
from sio.harnesses.pi import _FRONTMATTER_RE, _scalar_value, _yaml_str

#: ``(dir_name, text) -> (text, notes)`` — a per-harness SKILL.md transform.
Conformer = Callable[[str, str], tuple[str, list[str]]]

_NAME_LINE_RE = re.compile(r"^name:", re.MULTILINE)


def ensure_frontmatter_description(dir_name: str, text: str) -> tuple[str, list[str]]:
    """Return ``(text, notes)`` — SKILL.md with a frontmatter block and a description.

    Both codex and opencode index a skill by its frontmatter and drop one
    whose ``description`` is missing or empty (codex reports
    ``missing field `description```; opencode never surfaces it to the
    model). Neither constrains the name, so only those two conditions are
    ever repaired. A conforming file comes back verbatim with no notes.
    """
    notes: list[str] = []
    m = _FRONTMATTER_RE.match(text)
    if not m:
        first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
        desc = first or f"SIO skill {dir_name}"
        fm = f"---\nname: {dir_name}\ndescription: {_yaml_str(desc)}\n---\n"
        notes.append("added missing frontmatter (name + description)")
        return fm + text, notes

    fm_lines = m.group(1).split("\n")
    body = text[m.end() :]
    desc: str | None = None
    desc_idx = desc_end = -1
    i = 0
    while i < len(fm_lines):
        if fm_lines[i].startswith("description:"):
            desc, end = _scalar_value(fm_lines, i)
            desc_idx, desc_end = i, end
            i = end
            continue
        i += 1
    if desc is not None and desc.strip():
        return text, notes

    first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "")
    new_desc = first or f"SIO skill {dir_name}"
    out = list(fm_lines)
    line = f"description: {_yaml_str(new_desc)}"
    if desc_idx >= 0:
        out[desc_idx:desc_end] = [line]
    else:
        out.append(line)
    notes.append("description missing → synthesized from first body line")
    return "---\n" + "\n".join(out) + "\n---\n" + body, notes


@dataclass
class StagedSkills:
    """What a skills-only adapter stages, plus what it could not."""

    files: list[StagedFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unsupported_rules: int = 0
    transforms: list[str] = field(default_factory=list)


def stage_bundled_skills(
    skills_root: Path,
    *,
    harness: str,
    conform: Conformer = ensure_frontmatter_description,
) -> StagedSkills:
    """Map the bundled ``skills/**`` tree onto ``skills_root``.

    ``rules/**`` is counted, not staged — the adapter phrases why. Bare
    ``skills/<file>.md`` (the README) is never staged: a loader that scans
    a skills root should not be handed a file that is not a skill. Every
    ``SKILL.md`` passes through ``conform``; any change is recorded.
    """
    staged = StagedSkills()
    for _src, rel_path, text in iter_bootstrap_files():
        top = rel_path.parts[0] if rel_path.parts else ""
        if top != "skills":
            staged.unsupported_rules += 1
            continue
        if len(rel_path.parts) < 3:
            staged.notes.append(f"{rel_path}: not staged (not a skill folder)")
            continue
        skill_dir = rel_path.parts[1]
        if rel_path.name == "SKILL.md":
            text, changes = conform(skill_dir, text)
            staged.transforms.extend(f"{skill_dir}: {c}" for c in changes)
        target = skills_root / Path(*rel_path.parts[1:])
        staged.files.append((str(rel_path), target, text))

    foreign = foreign_skills(skills_root, {Path(k).parts[1] for k, _t, _x in staged.files})
    if foreign:
        staged.notes.append(
            f"{len(foreign)} existing skill(s) not managed by SIO — left "
            f"untouched: {', '.join(foreign)}"
        )
    staged.notes.extend(f"{harness}-conformance transform: {t}" for t in staged.transforms)
    return staged


def foreign_skills(skills_root: Path, sio_names: Iterable[str]) -> list[str]:
    """Names of entries in ``skills_root`` that SIO does not stage (symlinks flagged)."""
    if not skills_root.is_dir():
        return []
    mine = set(sio_names)
    out: list[str] = []
    for p in sorted(skills_root.iterdir()):
        if p.name.startswith(".") or p.name in mine:
            continue
        out.append(f"{p.name} (symlink)" if p.is_symlink() else p.name)
    return out


def prune_empty_dirs(skills_root: Path, removed: Iterable[Path]) -> None:
    """Remove skill directories an uninstall left empty (``rmdir`` is a no-op otherwise)."""
    for path in removed:
        parent = path.parent
        while parent != skills_root and skills_root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
