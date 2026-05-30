"""HTML renderer for the cohort A/B report (T022).

Self-contained single-file HTML mirroring the dark-theme layout of
``sio.reports.html_report`` (same CSS variables / stat-card / table
idioms) so the two reports feel like one product.
"""

from __future__ import annotations

import html
from typing import Any

_CSS = """
  :root {
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --green: #10b981; --red: #ef4444; --yellow: #f59e0b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 'Helvetica Neue', Arial, sans-serif;
    background: var(--bg); color: var(--text);
    line-height: 1.6; padding: 2rem;
  }
  h1 { font-size: 1.8rem; margin-bottom: 0.5rem; }
  h2 {
    font-size: 1.3rem; margin: 2rem 0 1rem;
    padding-bottom: 0.4rem; border-bottom: 2px solid var(--border);
  }
  .subtitle { color: var(--muted); margin-bottom: 2rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }
  .stat-card {
    background: var(--surface); border-radius: 8px; padding: 1.2rem;
    border: 1px solid var(--border);
  }
  .stat-card .label { color: var(--muted); font-size: 0.85rem; }
  .stat-card .value { font-size: 1.6rem; font-weight: 700; margin-top: 0.3rem; }
  table {
    width: 100%; border-collapse: collapse; background: var(--surface);
    border-radius: 8px; overflow: hidden; margin-bottom: 1rem;
  }
  th {
    background: var(--border); text-align: left; padding: 0.7rem 1rem;
    font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em;
  }
  td { padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .delta-down { color: var(--green); font-weight: 700; }
  .delta-up { color: var(--red); font-weight: 700; }
  .delta-flat { color: var(--muted); }
  .empty-msg { padding: 1rem; text-align: center; color: var(--muted); }
  .badge {
    display: inline-block; padding: 0.15rem 0.6rem; border-radius: 9999px;
    font-size: 0.75rem; font-weight: 600;
  }
  .badge-green { background: #065f4620; color: var(--green); border: 1px solid var(--green); }
  .badge-red   { background: #7f1d1d20; color: var(--red); border: 1px solid var(--red); }
"""


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _delta_html(delta_pct: float | None) -> str:
    if delta_pct is None:
        return '<span class="delta-flat">n/a</span>'
    if delta_pct < 0:
        return f'<span class="delta-down">&#8595; {delta_pct:+.1f}%</span>'
    if delta_pct > 0:
        return f'<span class="delta-up">&#8593; {delta_pct:+.1f}%</span>'
    return '<span class="delta-flat">&#8594; 0%</span>'


def render_html(report: dict[str, Any]) -> str:
    """Render the report dict as a self-contained HTML document."""
    exp = report["experiment"]
    win = report["windows"]
    er = report["error_rate"]
    nec = report.get("new_error_classes", [])
    fd = report.get("flow_delta", {"emerged": [], "died": []})
    sugg = report.get("suggestions", [])

    # New error class rows
    if nec:
        nec_rows = "".join(
            f"<tr><td>{_esc(c.get('error_type'))}</td>"
            f"<td class='num'>{_esc(c.get('count'))}</td>"
            f"<td>{_esc((c.get('sample') or '')[:80])}</td></tr>"
            for c in nec
        )
        nec_table = (
            "<table><thead><tr><th>Error type</th><th>Count</th>"
            f"<th>Sample</th></tr></thead><tbody>{nec_rows}</tbody></table>"
        )
    else:
        nec_table = '<div class="empty-msg">No new error classes vs baseline.</div>'

    # Flow delta rows
    flow_rows = ""
    for f in fd.get("emerged", []):
        flow_rows += (
            f"<tr><td><span class='badge badge-green'>emerged</span></td>"
            f"<td>{_esc(f.get('sequence'))}</td>"
            f"<td class='num'>{_esc(f.get('count'))}</td></tr>"
        )
    for f in fd.get("died", []):
        flow_rows += (
            f"<tr><td><span class='badge badge-red'>died</span></td>"
            f"<td>{_esc(f.get('sequence'))}</td>"
            f"<td class='num'>{_esc(f.get('count'))}</td></tr>"
        )
    if flow_rows:
        flow_table = (
            "<table><thead><tr><th>Change</th><th>Flow</th>"
            f"<th>Count</th></tr></thead><tbody>{flow_rows}</tbody></table>"
        )
    else:
        flow_table = '<div class="empty-msg">No flow changes.</div>'

    # Suggestion rows
    if sugg:
        sugg_rows = "".join(
            f"<tr><td>{_esc(s.get('confidence'))}</td>"
            f"<td>{_esc((s.get('description') or '')[:120])}</td></tr>"
            for s in sugg
        )
        sugg_table = (
            "<table><thead><tr><th>Confidence</th>"
            f"<th>Suggestion</th></tr></thead><tbody>{sugg_rows}</tbody></table>"
        )
    else:
        sugg_table = '<div class="empty-msg">No scoped suggestions.</div>'

    note_block = (
        f'<p class="subtitle">{_esc(exp.get("note"))}</p>' if exp.get("note") else ""
    )

    # Pre-rendered "N errors / Hh" labels (kept short to satisfy line length).
    exp_label = f"{_esc(er['experiment']['count'])} errors / {_esc(er['experiment']['hours'])}h"
    base_label = f"{_esc(er['baseline']['count'])} errors / {_esc(er['baseline']['hours'])}h"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SIO Experiment Report &mdash; {_esc(exp['name'])}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Experiment report &mdash; {_esc(exp['name'])}</h1>
<p class="subtitle">
  status: {_esc(exp['status'])} &middot;
  project: {_esc(exp.get('project') or 'global')} &middot;
  baseline: {_esc(win['baseline_spec'])}
</p>
{note_block}

<h2>Windows</h2>
<div class="grid">
  <div class="stat-card">
    <div class="label">Experiment window</div>
    <div class="value" style="font-size:0.95rem">
      {_esc(win['experiment']['start'])}<br>&rarr; {_esc(win['experiment']['end'])}
    </div>
  </div>
  <div class="stat-card">
    <div class="label">Baseline window</div>
    <div class="value" style="font-size:0.95rem">
      {_esc(win['baseline']['start'])}<br>&rarr; {_esc(win['baseline']['end'])}
    </div>
  </div>
</div>

<h2>Error-rate delta (per hour)</h2>
<div class="grid">
  <div class="stat-card">
    <div class="label">Experiment</div>
    <div class="value">{_esc(er['experiment']['per_hour'])}/h</div>
    <div class="label">{exp_label}</div>
  </div>
  <div class="stat-card">
    <div class="label">Baseline</div>
    <div class="value">{_esc(er['baseline']['per_hour'])}/h</div>
    <div class="label">{base_label}</div>
  </div>
  <div class="stat-card">
    <div class="label">Delta</div>
    <div class="value">{_delta_html(er['delta_pct'])}</div>
    <div class="label">{_esc(er['delta_per_hour'])}/h</div>
  </div>
</div>

<h2>New error classes ({len(nec)})</h2>
{nec_table}

<h2>Flow delta (emerged {len(fd.get('emerged', []))}, died {len(fd.get('died', []))})</h2>
{flow_table}

<h2>Scoped suggestions ({len(sugg)})</h2>
{sugg_table}

</body>
</html>"""
