"""SIO CLI — Self-Improving Organism command-line interface."""

import json as _json
import os
from contextlib import contextmanager
from importlib.metadata import version as pkg_version

import click

_DEFAULT_DB_DIR = os.path.expanduser("~/.sio/claude-code")


@contextmanager
def _db_conn(db_path):
    """Context manager that opens init_db() and guarantees close."""
    from sio.core.db.schema import init_db

    conn = init_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _get_sio_db_conn():
    """Open (and init) the SIO main database. Returns conn or None.

    Extracted as a module-level function so tests can monkeypatch it.
    """
    from sio.core.db.schema import init_db

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        return None
    return init_db(db_path)


@click.group()
@click.version_option(version=pkg_version("sio"))
def cli():
    """SIO: Self-Improving Organism for AI coding CLIs."""
    pass


@cli.command()
@click.option("--platform", default="claude-code", help="Platform filter.")
@click.option("--skill", default=None, help="Skill name filter.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.",
)
def health(platform, skill, fmt):
    """Show per-skill health metrics."""
    from sio.core.health.aggregator import compute_health

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    if not os.path.exists(db_path):
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)

    with _db_conn(db_path) as conn:
        results = compute_health(conn, platform=platform, skill=skill)

    if fmt == "json":
        data = [
            {
                "platform": r.platform,
                "skill_name": r.skill_name,
                "total_invocations": r.total_invocations,
                "satisfied_count": r.satisfied_count,
                "unsatisfied_count": r.unsatisfied_count,
                "unlabeled_count": r.unlabeled_count,
                "satisfaction_rate": r.satisfaction_rate,
                "flagged": r.flagged,
            }
            for r in results
        ]
        click.echo(_json.dumps(data, indent=2))
    else:
        if not results:
            click.echo("No health data available.")
            return
        click.echo(f"{'Skill':<30} {'Total':>6} {'Sat%':>6} {'Flagged':>8}")
        click.echo("-" * 52)
        for r in results:
            rate_str = f"{r.satisfaction_rate:.0%}" if r.satisfaction_rate is not None else "N/A"
            flag_str = "YES" if r.flagged else ""
            click.echo(f"{r.skill_name:<30} {r.total_invocations:>6} {rate_str:>6} {flag_str:>8}")


@cli.command()
@click.option("--platform", default="claude-code", help="Platform filter.")
@click.option("--session", default=None, help="Session ID filter.")
@click.option("--limit", default=20, help="Max items to review.")
def review(platform, session, limit):
    """Batch-review unlabeled invocations."""
    from sio.core.feedback.batch_review import apply_label, get_reviewable

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    if not os.path.exists(db_path):
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)

    with _db_conn(db_path) as conn:
        items = get_reviewable(
            conn, platform, session_id=session, limit=limit,
        )

        if not items:
            click.echo("No unlabeled invocations to review.")
            return

        skew = items[0].get("skew_warning") if items else None
        if skew:
            click.echo(f"Warning: {skew}")
            click.echo()

        labeled = 0
        for i, item in enumerate(items, 1):
            click.echo(f"[{i}/{len(items)}] {item['actual_action']}")
            click.echo(f"  Message: {item['user_message'][:80]}")
            click.echo(f"  Time:    {item['timestamp']}")

            choice = click.prompt(
                "  Label [++/--/s(kip)/q(uit)]",
                type=str,
                default="s",
            )
            if choice == "q":
                break
            if choice in ("++", "--"):
                note = click.prompt("  Note (optional)", default="", type=str)
                apply_label(
                    conn, item["id"], choice, note or None,
                )
                labeled += 1
            click.echo()

    click.echo(f"Labeled {labeled} invocations.")


@cli.command()
@click.argument("skill_name")
@click.option("--platform", default="claude-code", help="Platform filter.")
@click.option(
    "--optimizer",
    type=click.Choice(["gepa", "miprov2", "bootstrap"]),
    default="gepa",
    help="DSPy optimizer to use.",
)
@click.option("--dry-run", is_flag=True, help="Show diff without applying.")
def optimize(skill_name, platform, optimizer, dry_run):
    """Run prompt optimization for a skill."""
    click.echo(
        "\u26a0\ufe0f  'sio optimize' is deprecated."
        " Use 'sio optimize-suggestions' instead.",
        err=True,
    )
    from sio.core.dspy.optimizer import optimize as run_opt

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    if not os.path.exists(db_path):
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)

    with _db_conn(db_path) as conn:
        result = run_opt(
            conn, skill_name=skill_name, platform=platform,
            optimizer=optimizer, dry_run=dry_run,
        )

        if result["status"] == "error":
            click.echo(f"Cannot optimize: {result.get('reason', 'unknown')}")
            raise SystemExit(1)

        click.echo(f"Optimization for '{skill_name}' ({optimizer}):")
        click.echo()
        click.echo(result.get("diff", ""))
        click.echo()

        if dry_run:
            click.echo("[dry-run] No changes applied.")
            return

        click.echo(f"Status: {result['status']}")
        if result.get("optimization_id"):
            click.echo(f"Optimization ID: {result['optimization_id']}")

        choice = click.prompt(
            "[a(pprove)/r(eject)/d(etails)]",
            type=click.Choice(["a", "r", "d"]),
            default="r",
        )

        if choice == "a":
            click.echo("Optimization approved (pending deployment).")
        elif choice == "d":
            click.echo(f"Full result: {_json.dumps(result, indent=2, default=str)}")
        else:
            click.echo("Optimization rejected.")


@cli.command()
@click.option(
    "--platform",
    type=click.Choice(["claude-code"]),
    default="claude-code",
    help="Platform to install.",
)
@click.option("--auto", "auto_detect", is_flag=True, help="Auto-detect platform.")
def install(platform, auto_detect):
    """Install SIO for a platform."""
    from sio.adapters.claude_code.installer import install as do_install

    click.echo(f"Installing SIO for {platform}...")
    result = do_install()
    click.echo(f"Database: {result['db_path']}")
    click.echo(f"Hooks registered: {result['hooks_registered']}")
    skills = result.get("skills_installed", [])
    if skills:
        click.echo(f"Skills installed ({len(skills)}):")
        for s in skills:
            click.echo(f"  - {s}")
    click.echo("Installation complete.")


@cli.command()
@click.option("--platform", default="claude-code", help="Platform filter.")
@click.option("--days", default=90, help="Purge records older than N days.")
@click.option("--dry-run", is_flag=True, help="Show count without deleting.")
def purge(platform, days, dry_run):
    """Purge old telemetry records."""
    from sio.core.db.retention import purge as do_purge

    db_path = os.path.join(
        os.path.expanduser(f"~/.sio/{platform}"),
        "behavior_invocations.db",
    )
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    with _db_conn(db_path) as conn:
        count = do_purge(conn, older_than_days=days, dry_run=dry_run)

    if dry_run:
        click.echo(f"Would purge {count} records older than {days} days.")
    else:
        click.echo(f"Purged {count} records older than {days} days.")


