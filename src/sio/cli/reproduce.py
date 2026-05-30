"""sio reproduce — print the exact CLI command that produced a module.

Reads optimized_modules + trainsets to reconstruct the invocation.
Mode-aware: maps task_lm / reflection_lm model strings back to
--task-mode / --reflection-mode flags when they match a known tier.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import click

_MODEL_TO_MODE = {
    "gemini/gemini-pro-latest":   "work",
    "gemini/gemini-flash-latest": "cheap",
    "openai/gpt-5":               "personal-strong",
    "openai/gpt-4o-mini":         "personal",
}


@click.command("reproduce")
@click.argument("module_id", type=int)
@click.option("--copy", is_flag=True,
              help="Print to stdout in a copy-pasteable single-line form.")
def reproduce_cmd(module_id, copy):
    """Show the exact sio optimize command that produced MODULE_ID.

    Includes optimizer, trainset path (resolved via trainsets table),
    task-mode + reflection-mode (when LMs match known tiers), seed (if recorded),
    and --baseline-against pointing at the previous active module of the same type.
    """
    db = os.path.expanduser(os.environ.get("SIO_DB_PATH",
                                            str(Path.home() / ".sio" / "sio.db")))
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM optimized_modules WHERE id = ?", (module_id,)
    ).fetchone()
    if row is None:
        click.echo(f"No optimized_modules row with id={module_id}", err=True)
        raise SystemExit(1)
    row = dict(row)

    # Resolve trainset path
    trainset_path = None
    if row.get("trainset_id"):
        ts = conn.execute(
            "SELECT * FROM trainsets WHERE id = ?", (row["trainset_id"],)
        ).fetchone()
        if ts:
            trainset_path = dict(ts).get("stored_path")

    # Find prior active module of same type → --baseline-against candidate
    prior = conn.execute(
        "SELECT id FROM optimized_modules "
        "WHERE module_type = ? AND id < ? AND is_active = 1 "
        "ORDER BY id DESC LIMIT 1",
        (row["module_type"], module_id),
    ).fetchone()
    conn.close()

    # Build command parts
    parts = ["sio", "optimize"]
    parts += ["--optimizer", str(row["optimizer_used"])]
    if trainset_path:
        parts += ["--trainset-file", trainset_path]
    else:
        parts += ["# WARNING: no trainset_id linked — cannot reproduce exactly"]
    # LM modes
    tm = _MODEL_TO_MODE.get(row.get("task_lm"))
    if tm:
        parts += ["--task-mode", tm]
    elif row.get("task_lm"):
        parts.append(f"# task_lm={row['task_lm']} (no matching mode)")
    rm = _MODEL_TO_MODE.get(row.get("reflection_lm"))
    if rm:
        parts += ["--reflection-mode", rm]
    elif row.get("reflection_lm"):
        parts.append(f"# reflection_lm={row['reflection_lm']} (no matching mode)")
    if prior:
        parts += ["--baseline-against", str(prior[0])]
    # Seed
    if row.get("seed") is not None:
        parts += ["# (seed:", str(row["seed"]) + ")"]

    if copy:
        click.echo(" ".join(parts))
        return

    click.echo(f"\nReproduce module #{module_id}:")
    click.echo(f"  optimizer:     {row['optimizer_used']}")
    click.echo(f"  module_type:   {row['module_type']}")
    click.echo(f"  score:         {row.get('metric_after')}")
    click.echo(f"  trainset_id:   {row.get('trainset_id') or '(none — gap)'}")
    if trainset_path:
        click.echo(f"  trainset_path: {trainset_path}")
    click.echo(f"  task_lm:       {row.get('task_lm') or '(unrecorded — gap)'}")
    click.echo(f"  reflection_lm: {row.get('reflection_lm') or '(unrecorded — gap)'}")
    click.echo(f"  seed:          {row.get('seed') or '(unrecorded — gap)'}")
    click.echo(f"  created_at:    {row['created_at']}")
    click.echo()
    click.echo("Command:")
    click.echo("  " + " ".join(parts))

    gaps = []
    if not trainset_path:
        gaps.append("trainset_id")
    if not row.get("task_lm"):
        gaps.append("task_lm")
    if not row.get("reflection_lm"):
        gaps.append("reflection_lm")
    if row.get("seed") is None:
        gaps.append("seed")
    if gaps:
        click.echo()
        click.echo(f"⚠  Reproducibility gaps: {', '.join(gaps)}. "
                   "Future runs will record these (PRD sio_dataset_versioning_2026-05-16).")
