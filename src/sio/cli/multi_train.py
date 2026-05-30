"""sio multi-train — parallel optimizer driver across multiple target_surface
or LM mixes.

This is the "fire many optimize runs concurrently" tool. Each child run uses
the same shared pipeline (curate trainset → optimize → render) but can be
parameterized on:
  - target_surface (filter trainset by surface)
  - task_mode / reflection_mode (LM tier per role)
  - optimizer (gepa/mipro/bootstrap)

Children run as subprocesses. The parent streams progress + collects results
via the XIII run-log files each child produces.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from sio.core.cost.estimator import estimate_optimize_run

SURFACES = [
    "claude_md_rule",
    "skill_update",
    "hook_config",
    "mcp_config",
    "settings_config",
    "agent_profile",
    "project_config",
]


@click.command("multi-train")
@click.option(
    "--surfaces", default="claude_md_rule",
    help=(
        "Comma-separated target_surface list, or 'all' for every surface in "
        f"the catalog ({', '.join(SURFACES)})."
    ),
)
@click.option("--parallelism", default=4, show_default=True, type=int,
              help="Max concurrent subprocess optimize runs.")
@click.option("--optimizer", default="gepa", show_default=True,
              type=click.Choice(["gepa", "mipro", "bootstrap"]))
@click.option("--budget", default="light", show_default=True,
              type=click.Choice(["light", "medium", "heavy"]))
@click.option("--lm-mix", default="balanced", show_default=True,
              type=click.Choice(["all-work", "all-cheap", "balanced", "free-first"]),
              help=(
                  "How to assign task/reflection LMs across runs. "
                  "all-work=Pro both; all-cheap=Flash both; "
                  "balanced=rotate gpt-5/Pro/Flash; free-first=Ollama where possible."
              ))
@click.option("--trainset-file", default=None,
              help="Default trainset (per-surface filter applied via sio curate).")
@click.option("--dry-run", is_flag=True,
              help="Print plan + cost estimate, do not launch any child.")
def multi_train_cmd(surfaces, parallelism, optimizer, budget, lm_mix,
                    trainset_file, dry_run):
    """Fire N optimize runs in parallel, one per surface (or LM combo)."""

    # Resolve surface list
    if surfaces.strip() == "all":
        surface_list = SURFACES[:]
    else:
        surface_list = [s.strip() for s in surfaces.split(",") if s.strip()]

    # LM-mix → list of (task_mode, reflection_mode) tuples cycled across runs
    _LM_MIXES = {
        "all-work":    [("work", "work")],
        "all-cheap":   [("cheap", "cheap")],
        "balanced":    [
            ("cheap", "personal-strong"),  # Flash + gpt-5 (proven winner)
            ("cheap", "work"),             # Flash + Pro (work-key billed)
            ("cheap", "cheap"),            # Flash + Flash (cheapest)
        ],
        "free-first":  [
            ("free", "free"),              # Ollama + Ollama
            ("cheap", "free"),             # Flash task + Ollama reflection
        ],
    }
    lm_combos = _LM_MIXES[lm_mix]

    # Build run plan: one row per (surface, lm_combo) pair if multiple LM combos
    plan: list[dict] = []
    for surface in surface_list:
        for i, (task_mode, refl_mode) in enumerate(lm_combos):
            plan.append({
                "surface": surface,
                "task_mode": task_mode,
                "reflection_mode": refl_mode,
                "label": f"{surface}-{task_mode}-{refl_mode}-{i}",
            })

    # Cost estimate
    _MODE_TO_MODEL = {
        "work":            "gemini/gemini-pro-latest",
        "cheap":           "gemini/gemini-flash-latest",
        "free":            "ollama_chat/deepseek-r1:32b",
        "personal":        "openai/gpt-4o-mini",
        "personal-strong": "openai/gpt-5",
    }
    total_low = total_mid = total_high = 0.0
    click.echo("\n=== multi-train plan ===")
    click.echo(f"  surfaces:    {', '.join(surface_list)}")
    click.echo(f"  optimizer:   {optimizer} budget={budget}")
    click.echo(f"  lm-mix:      {lm_mix} ({len(lm_combos)} combo(s))")
    click.echo(f"  parallelism: {parallelism}")
    click.echo(f"  total runs:  {len(plan)}")
    click.echo()
    hdr = (
        f"  {'#':<3} {'surface':<20} {'task_lm':<32} "
        f"{'reflection_lm':<32} {'cost (low-mid-high)':<25}"
    )
    click.echo(hdr)
    click.echo("  " + "-" * 115)
    for i, run in enumerate(plan):
        task_lm = _MODE_TO_MODEL[run["task_mode"]]
        refl_lm = _MODE_TO_MODEL[run["reflection_mode"]]
        e = estimate_optimize_run(optimizer, budget, task_lm, refl_lm)
        t = e["total"]
        total_low += t["low"]
        total_mid += t["mid"]
        total_high += t["high"]
        click.echo(
            f"  {i+1:<3} {run['surface']:<20} {task_lm:<32} {refl_lm:<32} "
            f"${t['low']:.2f}-${t['mid']:.2f}-${t['high']:.2f}"
        )
    click.echo("  " + "-" * 115)
    click.echo(f"  {'TOTAL':<87} ${total_low:.2f}-${total_mid:.2f}-${total_high:.2f}")
    click.echo()

    if total_mid > 5.0:
        click.echo("⚠  HIGH-COST RUN — confirm with --yes or pass smaller --surfaces.")
    if dry_run:
        click.echo("(dry-run — no children launched)")
        return

    # Launch children
    click.echo(f"Launching {len(plan)} child runs with parallelism={parallelism}...\n")
    active: list[tuple[dict, subprocess.Popen]] = []
    completed: list[dict] = []
    pending = list(plan)

    while pending or active:
        # Refill active pool
        while pending and len(active) < parallelism:
            run = pending.pop(0)
            child_cmd = _build_child_cmd(
                run, optimizer, budget, trainset_file,
            )
            log_path = (
                Path("/tmp") / f"sio_multi_{run['label']}_{int(time.time())}.log"
            )
            click.echo(f"  ▶ START {run['label']}  → log={log_path}")
            f = open(log_path, "w")
            proc = subprocess.Popen(
                child_cmd, stdout=f, stderr=subprocess.STDOUT,
                env={**os.environ},
            )
            run["log"] = str(log_path)
            run["pid"] = proc.pid
            run["start_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            active.append((run, proc))

        # Reap finished
        still_active = []
        for run, proc in active:
            rc = proc.poll()
            if rc is None:
                still_active.append((run, proc))
            else:
                run["exit_code"] = rc
                run["end_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                completed.append(run)
                click.echo(f"  ✓ DONE  {run['label']}  exit={rc}")
        active = still_active

        if active:
            time.sleep(5)  # poll cadence

    # Summary
    click.echo("\n=== multi-train summary ===")
    for run in completed:
        click.echo(f"  {run['label']:<60} exit={run['exit_code']}  log={run['log']}")
    ok = sum(1 for r in completed if r["exit_code"] == 0)
    failed = len(completed) - ok
    click.echo(f"\n  {ok} ok / {failed} failed of {len(completed)} runs")

    # Persist plan + outcomes
    out_dir = Path.home() / ".sio" / "multi_train"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"plan_{int(time.time())}.json"
    out_path.write_text(json.dumps({"plan": plan, "completed": completed}, indent=2))
    click.echo(f"  plan persisted: {out_path}")


def _build_child_cmd(run: dict, optimizer: str, budget: str,
                     trainset_file: str | None) -> list[str]:
    """Build the argv for a child `sio optimize ...` subprocess."""
    cmd = [
        "sio", "optimize",
        "--optimizer", optimizer,
        "--gepa-budget", budget,
        "--task-mode", run["task_mode"],
        "--reflection-mode", run["reflection_mode"],
    ]
    if trainset_file:
        cmd.extend(["--trainset-file", trainset_file])
    return cmd