@cli.command()
@click.option("--platform", default="claude-code", help="Platform filter.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["json", "csv"]),
    default="json",
    help="Export format.",
)
@click.option("--output", "-o", default=None, help="Output file path.")
def export(platform, fmt, output):
    """Export telemetry data."""
    import csv
    import io

    db_path = os.path.join(
        os.path.expanduser(f"~/.sio/{platform}"),
        "behavior_invocations.db",
    )
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    with _db_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM behavior_invocations").fetchall()

    data = [dict(r) for r in rows]

    if fmt == "json":
        text = _json.dumps(data, indent=2, default=str)
    else:
        if not data:
            click.echo("No data to export.")
            return
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        text = buf.getvalue()

    if output:
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"Exported {len(data)} records to {output}")
    else:
        click.echo(text)


# ---------------------------------------------------------------------------
# v2 pipeline commands
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--since", required=True,
    help=(
        'Time window: "3 days", "2 weeks", "1 month",'
        ' "6h", "yesterday", "3 days ago", "2026-01-15".'
    ),
)
@click.option("--project", default=None, help="Filter by project name.")
@click.option(
    "--source",
    type=click.Choice(["specstory", "jsonl", "both"]),
    default="both",
    help="Source type.",
)
def mine(since, project, source):
    """Mine recent sessions for errors and failures."""
    from pathlib import Path

    from sio.mining.pipeline import run_mine

    db_path = os.path.expanduser("~/.sio/sio.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    source_dirs = []
    specstory_dir = Path(os.path.expanduser("~/.specstory/history"))
    jsonl_dir = Path(os.path.expanduser("~/.claude/projects"))

    if source in ("specstory", "both") and specstory_dir.exists():
        source_dirs.append(specstory_dir)
    if source in ("jsonl", "both") and jsonl_dir.exists():
        source_dirs.append(jsonl_dir)

    if not source_dirs:
        click.echo("No source directories found. Checked:")
        if source in ("specstory", "both"):
            click.echo(f"  SpecStory: {specstory_dir}")
        if source in ("jsonl", "both"):
            click.echo(f"  JSONL:     {jsonl_dir}")
        return

    with _db_conn(db_path) as conn:
        result = run_mine(conn, source_dirs, since, source, project)

    click.echo(f"Scanned {result['total_files_scanned']} files")
    click.echo(f"Found {result['errors_found']} errors")


@cli.command()
@click.option(
    "--type", "error_type", default=None,
    type=click.Choice([
        "tool_failure", "user_correction",
        "repeated_attempt", "undo", "agent_admission",
    ]),
    help="Filter by error type.",
)
@click.option(
    "--project", default=None,
    help="Filter by project name (substring match on source path).",
)
def patterns(error_type, project):
    """Show discovered error patterns ranked by importance."""
    from rich.console import Console
    from rich.table import Table

    from sio.clustering.pattern_clusterer import cluster_errors
    from sio.clustering.ranker import rank_patterns
    from sio.core.db.queries import get_error_records

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        # Get all error records from DB
        errors = get_error_records(conn, project=project)
        if not errors:
            click.echo("No errors mined yet. Run 'sio mine --since \"7 days\"' first.")
            return

        # Filter by type if requested
        if error_type:
            errors = [e for e in errors if e.get("error_type") == error_type]
            if not errors:
                click.echo(f"No '{error_type}' errors found.")
                return

    # Cluster and rank
    clustered = cluster_errors(errors)
    ranked = rank_patterns(clustered)

    title = (
        f"Error Patterns — {error_type}"
        if error_type
        else "Error Patterns (ranked by importance)"
    )
    console = Console()
    table = Table(title=title)
    table.add_column("#", style="bold")
    table.add_column("Pattern", style="cyan")
    table.add_column("Errors", justify="right")
    table.add_column("Sessions", justify="right")
    table.add_column("Last Seen")
    table.add_column("Score", justify="right")

    for i, p in enumerate(ranked, 1):
        table.add_row(
            str(i),
            p.get("description", p.get("pattern_id", "unknown"))[:60],
            str(p.get("error_count", 0)),
            str(p.get("session_count", 0)),
            (p.get("last_seen") or "")[:10],
            f"{p.get('rank_score', 0):.2f}",
        )

    console.print(table)


@cli.command()
@click.option(
    "--type", "error_type", default=None,
    type=click.Choice([
        "tool_failure", "user_correction",
        "repeated_attempt", "undo", "agent_admission",
    ]),
    help="Filter by error type.",
)
@click.option("--limit", "-n", default=20, help="Max errors to show.")
@click.option(
    "--grep", "-g", "grep_term", default=None,
    help=(
        "Search content for keyword(s). Comma-separated"
        " for OR logic (e.g. 'placeholder,hardcoded,stub')."
    ),
)
@click.option(
    "--project", default=None,
    help="Filter by project name (substring match on source path).",
)
@click.option(
    "--exclude-type", "exclude_types", default=None,
    help="Exclude error types. Comma-separated (e.g. 'repeated_attempt,tool_failure').",
)
def errors(error_type, limit, grep_term, project, exclude_types):
    """Browse mined errors with optional type and content filters."""
    from rich.console import Console
    from rich.table import Table

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        # Build query based on filters
        where_clauses = ["1=1"]
        params: list = []

        if error_type:
            where_clauses.append("error_type = ?")
            params.append(error_type)

        if exclude_types:
            excluded = [t.strip() for t in exclude_types.split(",") if t.strip()]
            placeholders = ", ".join(["?"] * len(excluded))
            where_clauses.append(f"error_type NOT IN ({placeholders})")
            params.extend(excluded)

        if project:
            where_clauses.append("source_file LIKE ?")
            params.append(f"%{project}%")

        if grep_term:
            # Comma-separated terms use OR logic across all content fields
            terms = [t.strip() for t in grep_term.split(",") if t.strip()]
            term_clauses = []
            for term in terms:
                term_clauses.append(
                    "(error_text LIKE ? OR user_message LIKE ? OR "
                    "context_before LIKE ? OR context_after LIKE ? OR "
                    "source_file LIKE ?)"
                )
                like_term = f"%{term}%"
                params.extend([like_term] * 5)
            where_clauses.append(f"({' OR '.join(term_clauses)})")

        where_sql = " AND ".join(where_clauses)

        # Summary counts (respecting grep filter)
        type_counts = conn.execute(
            f"SELECT error_type, COUNT(*) FROM error_records "
            f"WHERE {where_sql} GROUP BY error_type ORDER BY COUNT(*) DESC",
            params,
        ).fetchall()

        if not type_counts:
            if grep_term:
                click.echo(f"No errors matching '{grep_term}' found.")
            else:
                click.echo("No errors mined yet.")
            return

        console = Console()

        # Show type breakdown
        total_matching = sum(row[1] for row in type_counts)
        title = "Error Type Summary"
        if grep_term:
            title += f" (matching '{grep_term}': {total_matching} hits)"
        summary = Table(title=title)
        summary.add_column("Type", style="bold")
        summary.add_column("Count", justify="right")
        for row in type_counts:
            style = "yellow" if row[0] == "agent_admission" else ""
            summary.add_row(row[0], str(row[1]), style=style)
        console.print(summary)
        console.print()

        # Show filtered errors
        rows = conn.execute(
            f"SELECT error_type, error_text, tool_name, session_id, timestamp, "
            f"user_message, source_file "
            f"FROM error_records WHERE {where_sql} "
            f"ORDER BY timestamp DESC LIMIT ?",
            params + [limit],
        ).fetchall()

        if rows:
            title_detail = f"errors (latest {limit})"
            if grep_term:
                title_detail = f"'{grep_term}' errors (latest {limit})"
            if error_type:
                title_detail = f"{error_type} " + title_detail
            detail = Table(title=title_detail)
            detail.add_column("Type", style="dim")
            detail.add_column("Error", max_width=60)
            detail.add_column("Tool")
            detail.add_column("Source", max_width=30)
            detail.add_column("Time")
            for r in rows:
                # Highlight the grep term in the error text for readability
                error_display = (r[1] or "")[:60]
                source = (r[6] or "").split("/")[-1][:30]  # just filename
                detail.add_row(
                    r[0],
                    error_display,
                    r[2] or "",
                    source,
                    (r[4] or "")[:16],
                )
            console.print(detail)

            # If grep is active, also show a sample user_message for context
            if grep_term:
                console.print()
                console.print("[dim]Sample user contexts:[/dim]")
                seen: set = set()
                for r in rows[:5]:
                    user_msg = (r[5] or "").strip()[:120]
                    if user_msg and user_msg not in seen:
                        seen.add(user_msg)
                        console.print(f"  [dim]>[/dim] {user_msg}")


@cli.group(invoke_without_command=True)
@click.pass_context
def datasets(ctx):
    """Manage pattern datasets."""
    if ctx.invoked_subcommand is None:
        db_path = os.path.expanduser("~/.sio/sio.db")
        if not os.path.exists(db_path):
            click.echo("No database found. Run 'sio mine' first.")
            return

        with _db_conn(db_path) as conn:
            pattern_rows = conn.execute(
                "SELECT d.id, d.pattern_id, d.file_path, d.positive_count, d.negative_count, "
                "d.created_at, d.updated_at FROM datasets d"
            ).fetchall()

        if not pattern_rows:
            click.echo("No datasets built yet.")
            return

        for row in pattern_rows:
            d = dict(row)
            click.echo(
                f"  Dataset #{d['id']} (pattern {d['pattern_id']}): "
                f"{d['positive_count']} positive, {d['negative_count']} negative "
                f"— {d['file_path']}"
            )


@datasets.command()
@click.option("--since", default=None, help="Time range for collection.")
@click.option("--error-type", default=None, help="Error type filter.")
def collect(since, error_type):
    """Collect targeted dataset from specific criteria."""
    from sio.datasets.builder import collect_dataset

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        result = collect_dataset(conn, since=since, error_type=error_type)

    count = len(result.get("errors", []))
    click.echo(f"Collected {count} error records matching criteria.")


@datasets.command()
@click.argument("pattern_id")
def inspect(pattern_id):
    """Inspect dataset for a specific pattern.

    Shows error distribution, session timeline, ground truth entries,
    and coverage gaps per surface type.
    """
    from collections import Counter

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    from sio.core.db.queries import (
        get_errors_for_pattern,
        get_ground_truth_by_pattern,
        get_pattern_by_id,
    )

    with _db_conn(db_path) as conn:
        # Look up pattern
        pattern = get_pattern_by_id(conn, pattern_id)
        if pattern is None:
            click.echo(f"No pattern found with id '{pattern_id}'.")
            return

        pat_row_id = pattern["id"]

        # --- Error distribution ---
        errors = get_errors_for_pattern(conn, pat_row_id)

        error_type_counts: Counter = Counter()
        session_ids: set = set()
        tool_counts: Counter = Counter()
        timestamps: list = []
        top_messages: list = []
        seen_msg_prefixes: set = set()

        for e in errors:
            et = e.get("error_type") or "unknown"
            error_type_counts[et] += 1
            sid = e.get("session_id")
            if sid:
                session_ids.add(sid)
            tn = e.get("tool_name")
            if tn:
                tool_counts[tn] += 1
            ts = e.get("timestamp")
            if ts:
                timestamps.append(ts)
            msg = (e.get("error_text") or "").strip()
            if msg:
                prefix = msg[:80].lower()
                if prefix not in seen_msg_prefixes and len(top_messages) < 5:
                    seen_msg_prefixes.add(prefix)
                    top_messages.append(msg[:120])

        # Ground truth info
        gt_entries = get_ground_truth_by_pattern(conn, pattern_id)

    # --- Display (no DB needed) ---

    # Error distribution table
    err_table = Table(title="Error Distribution by Type")
    err_table.add_column("Error Type", style="bold")
    err_table.add_column("Count", justify="right")
    for etype, count in error_type_counts.most_common():
        err_table.add_row(etype, str(count))
    console.print(err_table)
    console.print()

    # Session timeline
    sorted_ts = sorted(timestamps) if timestamps else []
    session_info = (
        f"Sessions: {len(session_ids)} unique\n"
        f"Errors: {len(errors)} total\n"
    )
    if sorted_ts:
        session_info += f"Date range: {sorted_ts[0]} to {sorted_ts[-1]}"
    else:
        first = pattern.get('first_seen', '?')
        last = pattern.get('last_seen', '?')
        session_info += f"Date range: {first} to {last}"
    console.print(Panel(session_info, title="Session Timeline"))
    console.print()

    # Top tools
    if tool_counts:
        tool_table = Table(title="Top Tools")
        tool_table.add_column("Tool", style="bold")
        tool_table.add_column("Count", justify="right")
        for tn, count in tool_counts.most_common(5):
            tool_table.add_row(tn, str(count))
        console.print(tool_table)
        console.print()

    # Ground truth
    gt_label_counts: Counter = Counter()
    gt_surface_counts: Counter = Counter()
    for gt in gt_entries:
        gt_label_counts[gt.get("label", "unknown")] += 1
        gt_surface_counts[gt.get("target_surface", "unknown")] += 1

    gt_table = Table(title="Ground Truth Entries")
    gt_table.add_column("Label", style="bold")
    gt_table.add_column("Count", justify="right")
    if gt_label_counts:
        for label, count in gt_label_counts.most_common():
            gt_table.add_row(label, str(count))
    else:
        gt_table.add_row("(none)", "0")
    console.print(gt_table)
    console.print()

    # Coverage gaps per surface type
    all_surfaces = {
        "claude_md_rule", "skill_update", "hook_config",
        "mcp_config", "settings_config", "agent_profile",
        "project_config",
    }
    covered_surfaces = set(gt_surface_counts.keys())

    coverage_table = Table(title="Surface Coverage Gaps")
    coverage_table.add_column("Surface Type", style="bold")
    coverage_table.add_column("Status")
    for surface in sorted(all_surfaces):
        if surface in covered_surfaces:
            cnt = gt_surface_counts[surface]
            coverage_table.add_row(
                surface, f"[green]covered ({cnt})[/green]",
            )
        else:
            coverage_table.add_row(
                surface, "[yellow]no ground truth[/yellow]",
            )
    console.print(coverage_table)


@cli.command()
@click.option(
    "--type", "error_type", default=None,
    type=click.Choice([
        "tool_failure", "user_correction",
        "repeated_attempt", "undo", "agent_admission",
    ]),
    help="Only analyze errors of this type.",
)
@click.option("--min-examples", default=3, help="Min examples to build a dataset.")
@click.option(
    "--grep", "-g", "grep_term", default=None,
    help=(
        "Filter errors by keyword(s) in content."
        " Comma-separated for OR logic"
        " (e.g. 'placeholder,hardcoded,stub')."
    ),
)
@click.option(
    "--verbose", "-v", is_flag=True, default=False,
    help="Enable verbose DSPy trace logging.",
)
@click.option(
    "--auto", "auto_mode", is_flag=True, default=False,
    help="Force automated mode for all patterns (skip interactive review).",
)
@click.option(
    "--analyze", "analyze_mode", is_flag=True, default=False,
    help="Force HITL (human-in-the-loop) mode for all patterns.",
)
@click.option(
    "--project", default=None,
    help="Filter by project name (substring match on source path).",
)
@click.option(
    "--exclude-type", "exclude_types", default=None,
    help="Exclude error types. Comma-separated (e.g. 'repeated_attempt,tool_failure').",
)
@click.option(
    "--preview", is_flag=True, default=False,
    help="Preview: filter + cluster + show pattern groupings, then stop. No generation.",
)
def suggest(
    error_type, min_examples, grep_term, verbose,
    auto_mode, analyze_mode, project, exclude_types, preview,
):
    """Run the full pipeline: cluster -> persist -> dataset -> suggestions."""
    from datetime import datetime, timezone

    from rich.console import Console
    from rich.table import Table

    from sio.clustering.pattern_clusterer import cluster_errors
    from sio.clustering.ranker import rank_patterns
    from sio.core.db.queries import (
        get_error_records,
        insert_pattern,
        link_error_to_pattern,
    )
    from sio.datasets.builder import build_dataset
    from sio.suggestions.generator import generate_suggestions

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        console = Console()

        # 1. Get all errors (no limit), filtered by project if specified
        all_errors = get_error_records(conn, limit=0, project=project)
        if not all_errors:
            filter_hint = f" for project '{project}'" if project else ""
            click.echo(
                f"No errors mined yet{filter_hint}."
                " Run 'sio mine --since \"7 days\"' first."
            )
            return

        errors_to_cluster = all_errors

        # Apply error type filter (include)
        if error_type:
            errors_to_cluster = [e for e in errors_to_cluster if e.get("error_type") == error_type]

        # Apply error type exclusion filter
        if exclude_types:
            excluded = {t.strip().lower() for t in exclude_types.split(",")}
            errors_to_cluster = [
                e for e in errors_to_cluster
                if (e.get("error_type") or "").lower() not in excluded
            ]

        # Apply content grep filter — comma-separated terms use OR logic
        # Searches across error_text, user_message, context_before, context_after, source_file
        # Results are deduped by error ID
        if grep_term:
            terms = [t.strip().lower() for t in grep_term.split(",") if t.strip()]

            def _matches_any_term(e: dict) -> bool:
                searchable = (
                    "error_text", "user_message",
                    "context_before", "context_after",
                    "source_file",
                )
                for field in searchable:
                    val = (e.get(field) or "").lower()
                    for term in terms:
                        if term in val:
                            return True
                return False

            errors_to_cluster = [e for e in errors_to_cluster if _matches_any_term(e)]
            # Dedup by error record ID
            seen_ids: set = set()
            deduped: list = []
            for e in errors_to_cluster:
                eid = e.get("id")
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    deduped.append(e)
            errors_to_cluster = deduped

        if not errors_to_cluster:
            filter_desc = []
            if error_type:
                filter_desc.append(f"type='{error_type}'")
            if grep_term:
                filter_desc.append(f"grep='{grep_term}'")
            click.echo(f"No errors matching {', '.join(filter_desc)} found.")
            return

        filter_msg = ""
        if grep_term:
            filter_msg = f" matching '{grep_term}'"
        console.print(
            f"[bold]Step 1:[/bold] Clustering"
            f" {len(errors_to_cluster)} errors{filter_msg}..."
        )

        # 2. Cluster and rank
        clustered = cluster_errors(errors_to_cluster)
        ranked = rank_patterns(clustered)
        console.print(f"  Found {len(ranked)} patterns")

        # Preview mode: show pattern groupings and stop
        if preview:
            console.print()
            preview_table = Table(title="Pattern Groupings (preview)")
            preview_table.add_column("#", justify="right", style="dim")
            preview_table.add_column("Pattern", max_width=50)
            preview_table.add_column("Errors", justify="right")
            preview_table.add_column("Sessions", justify="right")
            preview_table.add_column("Top Error Type")
            preview_table.add_column("Score", justify="right")
            preview_table.add_column("Sample", max_width=40)

            for i, p in enumerate(ranked, 1):
                # Find most common error type in this cluster
                type_counts: dict[str, int] = {}
                sample_msg = ""
                for eid in p.get("error_ids", []):
                    for e in errors_to_cluster:
                        if e.get("id") == eid:
                            et = e.get("error_type", "unknown")
                            type_counts[et] = type_counts.get(et, 0) + 1
                            if not sample_msg:
                                sample_msg = (e.get("error_text") or "")[:40]
                            break
                top_type = max(type_counts, key=type_counts.get) if type_counts else "unknown"

                preview_table.add_row(
                    str(i),
                    p.get("description", p.get("pattern_id", "?"))[:50],
                    str(p.get("error_count", 0)),
                    str(p.get("session_count", 0)),
                    top_type,
                    f"{p.get('rank_score', 0):.2f}",
                    sample_msg,
                )

            console.print(preview_table)
            console.print()
            console.print(
                f"[bold]{len(ranked)}[/bold] patterns from"
                f" [bold]{len(errors_to_cluster)}[/bold]"
                " filtered errors."
            )
            console.print()

            # Export preview dataset as CSV for external analysis
            import csv

            preview_dir = os.path.expanduser("~/.sio/previews")
            os.makedirs(preview_dir, exist_ok=True)

            # Export patterns summary
            patterns_csv = os.path.join(preview_dir, "patterns_preview.csv")
            with open(patterns_csv, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "rank", "pattern_id", "description",
                    "error_count", "session_count", "rank_score",
                ])
                for i, p in enumerate(ranked, 1):
                    writer.writerow([
                        i, p.get("pattern_id", ""), p.get("description", "")[:120],
                        p.get("error_count", 0), p.get("session_count", 0),
                        f"{p.get('rank_score', 0):.2f}",
                    ])

            # Export filtered errors dataset
            errors_csv = os.path.join(preview_dir, "errors_preview.csv")
            with open(errors_csv, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "id", "error_type", "error_text",
                    "tool_name", "session_id", "timestamp",
                    "source_file", "user_message",
                ])
                for e in errors_to_cluster:
                    writer.writerow([
                        e.get("id", ""), e.get("error_type", ""),
                        (e.get("error_text") or "")[:200], e.get("tool_name", ""),
                        e.get("session_id", ""), e.get("timestamp", ""),
                        e.get("source_file", ""), (e.get("user_message") or "")[:200],
                    ])

            console.print("[bold]Exported for analysis:[/bold]")
            console.print(f"  Patterns: {patterns_csv}")
            console.print(f"  Errors:   {errors_csv}")
            console.print()
            console.print("[dim]To generate suggestions, re-run without --preview.[/dim]")
            console.print(
                "[dim]To refine, adjust --grep, --type,"
                " --exclude-type and re-run --preview.[/dim]"
            )
            return

        # 3. Persist patterns to DB (clear old patterns first for clean state)
        console.print("[bold]Step 2:[/bold] Persisting patterns to database...")
        conn.execute("DELETE FROM pattern_errors")
        conn.execute("DELETE FROM patterns")
        conn.commit()

        now_iso = datetime.now(timezone.utc).isoformat()
        seen_slugs: set[str] = set()
        persisted_patterns: list[dict] = []
        for p in ranked:
            # Ensure unique pattern_id slugs
            slug = p["pattern_id"]
            if slug in seen_slugs:
                # Append error count to disambiguate
                slug = f"{slug}-{p['error_count']}"
            seen_slugs.add(slug)
            p["pattern_id"] = slug

            p["centroid_embedding"] = None  # skip blob for now
            p["created_at"] = now_iso
            p["updated_at"] = now_iso
            row_id = insert_pattern(conn, p)
            p["id"] = row_id  # store DB id for dataset builder
            persisted_patterns.append(p)

            # Link errors to pattern
            for eid in p.get("error_ids", []):
                link_error_to_pattern(conn, row_id, eid)

        console.print(f"  Persisted {len(persisted_patterns)} patterns with error links")

        # 4. Build datasets (ephemeral — clear stale datasets and rebuild fresh)
        console.print("[bold]Step 3:[/bold] Building datasets...")
        conn.execute("DELETE FROM datasets")
        conn.commit()

        datasets: dict[str, dict] = {}
        for p in persisted_patterns:
            metadata = build_dataset(p, all_errors, conn, min_threshold=min_examples)
            if metadata is not None:
                pid = metadata["pattern_id"]
                ds_cur = conn.execute(
                    "INSERT INTO datasets (pattern_id, file_path, positive_count, "
                    "negative_count, min_threshold, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        p["id"], metadata["file_path"],
                        metadata["positive_count"], metadata["negative_count"],
                        min_examples, now_iso, now_iso,
                    ),
                )
                conn.commit()
                metadata["id"] = ds_cur.lastrowid
                datasets[pid] = metadata

        console.print(f"  Built {len(datasets)} datasets")

        # 5. Generate targeted suggestions
        console.print("[bold]Step 4:[/bold] Generating targeted suggestions...")
        # Determine mode from CLI flags
        mode = None
        if auto_mode:
            mode = "auto"
        elif analyze_mode:
            mode = "hitl"

        suggestions = generate_suggestions(
            persisted_patterns, datasets, conn, verbose=verbose, mode=mode,
        )

        # Clear old suggestions and insert new ones
        conn.execute("DELETE FROM suggestions WHERE status = 'pending'")
        conn.commit()

        for s in suggestions:
            conn.execute(
                "INSERT INTO suggestions (pattern_id, dataset_id, description, "
                "confidence, proposed_change, target_file, change_type, status, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    s["pattern_id"], s["dataset_id"], s["description"],
                    s["confidence"], s["proposed_change"], s["target_file"],
                    s["change_type"], "pending", now_iso,
                ),
            )
        conn.commit()

        console.print(f"  Generated {len(suggestions)} suggestions")
        console.print()

        # 6. Display results
        if suggestions:
            table = Table(title="Generated Suggestions")
            table.add_column("#", style="bold")
            table.add_column("Description", max_width=50)
            table.add_column("Conf.", justify="right")
            table.add_column("Target")
            table.add_column("Type")
            table.add_column("Source")

            for i, s in enumerate(suggestions, 1):
                source_label = (
                    "[DSPy]" if s.get("_using_dspy") else "[Template]"
                )
                table.add_row(
                    str(i),
                    s["description"][:50],
                    f"{s['confidence']:.0%}",
                    s["target_file"],
                    s["change_type"],
                    source_label,
                )
            console.print(table)
            console.print()
            console.print(
                f"[green]Run 'sio suggest-review' to review {len(suggestions)} "
                f"pending suggestions interactively.[/green]"
            )
        else:
            console.print("[yellow]No suggestions generated. Need more error data.[/yellow]")


