"""Self-contained HTML report generator for SIO.

Produces a standalone HTML file with embedded CSS and JavaScript.
Uses ``string.Template`` for variable substitution.  Chart.js is loaded
from CDN with a graceful fallback comment.

Sections:
  1. Session Metrics Dashboard (tokens, cost, cache efficiency over time)
  2. Error Trend Chart (30-day rolling line chart)
  3. Pattern Table (sortable, confidence bars, grade badges)
  4. Suggestion Cards (copy button using navigator.clipboard)
  5. Learning Velocity Graph (error rate per type over time)

Implements FR-047 and FR-048.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from string import Template
from typing import Any

# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------


def _query_session_metrics(
    conn: sqlite3.Connection,
    days: int,
) -> list[dict[str, Any]]:
    """Return session_metrics rows from the last *days* days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT session_id, total_input_tokens, total_output_tokens, "
        "total_cache_read_tokens, total_cache_create_tokens, "
        "cache_hit_ratio, total_cost_usd, session_duration_seconds, "
        "message_count, tool_call_count, error_count, "
        "correction_count, positive_signal_count, mined_at "
        "FROM session_metrics WHERE mined_at >= ? "
        "ORDER BY mined_at ASC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_error_trend(
    conn: sqlite3.Connection,
    days: int,
) -> list[dict[str, Any]]:
    """Return daily error counts for the last *days* days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT DATE(timestamp) AS day, COUNT(*) AS count "
        "FROM error_records WHERE timestamp >= ? "
        "GROUP BY DATE(timestamp) ORDER BY day ASC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_patterns(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all patterns ordered by rank_score DESC."""
    rows = conn.execute(
        "SELECT pattern_id, description, tool_name, error_count, "
        "session_count, first_seen, last_seen, rank_score, grade, "
        "created_at, updated_at "
        "FROM patterns ORDER BY rank_score DESC",
    ).fetchall()
    return [dict(r) for r in rows]


def _query_suggestions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return pending/approved suggestions."""
    rows = conn.execute(
        "SELECT id, description, confidence, proposed_change, "
        "target_file, change_type, status, ai_explanation, created_at "
        "FROM suggestions WHERE status IN ('pending', 'approved') "
        "ORDER BY confidence DESC",
    ).fetchall()
    return [dict(r) for r in rows]


def _query_velocity(
    conn: sqlite3.Connection,
    days: int,
) -> list[dict[str, Any]]:
    """Return velocity snapshots for the last *days* days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT error_type, session_id, error_rate, "
        "error_count_in_window, window_start, window_end, "
        "rule_applied, created_at "
        "FROM velocity_snapshots WHERE created_at >= ? "
        "ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# JavaScript data serialization
# ---------------------------------------------------------------------------


def _metrics_to_js(metrics: list[dict[str, Any]]) -> str:
    """Convert session metrics to JS arrays for Chart.js."""
    labels = []
    tokens = []
    costs = []
    cache_ratios = []
    for m in metrics:
        labels.append((m.get("mined_at") or "")[:10])
        tokens.append((m.get("total_input_tokens") or 0) + (m.get("total_output_tokens") or 0))
        costs.append(round(m.get("total_cost_usd") or 0, 4))
        cache_ratios.append(round((m.get("cache_hit_ratio") or 0) * 100, 1))
    return (
        f"const metricsLabels = {json.dumps(labels)};\n"
        f"const metricsTokens = {json.dumps(tokens)};\n"
        f"const metricsCosts = {json.dumps(costs)};\n"
        f"const metricsCacheRatios = {json.dumps(cache_ratios)};"
    )


def _error_trend_to_js(trend: list[dict[str, Any]]) -> str:
    labels = [t.get("day", "") for t in trend]
    counts = [t.get("count", 0) for t in trend]
    return f"const errorLabels = {json.dumps(labels)};\nconst errorCounts = {json.dumps(counts)};"


