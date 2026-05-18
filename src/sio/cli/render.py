"""sio render — turn the active (or named) optimized module into a skill.

Examples:
    sio render --active
    sio render 15
    sio render --active --format system-prompt
    sio render 15 --format claude-md -o ~/.claude/rules/auto/sio.md
    sio render --active --dry-run
"""
from __future__ import annotations

import os
from pathlib import Path

import click

from sio.core.runlog import current as _runlog_current
from sio.core.runlog import runlogged


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
              help="Output file. Defaults to ~/.claude/skills/<name>.md for skill format.")
@click.option("--name", "skill_name", default="sio-rule-generator",
              show_default=True, help="Skill name (used in frontmatter + default filename).")
@click.option("--dry-run", is_flag=True,
              help="Print to stdout instead of writing to disk.")
@runlogged("render")
def render_cmd(module_id, use_active, all_active, fmt, output_path, skill_name, dry_run):
    """Render an optimized DSPy module as a skill / prompt / rule file."""
    from sio.render.reader import find_active_module  # noqa: PLC0415

    if not module_id and not use_active and not all_active:
        click.echo("Specify either MODULE_ID, --active, or --all-active.", err=True)
        raise SystemExit(1)

    # --all-active: iterate every distinct module_type with an active row
    if all_active:
        import os as _os  # noqa: PLC0415
        import sqlite3
        db = _os.path.expanduser("~/.sio/sio.db")
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
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
            out = _os.path.expanduser(f"~/.claude/skills/{skill_name_local}.md")
            _render_one(r["id"], "skill", out, skill_name_local, dry_run=False)
        click.echo(f"\n✓ All-active render complete — {len(rows)} skill(s) written.")
        return

    if use_active:
        module_id = find_active_module()

    _render_one(module_id, fmt, output_path, skill_name, dry_run)


def _render_one(module_id, fmt, output_path, skill_name, dry_run):
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
            "claude-md": lambda: render_claude_md(art, meta),
            "json-prompt": lambda: render_json_prompt(art, meta),
        }
        body = renderers[fmt]()

    # Resolve output path
    if dry_run:
        click.echo(body)
        rl.output("dry_run", True)
        return

    if output_path is None:
        if fmt == "skill":
            output_path = os.path.expanduser(f"~/.claude/skills/{skill_name}.md")
        elif fmt == "claude-md":
            output_path = os.path.expanduser(
                f"~/.claude/rules/auto/sio-{skill_name}-{meta['id']}.md"
            )
        elif fmt == "system-prompt":
            output_path = f"/tmp/sio-prompt-{meta['id']}.txt"
        else:
            output_path = f"/tmp/sio-prompt-{meta['id']}.json"

    out_path = Path(output_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body)
    rl.output("output_path", str(out_path))
    rl.output("bytes_written", len(body))

    click.echo(f"\n✓ Rendered module #{module_id} ({fmt}) → {out_path}")
    click.echo(f"  {len(body)} chars, {len(body.splitlines())} lines")
    click.echo(f"  optimizer={meta.get('optimizer_used')} score={meta.get('metric_after')}")