@cli.command("suggest-review")
def suggest_review():
    """Review pending improvement suggestions interactively."""
    from rich.console import Console
    from rich.table import Table

    from sio.review.reviewer import approve as do_approve
    from sio.review.reviewer import defer as do_defer
    from sio.review.reviewer import reject as do_reject
    from sio.review.reviewer import review_pending

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        pending = review_pending(conn)

        if not pending:
            click.echo("No pending suggestions to review.")
            return

        console = Console()
        for i, s in enumerate(pending, 1):
            table = Table(title=f"Suggestion {i}/{len(pending)} (ID: {s['id']})")
            table.add_column("Field", style="bold")
            table.add_column("Value")
            table.add_row("Description", s.get("description", ""))
            table.add_row("Confidence", f"{s.get('confidence', 0):.0%}")
            table.add_row("Target", s.get("target_file", ""))
            table.add_row("Type", s.get("change_type", ""))
            console.print(table)
            console.print(f"\n[dim]Proposed change:[/dim]\n{s.get('proposed_change', '')}\n")

            choice = click.prompt(
                "  [a(pprove)/r(eject)/d(efer)/q(uit)]",
                type=str,
                default="d",
            )
            if choice == "q":
                break
            note = ""
            if choice in ("a", "r"):
                note = click.prompt("  Note (optional)", default="", type=str)
            if choice == "a":
                do_approve(conn, s["id"], note or None)
                click.echo("  Approved.")
            elif choice == "r":
                do_reject(conn, s["id"], note or None)
                click.echo("  Rejected.")
            else:
                do_defer(conn, s["id"])
                click.echo("  Deferred.")
            click.echo()


