"""sio.curate — produce a curated training dataset from error_records.

Composable filters surface as CLI flags on ``sio curate``:

    sio curate --since "7 days" --emphasis --classified --pattern <slug>
               --exclude-corrections --has-positive-recovery
               --output ~/.sio/curated/<name>.jsonl

Each filter narrows the candidate set; defaults are conservative.

OUTPUTS
-------
* ``<output>.jsonl`` — one JSON record per row in canonical
  ``PatternToRule`` shape (see ``sio.ground_truth.corpus``).
* ``<output>.preview.md`` — human-readable summary: filter chain, row
  count, category breakdown, 10 sample rows.

The JSONL file is the canonical input for
``sio optimize --trainset-file``; the preview is for human review.

DESIGN
------
This module is read-only — it never writes back to ``error_records``.
``sio approve`` / ``sio promote-to-gold`` remain the ONLY writers to
``ground_truth`` and ``gold_standards``.

History: this module was scoped on 2026-05-15 after two adversarial
audits surfaced that 80%+ of mined records pre-date current rules
(concept drift), and the user explicitly wanted to train only on
TARGETED records — those carrying user-frustration markers (``!!`` /
``??``) within a recent time window. See PRD
``sio_backend_dead_loop_2026-05-15.md``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Filter chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurateFilters:
    """Composable filter chain for ``sio curate``.

    Each field maps to a CLI flag; the WHERE clause is built from the
    fields that are set. Empty filters → no narrowing.
    """

    since: str | None = None  # e.g. "7 days", or "2026-04-22"
    emphasis: bool = False  # require ``!!`` or ``??`` in user_message
    classified: bool = False  # require pattern_id NOT NULL
    pattern: str | None = None  # exact pattern_id slug
    pattern_prefix: str | None = None  # LIKE 'prefix%' on pattern_id
    error_types: tuple[str, ...] = ()  # restrict to these error_type values
    exclude_corrections: bool = True
    exclude_cascade: bool = True
    has_positive_recovery: bool = False  # at least one positive within 600s
    recovery_window_seconds: int = 600
    limit: int | None = None
    seed: int | None = None  # for reproducible random ordering


def _parse_since(since: str) -> str:
    """Convert "7 days" / "1 month" / ISO date → ISO datetime string."""
    s = since.strip().lower()
    if s[0:1].isdigit() and len(s.split()) == 2:
        n_str, unit = s.split()
        n = int(n_str)
        days = {"day": 1, "days": 1, "week": 7, "weeks": 7,
                "month": 30, "months": 30, "year": 365, "years": 365}
        if unit in days:
            delta = timedelta(days=n * days[unit])
            return (datetime.now(timezone.utc) - delta).isoformat()
        if unit in ("hour", "hours"):
            return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()
    # Treat as ISO date or datetime
    return since


def _build_where(filters: CurateFilters) -> tuple[str, list]:
    """Build the WHERE clause + bind params."""
    clauses: list[str] = ["1=1"]
    params: list = []
    if filters.since:
        clauses.append("er.timestamp >= ?")
        params.append(_parse_since(filters.since))
    if filters.emphasis:
        clauses.append("(er.user_message LIKE '%!!%' OR er.user_message LIKE '%??%')")
    if filters.classified:
        clauses.append("er.pattern_id IS NOT NULL AND er.pattern_id != ''")
    if filters.pattern:
        clauses.append("er.pattern_id = ?")
        params.append(filters.pattern)
    if filters.pattern_prefix:
        clauses.append("er.pattern_id LIKE ?")
        params.append(filters.pattern_prefix + "%")
    if filters.error_types:
        placeholders = ", ".join(["?"] * len(filters.error_types))
        clauses.append(f"er.error_type IN ({placeholders})")
        params.extend(filters.error_types)
    if filters.exclude_corrections:
        clauses.append("er.error_type != 'user_correction'")
    if filters.exclude_cascade:
        clauses.append("(er.pattern_id IS NULL OR er.pattern_id NOT LIKE '%cascade%')")
    if filters.has_positive_recovery:
        clauses.append(
            "EXISTS (SELECT 1 FROM positive_records p "
            "WHERE p.source_file = er.source_file "
            "  AND p.timestamp > er.timestamp "
            "  AND (julianday(p.timestamp) - julianday(er.timestamp))*86400 < ?)"
        )
        params.append(filters.recovery_window_seconds)
    return " AND ".join(clauses), params


def select_records(conn: sqlite3.Connection, filters: CurateFilters) -> list[dict]:
    """Run the composed WHERE clause; return matching error_records as dicts."""
    where, params = _build_where(filters)
    order = "ORDER BY er.timestamp DESC"  # newest first
    limit = f"LIMIT {int(filters.limit)}" if filters.limit else ""
    sql = (
        f"SELECT er.id, er.session_id, er.timestamp, er.tool_name, er.error_text, "
        f"er.user_message, er.context_before, er.error_type, er.pattern_id, "
        f"er.source_file, er.parent_session_id, er.is_subagent "
        f"FROM error_records er WHERE {where} {order} {limit}"
    )
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# DSPy.Example projection (matches PatternToRule canonical signature)
# ---------------------------------------------------------------------------


def to_dspy_example(row: dict) -> dict:
    """Project an error_record dict into the canonical PatternToRule shape.

    Returns the SAME dict shape that ``sio.ground_truth.corpus.load_training_corpus``
    emits, so ``sio optimize --trainset-file`` can consume either source
    interchangeably.
    """
    pattern_description = (
        f"[{row.get('error_type')}] {(row.get('error_text') or '')[:300]}"
    )
    example_errors = [(row.get("error_text") or "No example available")[:500]]
    return {
        "inputs": ["pattern_description", "example_errors", "project_context"],
        "data": {
            "pattern_description": pattern_description,
            "example_errors": example_errors,
            "project_context": (row.get("context_before") or "")[:500],
            # Outputs are unknown for raw error_records — leave empty.
            # Downstream consumers (DSPy metric) handle missing outputs.
            "rule_title": "",
            "rule_body": "",
            "rule_rationale": "",
            # Metadata (for traceability)
            "_meta": {
                "error_record_id": row.get("id"),
                "pattern_id": row.get("pattern_id"),
                "tool_name": row.get("tool_name"),
                "timestamp": row.get("timestamp"),
                "session_id": row.get("session_id"),
            },
        },
    }


# ---------------------------------------------------------------------------
# Preview builder
# ---------------------------------------------------------------------------


def write_jsonl(rows: list[dict], out_path: Path) -> None:
    """Write canonical PatternToRule dspy.Example shapes, one per line."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for r in rows:
            f.write(json.dumps(to_dspy_example(r)) + "\n")


