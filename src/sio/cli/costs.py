"""sio costs — summary of LLM spend across the rolling window."""
from __future__ import annotations

import click

from sio.core.cost import summarize
from sio.core.cost.estimator import estimate_optimize_run


@click.group("costs")
def costs_cmd():
    """Cost transparency commands (Principle XII)."""


@costs_cmd.command("summary")
@click.option("--since-days", default=7, show_default=True, type=int)
def costs_summary(since_days):
    """Show LLM spend summary from ~/.sio/usage.log."""
    s = summarize(since_days=since_days)
    click.echo(f"=== Last {since_days} days ===")
    click.echo(f"Total: ${s['total_cost']:.4f}  Calls: {s['total_calls']}")
    if s["by_model"]:
        click.echo("\nBy model:")
        for m, d in sorted(s["by_model"].items(), key=lambda kv: -kv[1]["cost"]):
            tok = f"{d['in_tok']:,}→{d['out_tok']:,} tok"
            click.echo(
                f"  {m:<40} ${d['cost']:>7.4f}  ({d['calls']} calls, {tok})"
            )
    if s["by_day"]:
        click.echo("\nBy day:")
        for day in sorted(s["by_day"].keys()):
            d = s["by_day"][day]
            click.echo(f"  {day}: ${d['cost']:.4f} ({d['calls']} calls)")


@costs_cmd.command("estimate")
@click.option("--optimizer", default="gepa", show_default=True,
              type=click.Choice(["gepa", "mipro", "bootstrap"]))
@click.option("--budget", default="light", show_default=True,
              type=click.Choice(["light", "medium", "heavy", "any"]))
@click.option("--task-lm", default="gemini/gemini-flash-latest", show_default=True)
@click.option("--reflection-lm", default="gemini/gemini-pro-latest", show_default=True)
def costs_estimate(optimizer, budget, task_lm, reflection_lm):
    """Pre-flight cost band for a hypothetical optimize run."""
    e = estimate_optimize_run(optimizer, budget, task_lm, reflection_lm)
    if "error" in e:
        click.echo(f"Error: {e['error']}", err=True)
        return
    click.echo(f"Estimate — optimizer={optimizer} budget={budget}")
    for role, d in e["by_role"].items():
        click.echo(f"  {role:<12} {d['model']:<40} {d['calls']:>3} calls  "
                   f"${d['low']:.2f}–${d['mid']:.2f}–${d['high']:.2f}")
    t = e["total"]
    click.echo(f"  {'TOTAL':<55} ${t['low']:.2f}–${t['mid']:.2f}–${t['high']:.2f}")