def _velocity_to_js(velocity: list[dict[str, Any]]) -> str:
    """Group velocity data by error_type for multi-line chart."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for v in velocity:
        et = v.get("error_type", "unknown")
        by_type.setdefault(et, []).append(v)

    datasets_js: list[dict[str, Any]] = []
    colors = [
        "#3b82f6",
        "#ef4444",
        "#10b981",
        "#f59e0b",
        "#8b5cf6",
        "#ec4899",
        "#06b6d4",
        "#84cc16",
    ]
    all_labels: list[str] = sorted(
        {(v.get("window_end") or v.get("created_at", ""))[:10] for v in velocity}
    )

    for idx, (etype, snapshots) in enumerate(sorted(by_type.items())):
        data_map = {
            (s.get("window_end") or s.get("created_at", ""))[:10]: s.get("error_rate", 0)
            for s in snapshots
        }
        data = [round(data_map.get(lbl, 0), 3) for lbl in all_labels]
        color = colors[idx % len(colors)]
        datasets_js.append(
            {
                "label": etype,
                "data": data,
                "borderColor": color,
                "backgroundColor": color + "33",
                "tension": 0.3,
                "fill": False,
            }
        )

    return (
        f"const velocityLabels = {json.dumps(all_labels)};\n"
        f"const velocityDatasets = {json.dumps(datasets_js)};"
    )


# ---------------------------------------------------------------------------
# HTML building blocks
# ---------------------------------------------------------------------------


def _build_pattern_rows(patterns: list[dict[str, Any]]) -> str:
    """Build HTML table rows for patterns."""
    if not patterns:
        return '<tr><td colspan="7" class="empty-msg">No patterns discovered yet.</td></tr>'
    rows: list[str] = []
    for p in patterns:
        confidence = p.get("rank_score", 0)
        bar_width = min(int(confidence * 100), 100)
        grade = p.get("grade") or "emerging"
        grade_class = {
            "established": "badge-green",
            "strong": "badge-blue",
            "emerging": "badge-yellow",
            "declining": "badge-red",
        }.get(grade, "badge-yellow")
        rows.append(
            f"<tr>"
            f"<td>{_esc(p.get('pattern_id') or str(p.get('id', '')))}</td>"
            f"<td>{_esc(p.get('description') or '')}</td>"
            f"<td>{_esc(p.get('tool_name') or '-')}</td>"
            f'<td class="num">{p.get("error_count", 0)}</td>'
            f'<td class="num">{p.get("session_count", 0)}</td>'
            f'<td><div class="bar-container">'
            f'<div class="bar-fill" style="width:{bar_width}%"></div>'
            f'<span class="bar-label">{confidence:.2f}</span>'
            f"</div></td>"
            f'<td><span class="badge {grade_class}">{grade}</span></td>'
            f"</tr>"
        )
    return "\n".join(rows)


def _build_suggestion_cards(suggestions: list[dict[str, Any]]) -> str:
    """Build HTML suggestion cards with copy buttons."""
    if not suggestions:
        return '<div class="empty-msg">No suggestions pending.</div>'
    cards: list[str] = []
    for s in suggestions:
        sid = s.get("id", "?")
        desc = _esc(s.get("description") or "")
        change = _esc(s.get("proposed_change") or "")
        target = _esc(s.get("target_file") or "")
        conf = s.get("confidence", 0)
        status = s.get("status", "pending")
        explanation = _esc(s.get("ai_explanation") or "")
        cards.append(
            f'<div class="card">'
            f'<div class="card-header">'
            f'<span class="card-id">#{sid}</span>'
            f'<span class="badge badge-blue">{status}</span>'
            f'<span class="conf-label">{conf:.0%} confidence</span>'
            f"</div>"
            f'<div class="card-body">'
            f"<p><strong>{desc}</strong></p>"
            f'<p class="target">Target: {target}</p>'
            + ('<p class="explanation">' + explanation + "</p>" if explanation else "")
            + f'<pre class="rule-text" id="rule-{sid}">{change}</pre>'
            f'<button class="copy-btn" onclick="copyRule({sid})">'
            f"Copy Rule</button>"
            f"</div></div>"
        )
    return "\n".join(cards)


def _esc(text: str) -> str:
    """Escape HTML entities."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Main template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SIO Report &mdash; ${report_date}</title>