def write_preview(
    rows: list[dict],
    filters: CurateFilters,
    out_path: Path,
) -> None:
    """Emit a human-readable Markdown preview of the curated set."""
    cats = Counter()
    tools = Counter()
    types = Counter()
    for r in rows:
        cats[r.get("pattern_id") or "(unclassified)"] += 1
        tools[r.get("tool_name") or "(none)"] += 1
        types[r.get("error_type") or "(none)"] += 1

    lines: list[str] = []
    lines.append("# Curated dataset preview")
    lines.append("")
    lines.append(f"- **Generated:** {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- **Row count:** {len(rows)}")
    lines.append("- **Filters applied:**")
    for k, v in filters.__dict__.items():
        if v not in (None, False, (), ""):
            lines.append(f"  - `{k}` = `{v}`")
    lines.append("")
    lines.append("## Distribution by pattern_id (top 20)")
    lines.append("")
    lines.append("| pattern_id | count |")
    lines.append("|---|---|")
    for cat, n in cats.most_common(20):
        lines.append(f"| `{cat}` | {n} |")
    lines.append("")
    lines.append("## Distribution by tool_name (top 10)")
    lines.append("")
    for tool, n in tools.most_common(10):
        lines.append(f"- `{tool}`: {n}")
    lines.append("")
    lines.append("## Distribution by error_type")
    lines.append("")
    for et, n in types.most_common():
        lines.append(f"- `{et}`: {n}")
    lines.append("")
    lines.append("## 10 sample rows")
    lines.append("")
    for r in rows[:10]:
        et = r.get("error_type", "?")
        pid = r.get("pattern_id") or "(unclassified)"
        tool = r.get("tool_name") or "?"
        ts = r.get("timestamp", "?")
        err = (r.get("error_text") or "")[:120].replace("\n", " ")
        umsg = (r.get("user_message") or "")[:120].replace("\n", " ")
        lines.append(f"### [{et}] `{pid}` — {tool} @ {ts}")
        lines.append(f"- **error:** `{err}`")
        lines.append(f"- **user_message:** `{umsg}`")
        lines.append("")

    out_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def curate(
    db_path: str,
    filters: CurateFilters,
    out_path: Path,
) -> dict:
    """Run the curation, write JSONL + preview, return summary."""
    conn = sqlite3.connect(db_path)
    try:
        rows = select_records(conn, filters)
    finally:
        conn.close()

    write_jsonl(rows, out_path)
    preview_path = out_path.with_suffix(".preview.md")
    write_preview(rows, filters, preview_path)

    return {
        "rows": len(rows),
        "jsonl_path": str(out_path),
        "preview_path": str(preview_path),
    }
