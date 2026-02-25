"""SIO CLI — Self-Improving Organism command-line interface."""

import json as _json
import os

import click

_DEFAULT_DB_DIR = os.path.expanduser("~/.sio/claude-code")


@click.group()
@click.version_option(version="0.1.0")
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
    from sio.core.db.schema import init_db
    from sio.core.health.aggregator import compute_health

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    if not os.path.exists(db_path):
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
    conn = init_db(db_path)

    results = compute_health(conn, platform=platform, skill=skill)
    conn.close()

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
    from sio.core.db.schema import init_db
    from sio.core.feedback.batch_review import apply_label, get_reviewable

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    if not os.path.exists(db_path):
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
    conn = init_db(db_path)

    items = get_reviewable(
        conn, platform, session_id=session, limit=limit,
    )

    if not items:
        click.echo("No unlabeled invocations to review.")
        conn.close()
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

    conn.close()
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
    from sio.core.db.schema import init_db
    from sio.core.dspy.optimizer import optimize as run_opt

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    if not os.path.exists(db_path):
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
    conn = init_db(db_path)

    result = run_opt(
        conn, skill_name=skill_name, platform=platform,
        optimizer=optimizer, dry_run=dry_run,
    )

    if result["status"] == "error":
        click.echo(f"Cannot optimize: {result.get('reason', 'unknown')}")
        conn.close()
        raise SystemExit(1)

    click.echo(f"Optimization for '{skill_name}' ({optimizer}):")
    click.echo()
    click.echo(result.get("diff", ""))
    click.echo()

    if dry_run:
        click.echo("[dry-run] No changes applied.")
        conn.close()
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

    conn.close()


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
    click.echo("Installation complete.")


@cli.command()
@click.option("--platform", default="claude-code", help="Platform filter.")
@click.option("--days", default=90, help="Purge records older than N days.")
@click.option("--dry-run", is_flag=True, help="Show count without deleting.")
def purge(platform, days, dry_run):
    """Purge old telemetry records."""
    from sio.core.db.retention import purge as do_purge
    from sio.core.db.schema import init_db

    db_path = os.path.join(
        os.path.expanduser(f"~/.sio/{platform}"),
        "behavior_invocations.db",
    )
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    conn = init_db(db_path)
    count = do_purge(conn, older_than_days=days, dry_run=dry_run)
    conn.close()

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

    from sio.core.db.schema import init_db

    db_path = os.path.join(
        os.path.expanduser(f"~/.sio/{platform}"),
        "behavior_invocations.db",
    )
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    conn = init_db(db_path)
    rows = conn.execute("SELECT * FROM behavior_invocations").fetchall()
    conn.close()

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
# v2 stub commands
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--since", required=True, help='Time window (e.g., "3 days", "1 week").')
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

    from sio.core.db.schema import init_db
    from sio.mining.pipeline import run_mine

    db_path = os.path.expanduser("~/.sio/sio.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = init_db(db_path)

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
        conn.close()
        return

    result = run_mine(conn, source_dirs, since, source, project)
    conn.close()

    click.echo(f"Scanned {result['total_files_scanned']} files")
    click.echo(f"Found {result['errors_found']} errors")


@cli.command()
def patterns():
    """Show discovered error patterns ranked by importance."""
    from rich.console import Console
    from rich.table import Table

    from sio.core.db.queries import get_error_records, get_patterns
    from sio.core.db.schema import init_db
    from sio.clustering.pattern_clusterer import cluster_errors
    from sio.clustering.ranker import rank_patterns

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    conn = init_db(db_path)

    # Get all error records from DB
    errors = get_error_records(conn)
    if not errors:
        click.echo("No errors mined yet. Run 'sio mine --since \"7 days\"' first.")
        conn.close()
        return

    # Cluster and rank
    clustered = cluster_errors(errors)
    ranked = rank_patterns(clustered)

    console = Console()
    table = Table(title="Error Patterns (ranked by importance)")
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
    conn.close()


@cli.group(invoke_without_command=True)
@click.pass_context
def datasets(ctx):
    """Manage pattern datasets."""
    if ctx.invoked_subcommand is None:
        from sio.core.db.queries import get_patterns
        from sio.core.db.schema import init_db

        db_path = os.path.expanduser("~/.sio/sio.db")
        if not os.path.exists(db_path):
            click.echo("No database found. Run 'sio mine' first.")
            return

        conn = init_db(db_path)
        pattern_rows = conn.execute(
            "SELECT d.id, d.pattern_id, d.file_path, d.positive_count, d.negative_count, "
            "d.created_at, d.updated_at FROM datasets d"
        ).fetchall()
        conn.close()

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
    from sio.core.db.schema import init_db
    from sio.datasets.builder import collect_dataset

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    conn = init_db(db_path)
    result = collect_dataset(conn, since=since, error_type=error_type)
    conn.close()

    count = len(result.get("errors", []))
    click.echo(f"Collected {count} error records matching criteria.")


@cli.command("suggest-review")
def suggest_review():
    """Review pending improvement suggestions."""
    click.echo("[v2] Suggestion review... (not yet implemented)")


@cli.command()
@click.argument("suggestion_id", type=int)
def approve(suggestion_id):
    """Approve a suggestion by ID."""
    click.echo(f"[v2] Approving suggestion {suggestion_id}... (not yet implemented)")


@cli.command()
@click.argument("suggestion_id", type=int)
def reject(suggestion_id):
    """Reject a suggestion by ID."""
    click.echo(f"[v2] Rejecting suggestion {suggestion_id}... (not yet implemented)")


@cli.command()
@click.argument("change_id", type=int)
def rollback(change_id):
    """Rollback an applied change by ID."""
    click.echo(f"[v2] Rolling back change {change_id}... (not yet implemented)")


@cli.group()
def schedule():
    """Manage passive analysis schedule."""
    pass


@schedule.command("install")
def schedule_install():
    """Install daily + weekly cron jobs."""
    click.echo("[v2] Installing schedule... (not yet implemented)")


@schedule.command("status")
def schedule_status():
    """Check scheduler status."""
    click.echo("[v2] Schedule status... (not yet implemented)")


@cli.command("status")
def sio_status():
    """Show overall SIO v2 status."""
    click.echo("[v2] Status... (not yet implemented)")


if __name__ == "__main__":
    cli()
