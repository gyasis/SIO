"""Session-start consultant — builds a brief actionable briefing from SIO data.

Queries the SIO database for violations, declining rules, budget warnings,
pending suggestions, and session error trends. Returns a compact markdown
string suitable for injecting into an agent's context at session start.

Public API
----------
    build_session_briefing(db, config=None) -> str
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sio.core.config import SIOConfig, load_config


def _get_rule_file_paths() -> list[str]:
    """Return paths to common instruction files if they exist."""
    candidates = [
        Path.home() / ".claude" / "CLAUDE.md",
        Path.cwd() / "CLAUDE.md",
    ]
    # Also check for rules/ directory
    rules_dir = Path.home() / ".claude" / "rules"
    if rules_dir.is_dir():
        candidates.extend(rules_dir.rglob("*.md"))

    return [str(p) for p in candidates if p.exists()]


def _section_violations(db: sqlite3.Connection) -> str | None:
    """Return a brief violations summary for the last 7 days, or None."""
    from sio.mining.violation_detector import get_violation_report

    rule_paths = _get_rule_file_paths()
    if not rule_paths:
        return None

    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    report = get_violation_report(db, rule_paths, since=since)

    summary = report.get("violation_summary", [])
    if not summary:
        return None

    lines = ["## Recent Violations"]
    for item in summary[:3]:
        lines.append(
            f"- **{item['rule_text'][:60]}** ({item['count']}x, {item['sessions']} sessions)"
        )
    return "\n".join(lines)


def _section_declining_rules(db: sqlite3.Connection) -> str | None:
    """Return rules where error rate increased after application, or None."""
    try:
        rows = db.execute(
            "SELECT vs.error_type, vs.error_rate, vs.rule_applied "
            "FROM velocity_snapshots vs "
            "WHERE vs.rule_applied = 1 "
            "ORDER BY vs.created_at DESC "
            "LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    if not rows:
        return None

    # Group by error_type: check if latest snapshot shows higher rate
    # than the earliest post-application snapshot.
    declining: list[str] = []
    seen: set[str] = set()
    for row in rows:
        etype = row["error_type"]
        if etype in seen:
            continue
        seen.add(etype)
        # Get oldest and newest snapshots for this error_type post-rule
        pair = db.execute(
            "SELECT error_rate FROM velocity_snapshots "
            "WHERE error_type = ? AND rule_applied = 1 "
            "ORDER BY created_at ASC LIMIT 1",
            (etype,),
        ).fetchone()
        latest = db.execute(
            "SELECT error_rate FROM velocity_snapshots "
            "WHERE error_type = ? AND rule_applied = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (etype,),
        ).fetchone()
        if pair and latest and latest["error_rate"] > pair["error_rate"]:
            declining.append(etype)

    if not declining:
        return None

    lines = ["## Declining Rules"]
    for etype in declining[:3]:
        lines.append(f"- `{etype}` error rate increased after rule applied")
    return "\n".join(lines)


def _section_budget(config: SIOConfig) -> str | None:
    """Return budget warning if CLAUDE.md is >80% full, or None."""
    from sio.applier.budget import check_budget

    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    if not claude_md.exists():
        # Try project-local
        claude_md = Path.cwd() / "CLAUDE.md"
    if not claude_md.exists():
        return None

    result = check_budget(claude_md, new_rule_lines=0, config=config)
    utilization = result.current_lines / result.cap if result.cap > 0 else 0.0

    if utilization < 0.80:
        return None

    status_label = "BLOCKED" if result.status == "blocked" else "near capacity"
    return (
        f"## Budget Warning\n"
        f"- CLAUDE.md: {result.current_lines}/{result.cap} lines "
        f"({utilization:.0%}) -- {status_label}"
    )


def _section_pending(db: sqlite3.Connection) -> str | None:
    """Return pending high-confidence suggestions, or None."""
    try:
        rows = db.execute(
            "SELECT id, description, confidence FROM suggestions "
            "WHERE status = 'pending' AND confidence > 0.7 "
            "ORDER BY confidence DESC LIMIT 3"
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    if not rows:
        return None

    lines = ["## Pending Suggestions"]
    for row in rows:
        desc = row["description"][:50]
        lines.append(f"- #{row['id']}: {desc} (conf {row['confidence']:.0%})")
    return "\n".join(lines)


def _section_session_stats(db: sqlite3.Connection) -> str | None:
    """Return error count trend from last 5 sessions, or None."""
    try:
        rows = db.execute(
            "SELECT session_id, error_count FROM session_metrics ORDER BY mined_at DESC LIMIT 5"
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    if not rows:
        return None

    counts = [row["error_count"] for row in rows]
    trend = " -> ".join(str(c) for c in reversed(counts))
    return f"## Session Trend\n- Last 5 error counts: {trend}"


def build_session_briefing(
    db: sqlite3.Connection,
    config: SIOConfig | None = None,
) -> str:
    """Build a brief actionable session briefing from SIO data.

    Queries the database for violations, declining rules, budget warnings,
    pending suggestions, and session error trends. Returns only non-empty
    sections. Designed to be fast (DB reads only, no LLM, no embeddings)
    and compact (<500 chars for typical output).

    Parameters
    ----------
    db:
        Open SQLite connection to the SIO database (with row_factory set).
    config:
        Optional SIOConfig. If None, loads from default location.

    Returns
    -------
    str
        Markdown briefing string. Returns "All clear" if nothing to report.
    """
    if config is None:
        config = load_config()

    sections: list[str] = []

    violations = _section_violations(db)
    if violations:
        sections.append(violations)

    declining = _section_declining_rules(db)
    if declining:
        sections.append(declining)

    budget = _section_budget(config)
    if budget:
        sections.append(budget)

    pending = _section_pending(db)
    if pending:
        sections.append(pending)

    stats = _section_session_stats(db)
    if stats:
        sections.append(stats)

    if not sections:
        return "All clear -- no issues detected."

    return "\n\n".join(sections)
