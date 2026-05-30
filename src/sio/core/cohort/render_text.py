"""Terminal (rich-markup) renderer for the cohort A/B report (T021).

Returns a string with rich console markup. ``render_report`` prints it
to a Console; tests can assert on the plain substrings.
"""

from __future__ import annotations

from typing import Any


def _fmt_delta_pct(delta_pct: float | None) -> str:
    if delta_pct is None:
        return "n/a (no baseline activity)"
    arrow = "↓" if delta_pct < 0 else ("↑" if delta_pct > 0 else "→")
    color = "green" if delta_pct < 0 else ("red" if delta_pct > 0 else "dim")
    return f"[{color}]{arrow} {delta_pct:+.1f}%[/{color}]"


def render_text(report: dict[str, Any]) -> str:
    """Render the report dict as a rich-markup multi-line string."""
    exp = report["experiment"]
    win = report["windows"]
    er = report["error_rate"]

    lines: list[str] = []
    lines.append(f"[bold cyan]Experiment report — {exp['name']}[/bold cyan]")
    lines.append(f"  status:  {exp['status']}")
    lines.append(f"  window:  {win['experiment']['start']} … {win['experiment']['end']}")
    lines.append(
        f"  baseline ({win['baseline_spec']}): "
        f"{win['baseline']['start']} … {win['baseline']['end']}"
    )
    if exp.get("project"):
        lines.append(f"  project: {exp['project']}")
    if exp.get("note"):
        lines.append(f"  note:    {exp['note']}")
    lines.append("")

    # Error-rate delta (T017)
    lines.append("[bold]Error-rate delta (per hour)[/bold]")
    lines.append(
        f"  experiment: {er['experiment']['count']} errors "
        f"over {er['experiment']['hours']}h "
        f"= {er['experiment']['per_hour']}/h"
    )
    lines.append(
        f"  baseline:   {er['baseline']['count']} errors "
        f"over {er['baseline']['hours']}h "
        f"= {er['baseline']['per_hour']}/h"
    )
    lines.append(
        f"  delta:      {er['delta_per_hour']:+}/h  "
        f"({_fmt_delta_pct(er['delta_pct'])})"
    )
    lines.append("")

    # New error classes (T018)
    nec = report.get("new_error_classes", [])
    lines.append(f"[bold]New error classes[/bold] ({len(nec)})")
    if nec:
        for c in nec[:20]:
            lines.append(
                f"  • {c.get('error_type', '?')}: "
                f"{c.get('count', 0)}× — {(c.get('sample') or '')[:60]}"
            )
    else:
        lines.append("  [dim](none — no new clusters vs baseline)[/dim]")
    lines.append("")

    # Flow delta (T019)
    fd = report.get("flow_delta", {"emerged": [], "died": []})
    emerged = fd.get("emerged", [])
    died = fd.get("died", [])
    lines.append(
        f"[bold]Flow delta[/bold] (emerged {len(emerged)}, died {len(died)})"
    )
    for f in emerged[:10]:
        lines.append(f"  [green]+ {f.get('sequence', '?')}[/green] ({f.get('count', 0)}×)")
    for f in died[:10]:
        lines.append(f"  [red]- {f.get('sequence', '?')}[/red] ({f.get('count', 0)}×)")
    if not emerged and not died:
        lines.append("  [dim](no flow changes)[/dim]")
    lines.append("")

    # Scoped suggestions (T020)
    sugg = report.get("suggestions", [])
    lines.append(f"[bold]Scoped suggestions[/bold] ({len(sugg)})")
    if sugg:
        for s in sugg[:10]:
            lines.append(
                f"  • [{s.get('confidence', '?')}] {(s.get('description') or '')[:70]}"
            )
    else:
        lines.append("  [dim](none generated for this window)[/dim]")

    return "\n".join(lines)
