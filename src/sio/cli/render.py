"""sio render — turn the active (or named) optimized module into a skill.

Examples:
    sio render --active
    sio render 15
    sio render --active --format system-prompt
    sio render 15 --format claude-md        # -> ~/.claude/rulebook/domains/ (on demand)
    sio render 15 --format claude-md --always-load   # -> ~/.claude/rules/core/ (every session)
    sio render --active --dry-run

Where output lands (Claude Code auto-loads EVERY .md under ~/.claude/rules/):
    skill      ~/.claude/skills/<name>/SKILL.md   only the description is always in context
    claude-md  ~/.claude/rulebook/domains/sio-<name>-<id>.md, with a
               ``<!-- triggers: ... -->`` line so a rules-injector loads it on demand
               (top level of domains/: injectors do not scan subfolders)
    --always-load  claude-md to ~/.claude/rules/core/ instead — costs context in every
                   session; use only for a rule that must always hold
"""
from __future__ import annotations

from pathlib import Path

import click

from sio.core.runlog import current as _runlog_current
from sio.core.runlog import runlogged
from sio.core.paths import db_path as _default_db_path


@click.command("render")
@click.argument("module_id", required=False, type=int)
@click.option("--active", "use_active", is_flag=True,
              help="Render the currently-active optimized module.")
@click.option("--all-active", "all_active", is_flag=True,
              help="Render EVERY active optimized module (one skill per module_type).")
@click.option("--format", "fmt", default="skill",
              type=click.Choice(["skill", "system-prompt", "claude-md", "json-prompt"]),
              show_default=True)
@click.option("-o", "--output", "output_path", default=None,
              help="Output file. Defaults: skill -> ~/.claude/skills/<name>/SKILL.md; "
                   "claude-md -> ~/.claude/rulebook/domains/sio-<name>-<id>.md.")
@click.option("--always-load", is_flag=True,
              help="claude-md only: write to ~/.claude/rules/core/ so Claude Code loads it "
                   "into EVERY session (costs context on every prompt).")
@click.option("--name", "skill_name", default="sio-rule-generator",
              show_default=True, help="Skill name (used in frontmatter + default filename).")
@click.option("--dry-run", is_flag=True,
              help="Print to stdout instead of writing to disk.")
@runlogged("render")
def render_cmd(module_id, use_active, all_active, fmt, output_path, always_load, skill_name,
               dry_run):
    """Render an optimized DSPy module as a skill / prompt / rule file."""
    from sio.render.reader import find_active_module  # noqa: PLC0415

    if not module_id and not use_active and not all_active:
        click.echo("Specify either MODULE_ID, --active, or --all-active.", err=True)
        raise SystemExit(1)

    # --all-active: iterate every distinct module_type with an active row
    if all_active:
        import sqlite3
        db = str(_default_db_path())
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, module_type FROM optimized_modules "
            "WHERE is_active = 1 GROUP BY module_type "
            "ORDER BY id DESC"
        ).fetchall()
        conn.close()
        if not rows:
            click.echo("No active modules to render.", err=True)
            raise SystemExit(1)
        click.echo(f"Rendering {len(rows)} active module(s) as skills...\n")
        for r in rows:
            mtype = r["module_type"]
            # Derive a safe skill name from module_type
            skill_name_local = f"sio-{mtype.replace('_', '-')}"
            out = str(_skill_path(skill_name_local))
            _render_one(r["id"], "skill", out, skill_name_local, dry_run=False)
        click.echo(f"\n✓ All-active render complete — {len(rows)} skill(s) written.")
        return

    if use_active:
        module_id = find_active_module()

    if always_load and fmt != "claude-md":
        click.echo("--always-load only applies to --format claude-md.", err=True)
        raise SystemExit(1)
    _render_one(module_id, fmt, output_path, skill_name, dry_run, always_load=always_load)


def _claude_home() -> Path:
    return Path.home() / ".claude"


def _skill_path(skill_name: str) -> Path:
    """Claude Code skills are folders: ~/.claude/skills/<name>/SKILL.md (the layout
    `sio init` installs, so a render replaces the shipped placeholder)."""
    return _claude_home() / "skills" / skill_name / "SKILL.md"


