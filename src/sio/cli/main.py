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


if __name__ == "__main__":
    cli()
