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


if __name__ == "__main__":
    cli()