@cli.command()
@click.argument("suggestion_id", type=int)
@click.option("--note", "-n", default=None, help="Optional note.")
def approve(suggestion_id, note):
    """Approve a suggestion by ID and promote to ground truth."""
    from sio.ground_truth.corpus import promote_to_ground_truth
    from sio.review.reviewer import approve as do_approve

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    with _db_conn(db_path) as conn:
        ok = do_approve(conn, suggestion_id, note)
        if ok:
            click.echo(f"Suggestion {suggestion_id} approved.")
            # T047: Auto-promote to ground truth
            try:
                gt_id = promote_to_ground_truth(conn, suggestion_id)
                click.echo(f"  Promoted to ground truth (ID: {gt_id}).")
            except Exception as exc:
                click.echo(f"  Ground truth promotion failed: {exc}")
        else:
            click.echo(f"Suggestion {suggestion_id} not found.")
            raise SystemExit(1)


@cli.command()
@click.argument("suggestion_id", type=int)
@click.option("--note", "-n", default=None, help="Optional note.")
def reject(suggestion_id, note):
    """Reject a suggestion by ID."""
    from sio.review.reviewer import reject as do_reject

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    with _db_conn(db_path) as conn:
        ok = do_reject(conn, suggestion_id, note)
    if ok:
        click.echo(f"Suggestion {suggestion_id} rejected.")
    else:
        click.echo(f"Suggestion {suggestion_id} not found.")
        raise SystemExit(1)