<style>
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
  .chart-container {
    background: var(--surface); border-radius: 8px; padding: 1.5rem;
    border: 1px solid var(--border); margin-bottom: 1.5rem;
  }
  canvas { max-height: 320px; }
  table {
    width: 100%; border-collapse: collapse; background: var(--surface);
    border-radius: 8px; overflow: hidden;
  }
  th {
    background: var(--border); text-align: left; padding: 0.7rem 1rem;
    font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em;
    cursor: pointer; user-select: none;
  }
  th:hover { background: #475569; }
  td { padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .empty-msg { padding: 2rem; text-align: center; color: var(--muted); }
  .bar-container {
    position: relative; height: 20px; background: var(--border);
    border-radius: 4px; overflow: hidden; min-width: 80px;
  }
  .bar-fill {
    height: 100%; background: var(--accent); border-radius: 4px;
    transition: width 0.3s;
  }
  .bar-label {
    position: absolute; right: 6px; top: 1px; font-size: 0.75rem;
    color: var(--text); font-weight: 600;
  }
  .badge {
    display: inline-block; padding: 0.15rem 0.6rem; border-radius: 9999px;
    font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
  }
  .badge-green { background: #065f4620; color: var(--green); border: 1px solid var(--green); }
  .badge-blue  { background: #1e40af20; color: var(--accent); border: 1px solid var(--accent); }
  .badge-yellow { background: #78350f20; color: var(--yellow); border: 1px solid var(--yellow); }
  .badge-red   { background: #7f1d1d20; color: var(--red); border: 1px solid var(--red); }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; margin-bottom: 1rem; overflow: hidden;
  }
  .card-header {
    display: flex; align-items: center; gap: 0.8rem;
    padding: 0.8rem 1rem; background: var(--border);
  }
  .card-id { font-weight: 700; }
  .conf-label { margin-left: auto; color: var(--muted); font-size: 0.85rem; }
  .card-body { padding: 1rem; }
  .card-body p { margin-bottom: 0.5rem; }
  .target { color: var(--muted); font-size: 0.85rem; }
  .explanation { color: var(--muted); font-size: 0.9rem; font-style: italic; }
  pre.rule-text {
    background: var(--bg); padding: 0.8rem; border-radius: 6px;
    overflow-x: auto; font-size: 0.85rem; margin: 0.8rem 0;
    white-space: pre-wrap; word-break: break-word;
  }
  .copy-btn {
    background: var(--accent); color: #fff; border: none;
    padding: 0.4rem 1rem; border-radius: 6px; cursor: pointer;
    font-size: 0.85rem; font-weight: 600;
  }
  .copy-btn:hover { opacity: 0.85; }
  .copy-btn.copied { background: var(--green); }
  footer { margin-top: 3rem; color: var(--muted); font-size: 0.8rem; text-align: center; }
</style>
</head>
<body>

<h1>SIO Report</h1>
<p class="subtitle">
  ${days}-day window ending ${report_date}
  &mdash; ${session_count} sessions analyzed
</p>

<!-- ===== Section 1: Session Metrics Dashboard ===== -->
<h2>Session Metrics Dashboard</h2>
<div class="grid">
  <div class="stat-card">
    <div class="label">Total Tokens</div>
    <div class="value">${total_tokens}</div>
  </div>
  <div class="stat-card">
    <div class="label">Total Cost</div>
    <div class="value">$$${total_cost}</div>
  </div>
  <div class="stat-card">
    <div class="label">Avg Cache Efficiency</div>
    <div class="value">${avg_cache}%</div>
  </div>
  <div class="stat-card">
    <div class="label">Total Errors</div>
    <div class="value">${total_errors}</div>
  </div>
</div>
<div class="chart-container">
  <canvas id="metricsChart"></canvas>
</div>

<!-- ===== Section 2: Error Trend Chart ===== -->
<h2>Error Trend (${days}-day rolling)</h2>
<div class="chart-container">
  <canvas id="errorChart"></canvas>
</div>

<!-- ===== Section 3: Pattern Table ===== -->
<h2>Discovered Patterns</h2>
<table id="patternTable">
<thead>
<tr>
  <th onclick="sortTable(0)">ID</th>
  <th onclick="sortTable(1)">Description</th>
  <th onclick="sortTable(2)">Tool</th>
  <th onclick="sortTable(3)">Errors</th>
  <th onclick="sortTable(4)">Sessions</th>
  <th onclick="sortTable(5)">Confidence</th>
  <th onclick="sortTable(6)">Grade</th>
</tr>
</thead>
<tbody>
${pattern_rows}
</tbody>
</table>

<!-- ===== Section 4: Suggestion Cards ===== -->
<h2>Suggestions</h2>
${suggestion_cards}

<!-- ===== Section 5: Learning Velocity Graph ===== -->
<h2>Learning Velocity</h2>
<div class="chart-container">
  <canvas id="velocityChart"></canvas>
</div>

<footer>
  Generated by SIO &mdash; Self-Improving Organism &mdash; ${report_date}
</footer>

<!-- Chart.js from CDN (fallback: charts will not render without it) -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<!-- If Chart.js fails to load (offline), charts will show empty canvases.
     The rest of the report (tables, cards, stats) remains fully functional. -->

<script>
// ---- Data (injected by Python) ----
${metrics_js}
${error_trend_js}
${velocity_js}

// ---- Charts ----
document.addEventListener('DOMContentLoaded', function() {
  if (typeof Chart === 'undefined') {
    console.warn('Chart.js not loaded. Charts will not render.');
    return;
  }
  const darkGrid = { color: 'rgba(148,163,184,0.15)' };
  const darkTick = { color: '#94a3b8' };

  // Session Metrics Chart
  if (metricsLabels.length > 0) {
    new Chart(document.getElementById('metricsChart'), {
      type: 'line',
      data: {
        labels: metricsLabels,
        datasets: [
          {
            label: 'Tokens', data: metricsTokens,
            borderColor: '#3b82f6', backgroundColor: '#3b82f633',
            yAxisID: 'y', tension: 0.3, fill: true
          },
          {
            label: 'Cost ($)', data: metricsCosts,
            borderColor: '#10b981', backgroundColor: '#10b98133',
            yAxisID: 'y1', tension: 0.3, fill: false
          },
          {
            label: 'Cache %', data: metricsCacheRatios,
            borderColor: '#f59e0b', backgroundColor: '#f59e0b33',
            yAxisID: 'y2', tension: 0.3, fill: false
          }
        ]
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: darkGrid, ticks: darkTick },
          y:  { position: 'left', grid: darkGrid, ticks: darkTick,
                title: { display: true, text: 'Tokens', color: '#94a3b8' } },
          y1: { position: 'right', grid: { drawOnChartArea: false },
                ticks: darkTick,
                title: { display: true, text: 'Cost ($)', color: '#94a3b8' } },
          y2: { position: 'right', grid: { drawOnChartArea: false },
                ticks: darkTick, min: 0, max: 100,
                title: { display: true, text: 'Cache %', color: '#94a3b8' } }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });
  }

  // Error Trend Chart
  if (errorLabels.length > 0) {
    new Chart(document.getElementById('errorChart'), {
      type: 'line',
      data: {
        labels: errorLabels,
        datasets: [{
          label: 'Errors per day', data: errorCounts,
          borderColor: '#ef4444', backgroundColor: '#ef444433',
          tension: 0.3, fill: true
        }]
      },
      options: {
        responsive: true,
        scales: {
          x: { grid: darkGrid, ticks: darkTick },
          y: { beginAtZero: true, grid: darkGrid, ticks: darkTick }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });
  }

  // Velocity Chart
  if (velocityLabels.length > 0) {
    new Chart(document.getElementById('velocityChart'), {
      type: 'line',
      data: { labels: velocityLabels, datasets: velocityDatasets },
      options: {
        responsive: true,
        scales: {
          x: { grid: darkGrid, ticks: darkTick },
          y: { beginAtZero: true, grid: darkGrid, ticks: darkTick,
               title: { display: true, text: 'Error Rate', color: '#94a3b8' } }
        },
        plugins: { legend: { labels: { color: '#e2e8f0' } } }
      }
    });
  }
});

// ---- Copy button ----
function copyRule(id) {
  var el = document.getElementById('rule-' + id);
  if (!el) return;
  var text = el.textContent;
  navigator.clipboard.writeText(text).then(function() {
    var btns = document.querySelectorAll('.copy-btn');
    btns.forEach(function(b) {
      if (b.getAttribute('onclick') === 'copyRule(' + id + ')') {
        b.textContent = 'Copied!';
        b.classList.add('copied');
        setTimeout(function() {
          b.textContent = 'Copy Rule';
          b.classList.remove('copied');
        }, 2000);
      }
    });
  });
}

// ---- Sortable table ----
function sortTable(col) {
  var table = document.getElementById('patternTable');
  var tbody = table.querySelector('tbody');
  var rows = Array.from(tbody.querySelectorAll('tr'));
  var asc = table.dataset.sortCol == col && table.dataset.sortDir !== 'asc';
  table.dataset.sortCol = col;
  table.dataset.sortDir = asc ? 'asc' : 'desc';
  rows.sort(function(a, b) {
    var va = a.cells[col].textContent.trim();
    var vb = b.cells[col].textContent.trim();
    var na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) return asc ? na - nb : nb - na;
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
}
</script>
</body>
</html>
""")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_html_report(
    db: sqlite3.Connection,
    days: int = 30,
) -> str:
    """Generate a self-contained HTML report string.

    Parameters
    ----------
    db:
        Open sqlite3 connection (with SIO schema initialized).
    days:
        Lookback window in days.

    Returns
    -------
    Complete HTML document as a string.
    """
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Query all data
    metrics = _query_session_metrics(db, days)
    error_trend = _query_error_trend(db, days)
    patterns = _query_patterns(db)
    suggestions = _query_suggestions(db)
    velocity = _query_velocity(db, days)

    # Aggregate stats
    total_tokens = sum(
        (m.get("total_input_tokens") or 0) + (m.get("total_output_tokens") or 0) for m in metrics
    )
    total_cost = sum(m.get("total_cost_usd") or 0 for m in metrics)
    cache_ratios = [m.get("cache_hit_ratio") or 0 for m in metrics]
    avg_cache = round(sum(cache_ratios) / len(cache_ratios) * 100, 1) if cache_ratios else 0.0
    total_errors = sum(m.get("error_count") or 0 for m in metrics)

    # Format large numbers
    def _fmt_num(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    return _HTML_TEMPLATE.safe_substitute(
        report_date=report_date,
        days=str(days),
        session_count=str(len(metrics)),
        total_tokens=_fmt_num(total_tokens),
        total_cost=f"{total_cost:.2f}",
        avg_cache=str(avg_cache),
        total_errors=str(total_errors),
        metrics_js=_metrics_to_js(metrics),
        error_trend_js=_error_trend_to_js(error_trend),
        velocity_js=_velocity_to_js(velocity),
        pattern_rows=_build_pattern_rows(patterns),
        suggestion_cards=_build_suggestion_cards(suggestions),
    )