def default_output_path(fmt: str, skill_name: str, module_id, always_load: bool = False) -> Path:
    """Where a render lands when no -o is given. Never ~/.claude/rules/ outside core/,
    and core/ only on an explicit --always-load."""
    if fmt == "skill":
        return _skill_path(skill_name)
    if fmt == "claude-md":
        sub = ("rules", "core") if always_load else ("rulebook", "domains")
        stem = skill_name if skill_name.startswith("sio-") else f"sio-{skill_name}"
        return _claude_home().joinpath(*sub) / f"{stem}-{module_id}.md"
    if fmt == "system-prompt":
        return Path(f"/tmp/sio-prompt-{module_id}.txt")
    return Path(f"/tmp/sio-prompt-{module_id}.json")


def always_loaded_warning(out_path: Path, size: int) -> str | None:
    """Warn when a file lands where Claude Code loads it into every session."""
    rules = _claude_home() / "rules"
    try:
        rel = out_path.expanduser().resolve().relative_to(rules.resolve())
    except (ValueError, OSError):
        return None
    where = "rules/core/" if rel.parts[:1] == ("core",) else "rules/ (outside core/)"
    msg = (f"WARNING: {out_path} is under ~/.claude/{where} — Claude Code loads it into "
           f"EVERY session (+{size:,} chars of context per prompt).")
    if where != "rules/core/":
        msg += (" On-demand rules belong in ~/.claude/rulebook/domains/ "
                "(the default for --format claude-md).")
    return msg


def render_triggers(skill_name: str) -> list[str]:
    """Declared trigger keywords for an on-demand rendered rule: the SIO commands that
    use these optimized modules, plus the module's own name."""
    return ["sio suggest", "sio apply", "sio optimize", skill_name]


def _render_one(module_id, fmt, output_path, skill_name, dry_run, always_load=False):
    """Render a single module — extracted so --all-active can reuse."""
    from sio.render import (  # noqa: PLC0415
        load_artifact,
        load_module_metadata,
        render_claude_md,
        render_json_prompt,
        render_skill,
        render_system_prompt,
    )
    rl = _runlog_current()
    with rl.stage("load_metadata"):
        meta = load_module_metadata(module_id)
    artifact_path = Path(meta["file_path"])
    if not artifact_path.exists():
        click.echo(f"Artifact missing: {artifact_path}", err=True)
        raise SystemExit(1)

    with rl.stage("load_artifact"):
        art = load_artifact(artifact_path)
        if not art["instruction"]:
            click.echo(
                f"WARNING: artifact at {artifact_path} has empty instruction. "
                f"Shape={art.get('shape')}. Output will be incomplete.",
                err=True,
            )

    with rl.stage("render"):
        renderers = {
            "skill": lambda: render_skill(art, meta, skill_name=skill_name),
            "system-prompt": lambda: render_system_prompt(art, meta),
            "claude-md": lambda: render_claude_md(
                art, meta,
                # always-loaded rules need no trigger; on-demand ones do
                triggers=None if always_load else render_triggers(skill_name),
            ),
            "json-prompt": lambda: render_json_prompt(art, meta),
        }
        body = renderers[fmt]()

    # Resolve output path
    if dry_run:
        click.echo(body)
        rl.output("dry_run", True)
        return

    if output_path is None:
        output_path = default_output_path(fmt, skill_name, meta["id"], always_load)

    out_path = Path(output_path).expanduser()
    warning = always_loaded_warning(out_path, len(body))
    if warning:
        click.echo(warning, err=True)
        rl.output("always_loaded_warning", warning)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body)
    rl.output("output_path", str(out_path))
    rl.output("bytes_written", len(body))

    click.echo(f"\n✓ Rendered module #{module_id} ({fmt}) → {out_path}")
    click.echo(f"  {len(body)} chars, {len(body.splitlines())} lines")
    click.echo(f"  optimizer={meta.get('optimizer_used')} score={meta.get('metric_after')}")