@cli.command("apply")
@click.argument("suggestion_id", type=int)
def apply_suggestion(suggestion_id):
    """Apply an approved suggestion to its target file."""
    from sio.applier.writer import apply_change

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    with _db_conn(db_path) as conn:
        result = apply_change(conn, suggestion_id)

    if result["success"]:
        click.echo(f"Applied suggestion {suggestion_id} to {result['target_file']}")
        cid = result['change_id']
        click.echo(f"Change ID: {cid} (use 'sio rollback {cid}' to undo)")
    else:
        click.echo(f"Apply failed: {result.get('reason', 'unknown')}")
        raise SystemExit(1)


@cli.command()
@click.argument("change_id", type=int)
def rollback(change_id):
    """Rollback an applied change by ID."""
    from sio.applier.rollback import rollback_change

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    with _db_conn(db_path) as conn:
        result = rollback_change(conn, change_id)
    if result["success"]:
        click.echo(f"Change {change_id} rolled back: {result['target_file']}")
    else:
        click.echo(f"Rollback failed: {result.get('reason', 'unknown')}")
        raise SystemExit(1)


@cli.command()
def changes():
    """List applied changes and their status."""
    from rich.console import Console
    from rich.table import Table

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    with _db_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT ac.id, ac.suggestion_id, ac.target_file, ac.applied_at, "
            "ac.rolled_back_at, s.description "
            "FROM applied_changes ac "
            "LEFT JOIN suggestions s ON s.id = ac.suggestion_id "
            "ORDER BY ac.applied_at DESC"
        ).fetchall()

    if not rows:
        click.echo("No applied changes yet.")
        return

    console = Console()
    table = Table(title="Applied Changes")
    table.add_column("ID", style="bold")
    table.add_column("Suggestion")
    table.add_column("Target File")
    table.add_column("Applied At")
    table.add_column("Status")
    table.add_column("Description", max_width=40)

    for r in rows:
        r = dict(r)
        status = "rolled back" if r.get("rolled_back_at") else "active"
        style = "dim" if status == "rolled back" else "green"
        table.add_row(
            str(r["id"]),
            str(r["suggestion_id"]),
            r["target_file"] or "",
            (r["applied_at"] or "")[:16],
            status,
            (r.get("description") or "")[:40],
            style=style,
        )

    console.print(table)


@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx):
    """View and test LLM configuration."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@config.command("show")
def config_show():
    """Display current LLM configuration."""
    from rich.console import Console
    from rich.table import Table

    from sio.core.config import load_config

    cfg = load_config()
    console = Console()

    # Detect provider from config or env vars
    provider = "none"
    model_display = cfg.llm_model or "(auto-detect)"
    if cfg.llm_model:
        provider = cfg.llm_model.split("/")[0] if "/" in cfg.llm_model else "custom"
    else:
        for env_name, prov in [
            ("AZURE_OPENAI_API_KEY", "azure"),
            ("ANTHROPIC_API_KEY", "anthropic"),
            ("OPENAI_API_KEY", "openai"),
        ]:
            if os.environ.get(env_name):
                provider = prov
                break

    table = Table(title="SIO LLM Configuration")
    table.add_column("Setting", style="bold")
    table.add_column("Value")

    table.add_row("Model", model_display)
    table.add_row("Provider detected", provider)
    table.add_row("Sub-model", cfg.llm_sub_model or "(none)")
    table.add_row("Temperature", str(cfg.llm_temperature))
    table.add_row("Max tokens", str(cfg.llm_max_tokens))

    # API key masking
    if cfg.llm_api_key_env:
        raw = os.environ.get(cfg.llm_api_key_env, "")
        masked = _mask_key(raw) if raw else "(not set)"
        table.add_row(f"API key ({cfg.llm_api_key_env})", masked)

    console.print(table)

    # Auto-detection status
    console.print()
    env_table = Table(title="Environment Variable Detection")
    env_table.add_column("Variable", style="bold")
    env_table.add_column("Status")
    env_vars = [
        "AZURE_OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "OLLAMA_HOST",
    ]
    for var in env_vars:
        val = os.environ.get(var, "")
        if val:
            status = f"[green]set[/green] ({_mask_key(val)})"
        else:
            status = "[dim]not set[/dim]"
        env_table.add_row(var, status)
    console.print(env_table)


@config.command("test")
def config_test():
    """Test LLM connectivity with a simple query."""
    import time

    from rich.console import Console

    from sio.core.config import load_config

    console = Console()
    cfg = load_config()

    console.print("[bold]Testing LLM connection...[/bold]")

    try:
        from sio.core.dspy.lm_factory import create_lm
    except ImportError:
        console.print("[red]dspy is not installed. Run: pip install dspy[/red]")
        raise SystemExit(1)

    lm = create_lm(cfg)
    if lm is None:
        console.print("[red]No LLM available.[/red]")
        console.print()
        console.print("Set one of these environment variables:")
        console.print("  export AZURE_OPENAI_API_KEY=...")
        console.print("  export ANTHROPIC_API_KEY=...")
        console.print("  export OPENAI_API_KEY=...")
        console.print()
        console.print("Or configure explicitly in ~/.sio/config.toml:")
        console.print('  [llm]')
        console.print('  model = "openai/gpt-4o"')
        console.print('  api_key_env = "OPENAI_API_KEY"')
        raise SystemExit(1)

    console.print(f"  LM created: {lm}")

    try:
        import dspy

        start = time.perf_counter()
        predictor = dspy.Predict("question -> answer")
        with dspy.context(lm=lm):
            result = predictor(question="What is 2+2?")
        elapsed_ms = (time.perf_counter() - start) * 1000

        console.print(f"[green]Success![/green] Response: {result.answer}")
        console.print(f"  Latency: {elapsed_ms:.0f} ms")
    except Exception as e:
        console.print(f"[red]LLM call failed:[/red] {e}")
        raise SystemExit(1)


def _mask_key(key: str) -> str:
    """Mask an API key, showing only first 4 and last 4 characters."""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


@cli.group()
def schedule():
    """Manage passive analysis schedule."""
    pass


@schedule.command("install")
def schedule_install():
    """Install daily + weekly cron jobs."""
    from sio.scheduler.cron import install_schedule

    result = install_schedule()
    if result.get("installed"):
        click.echo("Schedule installed successfully.")
        if result.get("daily_enabled"):
            click.echo("  Daily job:  midnight (0 0 * * *)")
        if result.get("weekly_enabled"):
            click.echo("  Weekly job: Sunday midnight (0 0 * * 0)")
    else:
        click.echo("Schedule installation failed.", err=True)
        raise SystemExit(1)


@schedule.command("status")
def schedule_status():
    """Check scheduler status."""
    from sio.scheduler.cron import get_status

    status = get_status()
    installed = status.get("installed", False)
    daily = status.get("daily_enabled", False)
    weekly = status.get("weekly_enabled", False)

    click.echo(f"Installed:      {'yes' if installed else 'no'}")
    click.echo(f"Daily enabled:  {'yes' if daily else 'no'}")
    click.echo(f"Weekly enabled: {'yes' if weekly else 'no'}")


@cli.command("status")
def sio_status():
    """Show overall SIO v2 status."""
    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No SIO database found. Run 'sio mine' to start.")
        return

    with _db_conn(db_path) as conn:
        errors = conn.execute("SELECT COUNT(*) FROM error_records").fetchone()[0]
        patterns = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
        datasets = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE status = 'pending'"
        ).fetchone()[0]
        applied = conn.execute(
            "SELECT COUNT(*) FROM applied_changes WHERE rolled_back_at IS NULL"
        ).fetchone()[0]

    click.echo("SIO v2 Status")
    click.echo("-" * 30)
    click.echo(f"Errors mined:      {errors}")
    click.echo(f"Patterns found:    {patterns}")
    click.echo(f"Datasets built:    {datasets}")
    click.echo(f"Pending reviews:   {pending}")
    click.echo(f"Applied changes:   {applied}")


# ---------------------------------------------------------------------------
# Ground Truth commands (T045 / T046)
# ---------------------------------------------------------------------------


@cli.group(name="ground-truth")
def ground_truth_group():
    """Manage agent-generated ground truth for DSPy training."""
    pass


@ground_truth_group.command("seed")
@click.option("--count", default=10, help="Max number of seed entries to insert.")
@click.option("--surface", default=None, help="Filter by target surface type.")
def gt_seed(count, surface):
    """Seed ground truth with representative examples covering all surfaces."""
    from sio.core.config import load_config
    from sio.ground_truth.seeder import seed_ground_truth

    db_path = os.path.expanduser("~/.sio/sio.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    config = load_config()

    with _db_conn(db_path) as conn:
        ids = seed_ground_truth(config, conn, count=count, surface=surface)

    if surface:
        click.echo(f"Seeded {len(ids)} ground truth entries for surface '{surface}'.")
    else:
        click.echo(f"Seeded {len(ids)} ground truth entries across all 7 surfaces.")


@ground_truth_group.command("generate")
@click.option("--candidates", default=3, help="Candidates per pattern.")
@click.argument("pattern_id", required=False, default=None)
def gt_generate(candidates, pattern_id):
    """Generate ground truth candidates from discovered patterns."""
    from sio.core.config import load_config
    from sio.core.db.queries import get_pattern_by_id, get_patterns
    from sio.ground_truth.generator import generate_candidates

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        raise SystemExit(1)

    config = load_config()

    with _db_conn(db_path) as conn:
        if pattern_id is not None:
            pat = get_pattern_by_id(conn, pattern_id)
            if pat is None:
                click.echo(f"Pattern '{pattern_id}' not found.")
                raise SystemExit(1)
            patterns = [pat]
        else:
            patterns = get_patterns(conn)

        if not patterns:
            click.echo("No patterns found. Run 'sio suggest' first.")
            raise SystemExit(1)

        total_ids = []
        for pattern in patterns:
            # Build a minimal dataset reference
            ds_row = conn.execute(
                "SELECT * FROM datasets WHERE pattern_id = ? LIMIT 1",
                (pattern["id"],),
            ).fetchone()
            dataset = dict(ds_row) if ds_row else {"id": 0, "file_path": ""}

            ids = generate_candidates(
                pattern, dataset, conn, config, n_candidates=candidates,
            )
            total_ids.extend(ids)

    click.echo(
        f"Generated {len(total_ids)} ground truth candidates "
        f"from {len(patterns)} patterns."
    )


@ground_truth_group.command("review")
@click.option("--surface", default=None, help="Filter by target surface type.")
def gt_review(surface):
    """Interactive review of pending ground truth candidates."""
    from rich.console import Console
    from rich.panel import Panel

    from sio.core.db.queries import get_pending_ground_truth
    from sio.ground_truth.reviewer import approve, edit, reject

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        raise SystemExit(1)

    with _db_conn(db_path) as conn:
        pending = get_pending_ground_truth(conn, surface_type=surface)

        if not pending:
            click.echo("No pending ground truth entries to review.")
            raise SystemExit(1)

        console = Console()
        reviewed = 0

        for i, entry in enumerate(pending, 1):
            # Display entry details
            console.print()
            console.print(Panel(
                f"[bold]Pattern:[/bold] {entry.get('pattern_summary', '')[:120]}\n\n"
                f"[bold]Surface:[/bold] {entry.get('target_surface', '')}\n"
                f"[bold]Rule:[/bold] {entry.get('rule_title', '')}\n\n"
                f"[bold]Prevention:[/bold]\n{entry.get('prevention_instructions', '')}\n\n"
                f"[bold]Rationale:[/bold] {entry.get('rationale', '')}",
                title=f"Ground Truth {i}/{len(pending)} (ID: {entry['id']})",
            ))

            choice = click.prompt(
                "  [a]pprove / [r]eject / [e]dit / [s]kip / [q]uit",
                type=str,
                default="s",
            )

            if choice == "q":
                break
            elif choice == "a":
                note = click.prompt("  Note (optional)", default="", type=str)
                approve(conn, entry["id"], note or None)
                click.echo("  Approved.")
                reviewed += 1
            elif choice == "r":
                note = click.prompt("  Reason", default="", type=str)
                reject(conn, entry["id"], note or None)
                click.echo("  Rejected.")
                reviewed += 1
            elif choice == "e":
                new_title = click.prompt(
                    "  New rule title (Enter to keep)",
                    default=entry.get("rule_title", ""),
                )
                new_instructions = click.prompt(
                    "  New instructions (Enter to keep)",
                    default=entry.get("prevention_instructions", ""),
                )
                new_content = {}
                if new_title != entry.get("rule_title", ""):
                    new_content["rule_title"] = new_title
                if new_instructions != entry.get("prevention_instructions", ""):
                    new_content["prevention_instructions"] = new_instructions
                if new_content:
                    new_id = edit(conn, entry["id"], new_content)
                    click.echo(f"  Created edited entry (ID: {new_id}).")
                else:
                    click.echo("  No changes made.")
                reviewed += 1

    click.echo(f"\nReviewed {reviewed} entries.")


@ground_truth_group.command("status")
def gt_status():
    """Show ground truth statistics."""
    from rich.console import Console
    from rich.table import Table

    from sio.core.db.queries import get_ground_truth_stats

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio ground-truth seed' first.")
        return

    with _db_conn(db_path) as conn:
        stats = get_ground_truth_stats(conn)

    console = Console()

    if stats["total"] == 0:
        console.print("No ground truth entries yet. Run 'sio ground-truth seed'.")
        return

    # Summary table
    summary = Table(title="Ground Truth Summary")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")
    summary.add_row("Total entries", str(stats["total"]))
    for label, count in sorted(stats.get("by_label", {}).items()):
        style = "green" if label == "positive" else "red" if label == "negative" else ""
        summary.add_row(f"  {label}", str(count), style=style)
    console.print(summary)
    console.print()

    # Surface breakdown
    surface_table = Table(title="By Target Surface")
    surface_table.add_column("Surface", style="bold")
    surface_table.add_column("Count", justify="right")
    for surface, count in sorted(stats.get("by_surface", {}).items()):
        surface_table.add_row(surface, str(count))
    console.print(surface_table)


# ---------------------------------------------------------------------------
# Optimize Suggestions command (T063 / T064)
# ---------------------------------------------------------------------------


@cli.command("optimize-suggestions")
@click.option(
    "--optimizer",
    type=click.Choice(["auto", "bootstrap", "miprov2"]),
    default="auto",
    help="DSPy optimizer to use. 'auto' selects based on corpus size.",
)
@click.option("--dry-run", is_flag=True, help="Evaluate metrics without saving.")
def optimize_suggestions_cmd(optimizer, dry_run):
    """Optimize the suggestion module using ground truth corpus.

    Uses BootstrapFewShot (<50 examples) or MIPROv2 (>=50 examples)
    to optimize the DSPy SuggestionModule on approved ground truth.
    Shows before/after metric scores and prompts for approval.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from sio.core.config import load_config
    from sio.core.dspy.optimizer import OptimizationError, optimize_suggestions

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio ground-truth seed' first.")
        raise SystemExit(1)

    config = load_config()
    console = Console()

    console.print(
        f"\n[bold]Optimizing suggestions[/bold] "
        f"(optimizer={optimizer}, dry_run={dry_run})\n"
    )

    with _db_conn(db_path) as conn:
        try:
            result = optimize_suggestions(
                conn, optimizer=optimizer, dry_run=dry_run, config=config,
            )
        except OptimizationError as exc:
            console.print(f"[red]Optimization failed:[/red] {exc}")
            raise SystemExit(1)

        # Display results
        if result.status == "error":
            console.print(f"[red]Cannot optimize:[/red] {result.message}")
            raise SystemExit(1)

        # T064: Before/after metric display
        metrics_table = Table(title="Optimization Metrics")
        metrics_table.add_column("Metric", style="bold")
        metrics_table.add_column("Value", justify="right")
        metrics_table.add_row("Optimizer", result.optimizer_used)
        metrics_table.add_row("Training examples", str(result.training_count))
        metrics_table.add_row(
            "Metric (before)",
            f"{result.metric_before:.3f}" if result.metric_before is not None else "N/A",
        )
        metrics_table.add_row(
            "Metric (after)",
            f"{result.metric_after:.3f}" if result.metric_after is not None else "N/A",
        )

        # Compute improvement
        if result.metric_before is not None and result.metric_after is not None:
            delta = result.metric_after - result.metric_before
            delta_pct = (delta / max(result.metric_before, 0.001)) * 100
            style = "green" if delta > 0 else "red" if delta < 0 else ""
            metrics_table.add_row(
                "Improvement",
                f"{delta:+.3f} ({delta_pct:+.1f}%)",
                style=style,
            )

        console.print(metrics_table)
        console.print()

        # T064: Show few-shot demo diff if module was saved
        if result.module_id is not None:
            _display_optimization_diff(console, conn, result)

        if dry_run:
            console.print("[yellow][dry-run] No changes saved.[/yellow]")
        else:
            console.print(Panel(
                result.message,
                title="Result",
                style="green" if result.status == "success" else "yellow",
            ))

            # Approval prompt
            choice = click.prompt(
                "Keep optimized module? [y]es / [n]o (rollback)",
                type=click.Choice(["y", "n"]),
                default="y",
            )
            if choice == "n":
                # Rollback: deactivate the module
                from sio.core.dspy.module_store import deactivate_previous

                deactivate_previous(conn, "suggestion")
                console.print("[yellow]Optimized module deactivated.[/yellow]")


def _display_optimization_diff(console, conn, result):
    """T064: Display Rich diff of default vs optimized module's few-shot examples."""
    from rich.panel import Panel
    from rich.syntax import Syntax

    try:
        from sio.core.dspy.module_store import get_active_module

        active = get_active_module(conn, "suggestion")
        if active and os.path.exists(active["file_path"]):
            import json as json_mod

            with open(active["file_path"]) as f:
                module_data = json_mod.load(f)

            # Extract demos from the saved module JSON
            demos_text = json_mod.dumps(module_data, indent=2, default=str)
            # Truncate for display
            if len(demos_text) > 3000:
                demos_text = demos_text[:3000] + "\n... (truncated)"

            console.print(Panel(
                Syntax(demos_text, "json", theme="monokai"),
                title="Optimized Module (few-shot examples)",
                subtitle=f"File: {active['file_path']}",
            ))
    except Exception:
        # Non-critical display — don't crash
        pass


if __name__ == "__main__":
    cli()
