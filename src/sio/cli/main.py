"""SIO CLI — Self-Improving Organism command-line interface."""

import json as _json
import os
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

import click

from sio.core.constants import DEFAULT_PLATFORM
from sio.core.observability import log_failure

_DEFAULT_DB_DIR = os.path.expanduser(f"~/.sio/{DEFAULT_PLATFORM}")


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


try:
    _sio_version = pkg_version("sio")
except PackageNotFoundError:
    _sio_version = "0.0.0-dev"


@click.group()
@click.version_option(version=_sio_version)
def cli():
    """SIO: Self-Improving Organism for AI coding CLIs."""
    pass


@cli.command()
@click.option("--platform", default=DEFAULT_PLATFORM, help="Platform filter.")
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
@click.option("--platform", default=DEFAULT_PLATFORM, help="Platform filter.")
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
            conn,
            platform,
            session_id=session,
            limit=limit,
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
                    conn,
                    item["id"],
                    choice,
                    note or None,
                )
                labeled += 1
            click.echo()

    click.echo(f"Labeled {labeled} invocations.")


@cli.command()
@click.argument("skill_name")
@click.option("--platform", default=DEFAULT_PLATFORM, help="Platform filter.")
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
        "\u26a0\ufe0f  'sio optimize' is deprecated. Use 'sio optimize-suggestions' instead.",
        err=True,
    )
    from sio.core.dspy.optimizer import optimize as run_opt

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    if not os.path.exists(db_path):
        os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)

    with _db_conn(db_path) as conn:
        result = run_opt(
            conn,
            skill_name=skill_name,
            platform=platform,
            optimizer=optimizer,
            dry_run=dry_run,
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
    type=click.Choice([DEFAULT_PLATFORM]),
    default=DEFAULT_PLATFORM,
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
@click.option("--platform", default=DEFAULT_PLATFORM, help="Platform filter.")
@click.option("--days", default=90, help="Purge records older than N days.")
@click.option("--dry-run", is_flag=True, help="Show count without deleting.")
@click.option(
    "--behavior-only",
    is_flag=True,
    default=False,
    help=(
        "Also purge behavior_invocations rows from sio.db AND the per-platform DB "
        "(in addition to the default error_records / flow_events purge)."
    ),
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
def purge(platform, days, dry_run, behavior_only, yes):
    """Purge old telemetry records from the main SIO database.

    By default deletes rows in ``error_records`` and ``flow_events`` from
    ``~/.sio/sio.db`` (or ``$SIO_DB_PATH``) where ``mined_at`` is older
    than *--days* days.

    With ``--behavior-only`` also purges ``behavior_invocations`` rows from
    both the main DB and the per-platform DB.

    Examples:
        sio purge --days 30 --yes
        sio purge --days 30 --behavior-only --yes
        sio purge --days 90 --dry-run
    """
    # FR-025 / M7: always target the main sio.db, NOT the per-platform DB
    sio_db_path = os.environ.get(
        "SIO_DB_PATH",
        os.path.expanduser("~/.sio/sio.db"),
    )

    if not dry_run and not yes:
        target_desc = "error_records, flow_events" + (
            ", behavior_invocations" if behavior_only else ""
        )
        confirmed = click.confirm(
            f"Purge {target_desc} older than {days} days from {sio_db_path}?",
        )
        if not confirmed:
            click.echo("Aborted.")
            return

    if not os.path.exists(sio_db_path):
        click.echo(f"No main SIO database found at {sio_db_path}.")
        return

    total_purged = 0

    with _db_conn(sio_db_path) as conn:
        # Purge error_records and flow_events (always)
        if dry_run:
            try:
                n_errors = conn.execute(
                    "SELECT COUNT(*) FROM error_records WHERE mined_at < datetime('now', ?)",
                    (f"-{days} days",),
                ).fetchone()[0]
            except Exception:
                n_errors = 0
            try:
                n_flows = conn.execute(
                    "SELECT COUNT(*) FROM flow_events WHERE mined_at < datetime('now', ?)",
                    (f"-{days} days",),
                ).fetchone()[0]
            except Exception:
                n_flows = 0
            click.echo(
                f"Would purge {n_errors} error_records and {n_flows} flow_events "
                f"older than {days} days."
            )
        else:
            try:
                cur = conn.execute(
                    "DELETE FROM error_records WHERE mined_at < datetime('now', ?)",
                    (f"-{days} days",),
                )
                total_purged += cur.rowcount
            except Exception as e:
                log_failure("purge_errors", "error_records", e, stage="delete")
            try:
                cur = conn.execute(
                    "DELETE FROM flow_events WHERE mined_at < datetime('now', ?)",
                    (f"-{days} days",),
                )
                total_purged += cur.rowcount
            except Exception as e:
                log_failure("purge_errors", "flow_events", e, stage="delete")
            conn.commit()

        # Optionally purge behavior_invocations from main DB
        if behavior_only:
            if dry_run:
                try:
                    n_bi = conn.execute(
                        "SELECT COUNT(*) FROM behavior_invocations "
                        "WHERE timestamp < datetime('now', ?)",
                        (f"-{days} days",),
                    ).fetchone()[0]
                except Exception as e:
                    log_failure(
                        "purge_errors", "behavior_invocations", e, stage="count"
                    )
                    n_bi = 0
                click.echo(
                    f"Would also purge {n_bi} behavior_invocations rows from {sio_db_path}."
                )
            else:
                try:
                    cur = conn.execute(
                        "DELETE FROM behavior_invocations WHERE timestamp < datetime('now', ?)",
                        (f"-{days} days",),
                    )
                    total_purged += cur.rowcount
                    conn.commit()
                except Exception as e:
                    log_failure(
                        "purge_errors", "behavior_invocations (main)", e,
                        stage="delete",
                    )

    # Also purge per-platform DB when --behavior-only
    if behavior_only and not dry_run:
        platform_db_path = os.path.join(
            os.path.expanduser(f"~/.sio/{platform}"),
            "behavior_invocations.db",
        )
        if os.path.exists(platform_db_path):
            with _db_conn(platform_db_path) as pconn:
                try:
                    cur = pconn.execute(
                        "DELETE FROM behavior_invocations WHERE timestamp < datetime('now', ?)",
                        (f"-{days} days",),
                    )
                    total_purged += cur.rowcount
                    pconn.commit()
                except Exception as e:
                    log_failure(
                        "purge_errors",
                        f"behavior_invocations (platform:{platform})",
                        e,
                        stage="delete",
                    )
            click.echo(f"Also purged per-platform DB: {platform_db_path}")

    if not dry_run:
        click.echo(f"Purged {total_purged} total records older than {days} days.")


@cli.command()
@click.option("--platform", default=DEFAULT_PLATFORM, help="Platform filter.")
@click.option(
    "--format",
    "fmt",
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
    "--since",
    required=True,
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
@click.option(
    "--exclude-sidechains/--include-sidechains",
    default=True,
    help="Filter out sidechain messages before aggregation (default: on).",
)
def mine(since, project, source, exclude_sidechains):
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
        result = run_mine(
            conn,
            source_dirs,
            since,
            source,
            project,
            exclude_sidechains=exclude_sidechains,
        )

    from rich.console import Console
    from rich.table import Table

    console = Console()

    table = Table(
        title="Mining Summary",
        show_header=False,
        title_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    total_scanned = result["total_files_scanned"]
    skipped = result.get("skipped_files", 0)
    newly_mined = result.get("newly_mined", total_scanned - skipped)
    errors_found = result["errors_found"]
    total_cost = result.get("total_cost_tracked", 0.0)

    table.add_row("Sessions found", str(total_scanned))
    table.add_row(
        "Already processed (skipped)",
        str(skipped),
    )
    table.add_row("Newly mined", str(newly_mined))
    table.add_row(
        "Total cost tracked",
        f"${total_cost:.2f}" if total_cost else "$0.00",
    )
    table.add_row("Errors captured", str(errors_found))

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Flow discovery commands (v2.1 — positive pattern mining)
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--since",
    default="14 days",
    help='Time window: "7 days", "14 days", "30 days".',
)
@click.option("--project", default=None, help="Filter by project name.")
@click.option(
    "--min-count",
    default=3,
    type=int,
    help="Minimum occurrence count to show a flow.",
)
@click.option(
    "--limit",
    default=20,
    type=int,
    help="Maximum number of flows to display.",
)
@click.option(
    "--mine-first/--no-mine",
    default=True,
    help="Mine flow data before querying (default: yes).",
)
def flows(since, project, min_count, limit, mine_first):
    """Discover recurring positive tool sequence patterns.

    Analyzes JSONL session transcripts to find tool sequences that
    consistently lead to successful outcomes. No LLM required — pure
    regex + sequence matching.

    Examples:
        sio flows                         # Default: 14 days, min 3 occurrences
        sio flows --since "7 days"        # Last week
        sio flows --min-count 5           # Only frequent patterns
        sio flows --no-mine               # Skip mining, query existing data
    """
    from pathlib import Path

    from sio.mining.flow_pipeline import query_flows, run_flow_mine

    db_path = os.path.expanduser("~/.sio/sio.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with _db_conn(db_path) as conn:
        # Optionally mine fresh flow data
        if mine_first:
            source_dirs = []
            jsonl_dir = Path(os.path.expanduser("~/.claude/projects"))
            if jsonl_dir.exists():
                source_dirs.append(jsonl_dir)

            if source_dirs:
                result = run_flow_mine(conn, source_dirs, since, "jsonl", project)
                click.echo(
                    f"Mined {result['total_files_scanned']} sessions, "
                    f"found {result['flows_found']} flow events"
                )
                # No-more-silent-errors: visible failure banner + log path
                failed = result.get("failed_files", 0)
                if failed:
                    log_path = os.path.expanduser("~/.sio/logs/flow_failures.log")
                    click.secho(
                        f"⚠  {failed} file(s) failed during mining — "
                        f"details: {log_path}",
                        err=True,
                        fg="yellow",
                    )
                    # Show top 3 failures inline so the user actually sees them
                    for f in result.get("failures", [])[:3]:
                        click.secho(
                            f"   [{f['stage']}] {f['error']}  —  {f['file']}",
                            err=True,
                            fg="yellow",
                        )
                    if failed > 3:
                        click.secho(
                            f"   ... and {failed - 3} more (see log)",
                            err=True,
                            fg="yellow",
                        )
            else:
                click.echo("No JSONL source directories found.")
                return

        # Query aggregated flows
        results = query_flows(conn, since=since, min_count=min_count, limit=limit)

    if not results:
        click.echo("\nNo flows discovered yet. Try lowering --min-count or widening --since.")
        return

    # Display results
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title=f"Discovered Flows (last {since}, min {min_count} occurrences)")
        table.add_column("#", style="dim", width=3)
        table.add_column("Flow", style="cyan", max_width=50)
        table.add_column("Count", justify="right")
        table.add_column("Success", justify="right")
        table.add_column("Avg Time", justify="right")
        table.add_column("Sessions", justify="right")
        table.add_column("Confidence", justify="center")

        for i, flow in enumerate(results, 1):
            # Format duration
            dur = flow["avg_duration"]
            if dur >= 60:
                dur_str = f"{dur / 60:.1f}m"
            else:
                dur_str = f"{dur:.0f}s"

            # Color confidence
            conf = flow["confidence"]
            if conf == "HIGH":
                conf_str = f"[green]{conf}[/green]"
            elif conf == "MEDIUM":
                conf_str = f"[yellow]{conf}[/yellow]"
            else:
                conf_str = f"[dim]{conf}[/dim]"

            table.add_row(
                str(i),
                flow["sequence"],
                str(flow["count"]),
                f"{flow['success_rate']:.0f}%",
                dur_str,
                str(flow["session_count"]),
                conf_str,
            )

        console.print(table)

    except ImportError:
        # Fallback without rich
        click.echo(f"\nDiscovered Flows (last {since}):\n")
        click.echo(f"{'#':>3}  {'Flow':<50} {'Count':>5} {'Success':>7} {'Time':>6} {'Conf':>6}")
        click.echo("-" * 85)
        for i, flow in enumerate(results, 1):
            dur = flow["avg_duration"]
            dur_str = f"{dur / 60:.1f}m" if dur >= 60 else f"{dur:.0f}s"
            click.echo(
                f"{i:>3}  {flow['sequence']:<50} {flow['count']:>5} "
                f"{flow['success_rate']:>6.0f}% {dur_str:>6} {flow['confidence']:>6}"
            )


@cli.command()
@click.argument("session_path", required=False, default=None)
@click.option(
    "--latest",
    is_flag=True,
    default=False,
    help="Distill the most recent JSONL session.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Save playbook to file (default: print to stdout).",
)
@click.option(
    "--project",
    default=None,
    help="Filter latest session by project name.",
)
def distill(session_path, latest, output, project):
    """Distill a long session into a clean playbook of winning steps.

    Takes a messy exploratory session and extracts just the steps that worked,
    removing failures, retries, and dead ends. Outputs a numbered playbook.

    Examples:
        sio distill --latest                          # Most recent session
        sio distill --latest --project jira-issues    # Most recent for project
        sio distill /path/to/session.jsonl            # Specific session file
        sio distill --latest -o playbook.md           # Save to file
    """
    from pathlib import Path

    from sio.mining.jsonl_parser import parse_jsonl
    from sio.mining.session_distiller import distill_session, format_playbook

    # Find the session file
    if session_path:
        jsonl_file = Path(session_path)
        if not jsonl_file.exists():
            click.echo(f"File not found: {session_path}")
            return
    elif latest:
        # Find most recent JSONL in ~/.claude/projects/
        projects_dir = Path(os.path.expanduser("~/.claude/projects"))
        if not projects_dir.exists():
            click.echo("No projects directory found at ~/.claude/projects/")
            return

        jsonl_files = sorted(
            projects_dir.rglob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        if project:
            jsonl_files = [f for f in jsonl_files if project.lower() in str(f).lower()]

        if not jsonl_files:
            click.echo("No JSONL sessions found.")
            return

        jsonl_file = jsonl_files[0]
        click.echo(f"Distilling: {jsonl_file.name} ({jsonl_file.stat().st_size // 1024}KB)")
    else:
        click.echo("Provide a session path or use --latest")
        click.echo("  sio distill --latest")
        click.echo("  sio distill /path/to/session.jsonl")
        return

    # Parse and distill
    parsed = parse_jsonl(jsonl_file)
    if not parsed:
        click.echo("No messages found in session.")
        return

    distilled = distill_session(parsed)

    if not distilled["steps"]:
        click.echo("No successful tool calls found to distill.")
        return

    # Format output
    title = f"Playbook: {jsonl_file.stem}"
    playbook = format_playbook(distilled, title=title)

    # Stats line
    stats = distilled["stats"]
    click.echo(
        f"\nDistilled {stats['total_tool_calls']} tool calls → "
        f"{stats['winning_steps']} winning steps "
        f"({stats['failed_calls']} failures, {stats['retries']} retries removed)"
    )

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(playbook)
        click.echo(f"Playbook saved → {output}")
    else:
        click.echo("")
        click.echo(playbook)


@cli.command()
@click.argument("query")
@click.option(
    "--session",
    default=None,
    help="Path to specific JSONL session. Default: latest.",
)
@click.option(
    "--project",
    default=None,
    help="Filter latest session by project name.",
)
@click.option(
    "--polish/--no-polish",
    default=False,
    help="Use Gemini to polish into a clean runbook (costs ~$0.02).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Save runbook to file.",
)
def recall(query, session, project, polish, output):
    """Recall how a specific task was solved in a previous session.

    Topic-filters a distilled session to only the steps matching your query,
    detects struggle→fix transitions, and optionally polishes via Gemini.

    Examples:
        sio recall "dbt hhdev"                    # Cheap: filter + format
        sio recall "dbt hhdev" --polish            # Expensive: + Gemini runbook
        sio recall "auth fix" --project hh-dev     # Filter by project
        sio recall "snowflake deploy" -o runbook.md
    """
    from pathlib import Path

    from sio.mining.jsonl_parser import parse_jsonl
    from sio.mining.recall import (
        build_gemini_polish_prompt,
        detect_struggles,
        format_recall_output,
        topic_filter,
    )
    from sio.mining.session_distiller import distill_session

    # Find session
    if session:
        jsonl_file = Path(session)
    else:
        projects_dir = Path(os.path.expanduser("~/.claude/projects"))
        jsonl_files = sorted(
            projects_dir.rglob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if project:
            jsonl_files = [f for f in jsonl_files if project.lower() in str(f).lower()]
        if not jsonl_files:
            click.echo("No sessions found.")
            return
        jsonl_file = jsonl_files[0]

    click.echo(f"Session: {jsonl_file.name} ({jsonl_file.stat().st_size // 1024}KB)")

    # Step 1: Distill
    parsed = parse_jsonl(jsonl_file)
    if not parsed:
        click.echo("No messages found.")
        return

    distilled = distill_session(parsed)
    total_steps = distilled["stats"]["winning_steps"]

    # Step 2: Topic filter
    filtered = topic_filter(distilled, query)
    topic_steps = len(filtered["steps"])
    click.echo(f"Distilled {total_steps} steps → {topic_steps} matching '{query}'")

    if not filtered["steps"]:
        click.echo(f"No steps found matching '{query}'. Try broader keywords.")
        return

    # Step 3: Struggle detection
    struggles = detect_struggles(filtered["steps"])
    if struggles:
        click.echo(f"Found {len(struggles)} struggle→fix transitions")

    # Step 4: Format output
    if polish:
        # Build Gemini prompt
        prompt = build_gemini_polish_prompt(filtered, struggles, query)
        click.echo("Polishing via Gemini...")
        click.echo(f"\n--- GEMINI POLISH PROMPT ({len(prompt)} chars) ---")
        click.echo("Run this manually or use --no-polish for raw output:")
        click.echo(f"  gemini_brainstorm(topic='Create runbook: {query}', context='...')")
        click.echo("--- END PROMPT ---\n")
        # For CLI, we output the prompt. The /sio-recall skill will call Gemini directly.
        runbook = format_recall_output(filtered, struggles)
        runbook += (
            "\n\n---\n*Gemini polish prompt saved. Use /sio-recall skill for auto-polish.*\n"
        )
    else:
        runbook = format_recall_output(filtered, struggles)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(runbook)
        click.echo(f"Runbook saved → {output}")
    else:
        click.echo("")
        click.echo(runbook)


@cli.command()
@click.option(
    "--type",
    "error_type",
    default=None,
    type=click.Choice(
        [
            "tool_failure",
            "user_correction",
            "repeated_attempt",
            "undo",
            "agent_admission",
        ]
    ),
    help="Filter by error type.",
)
@click.option(
    "--project",
    default=None,
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
        f"Error Patterns — {error_type}" if error_type else "Error Patterns (ranked by importance)"
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
    "--type",
    "error_type",
    default=None,
    type=click.Choice(
        [
            "tool_failure",
            "user_correction",
            "repeated_attempt",
            "undo",
            "agent_admission",
        ]
    ),
    help="Filter by error type.",
)
@click.option("--limit", "-n", default=20, help="Max errors to show.")
@click.option(
    "--grep",
    "-g",
    "grep_term",
    default=None,
    help=(
        "Search content for keyword(s). Comma-separated"
        " for OR logic (e.g. 'placeholder,hardcoded,stub')."
    ),
)
@click.option(
    "--project",
    default=None,
    help="Filter by project name (substring match on source path).",
)
@click.option(
    "--exclude-type",
    "exclude_types",
    default=None,
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
    session_info = f"Sessions: {len(session_ids)} unique\nErrors: {len(errors)} total\n"
    if sorted_ts:
        session_info += f"Date range: {sorted_ts[0]} to {sorted_ts[-1]}"
    else:
        first = pattern.get("first_seen", "?")
        last = pattern.get("last_seen", "?")
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
        "claude_md_rule",
        "skill_update",
        "hook_config",
        "mcp_config",
        "settings_config",
        "agent_profile",
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
                surface,
                f"[green]covered ({cnt})[/green]",
            )
        else:
            coverage_table.add_row(
                surface,
                "[yellow]no ground truth[/yellow]",
            )
    console.print(coverage_table)


@cli.command()
@click.option(
    "--type",
    "error_type",
    default=None,
    type=click.Choice(
        [
            "tool_failure",
            "user_correction",
            "repeated_attempt",
            "undo",
            "agent_admission",
        ]
    ),
    help="Only analyze errors of this type.",
)
@click.option("--min-examples", default=3, help="Min examples to build a dataset.")
@click.option(
    "--grep",
    "-g",
    "grep_term",
    default=None,
    help=(
        "Filter errors by keyword(s) in content."
        " Comma-separated for OR logic"
        " (e.g. 'placeholder,hardcoded,stub')."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose DSPy trace logging.",
)
@click.option(
    "--auto",
    "auto_mode",
    is_flag=True,
    default=False,
    help="Force automated mode for all patterns (skip interactive review).",
)
@click.option(
    "--analyze",
    "analyze_mode",
    is_flag=True,
    default=False,
    help="Force HITL (human-in-the-loop) mode for all patterns.",
)
@click.option(
    "--project",
    default=None,
    help="Filter by project name (substring match on source path).",
)
@click.option(
    "--exclude-type",
    "exclude_types",
    default=None,
    help="Exclude error types. Comma-separated (e.g. 'repeated_attempt,tool_failure').",
)
@click.option(
    "--preview",
    is_flag=True,
    default=False,
    help="Preview: filter + cluster + show pattern groupings, then stop. No generation.",
)
def suggest(
    error_type,
    min_examples,
    grep_term,
    verbose,
    auto_mode,
    analyze_mode,
    project,
    exclude_types,
    preview,
):
    """Run the full pipeline: cluster -> persist -> dataset -> suggestions."""
    import uuid
    from datetime import datetime, timezone

    from rich.console import Console
    from rich.table import Table

    from sio.clustering.pattern_clusterer import cluster_errors
    from sio.clustering.ranker import rank_patterns
    from sio.core.db.queries import (
        get_error_records,
        insert_pattern,
        link_error_to_pattern,
        mark_stale_for_new_cycle,
    )
    from sio.datasets.builder import build_dataset
    from sio.suggestions.generator import generate_suggestions

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        console = Console()

        # Generate a new cycle_id for this suggest run (FR-003, data-model.md §2.8)
        cycle_id = str(uuid.uuid4())

        # 1. Get all errors (no limit), filtered by project if specified
        all_errors = get_error_records(conn, limit=0, project=project)
        if not all_errors:
            filter_hint = f" for project '{project}'" if project else ""
            click.echo(
                f"No errors mined yet{filter_hint}. Run 'sio mine --since \"7 days\"' first."
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
                e for e in errors_to_cluster if (e.get("error_type") or "").lower() not in excluded
            ]

        # Apply content grep filter — comma-separated terms use OR logic
        # Searches across error_text, user_message, context_before, context_after, source_file
        # Results are deduped by error ID
        if grep_term:
            terms = [t.strip().lower() for t in grep_term.split(",") if t.strip()]

            def _matches_any_term(e: dict) -> bool:
                searchable = (
                    "error_text",
                    "user_message",
                    "context_before",
                    "context_after",
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
            f"[bold]Step 1:[/bold] Clustering {len(errors_to_cluster)} errors{filter_msg}..."
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
                writer.writerow(
                    [
                        "rank",
                        "pattern_id",
                        "description",
                        "error_count",
                        "session_count",
                        "rank_score",
                    ]
                )
                for i, p in enumerate(ranked, 1):
                    writer.writerow(
                        [
                            i,
                            p.get("pattern_id", ""),
                            p.get("description", "")[:120],
                            p.get("error_count", 0),
                            p.get("session_count", 0),
                            f"{p.get('rank_score', 0):.2f}",
                        ]
                    )

            # Export filtered errors dataset
            errors_csv = os.path.join(preview_dir, "errors_preview.csv")
            with open(errors_csv, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "id",
                        "error_type",
                        "error_text",
                        "tool_name",
                        "session_id",
                        "timestamp",
                        "source_file",
                        "user_message",
                    ]
                )
                for e in errors_to_cluster:
                    writer.writerow(
                        [
                            e.get("id", ""),
                            e.get("error_type", ""),
                            (e.get("error_text") or "")[:200],
                            e.get("tool_name", ""),
                            e.get("session_id", ""),
                            e.get("timestamp", ""),
                            e.get("source_file", ""),
                            (e.get("user_message") or "")[:200],
                        ]
                    )

            console.print("[bold]Exported for analysis:[/bold]")
            console.print(f"  Patterns: {patterns_csv}")
            console.print(f"  Errors:   {errors_csv}")
            console.print()
            console.print("[dim]To generate suggestions, re-run without --preview.[/dim]")
            console.print(
                "[dim]To refine, adjust --grep, --type, --exclude-type and re-run --preview.[/dim]"
            )
            return

        # 3. Persist patterns to DB — non-destructive active-flag transition (FR-003)
        #    Mark prior active rows stale; applied_changes is NEVER touched.
        console.print("[bold]Step 2:[/bold] Persisting patterns to database...")
        mark_stale_for_new_cycle(conn, cycle_id)

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
            # Tag new patterns with the current cycle_id (active=1 by default)
            p["cycle_id"] = cycle_id
            row_id = insert_pattern(conn, p)
            p["id"] = row_id  # store DB id for dataset builder
            persisted_patterns.append(p)

            # Link errors to pattern
            for eid in p.get("error_ids", []):
                link_error_to_pattern(conn, row_id, eid)

        console.print(f"  Persisted {len(persisted_patterns)} patterns with error links")

        # 4. Build datasets — insert new cycle's datasets (stale already marked in step 2)
        console.print("[bold]Step 3:[/bold] Building datasets...")

        datasets: dict[str, dict] = {}
        for p in persisted_patterns:
            metadata = build_dataset(p, all_errors, conn, min_threshold=min_examples)
            if metadata is not None:
                pid = metadata["pattern_id"]
                ds_cur = conn.execute(
                    "INSERT INTO datasets (pattern_id, file_path, positive_count, "
                    "negative_count, min_threshold, created_at, updated_at, cycle_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        p["id"],
                        metadata["file_path"],
                        metadata["positive_count"],
                        metadata["negative_count"],
                        min_examples,
                        now_iso,
                        now_iso,
                        cycle_id,
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
            persisted_patterns,
            datasets,
            conn,
            verbose=verbose,
            mode=mode,
        )

        # Insert new cycle's suggestions — stale ones already deactivated in step 2 (FR-003)
        for s in suggestions:
            conn.execute(
                "INSERT INTO suggestions (pattern_id, dataset_id, description, "
                "confidence, proposed_change, target_file, change_type, status, "
                "created_at, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    s["pattern_id"],
                    s["dataset_id"],
                    s["description"],
                    s["confidence"],
                    s["proposed_change"],
                    s["target_file"],
                    s["change_type"],
                    "pending",
                    now_iso,
                    cycle_id,
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
                source_label = "[DSPy]" if s.get("_using_dspy") else "[Template]"
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
@click.argument("suggestion_id", type=int, required=False, default=None)
@click.option(
    "--experiment",
    is_flag=True,
    default=False,
    help="Apply on experiment branch instead of main.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip budget check (not recommended).",
)
@click.option(
    "--rollback",
    "rollback_id",
    type=int,
    default=None,
    help="Roll back an applied change by its ID (from applied_changes table).",
)
@click.option(
    "--merge",
    is_flag=True,
    default=False,
    help="Explicit consent to merge with a similar existing rule (FR-024).",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip interactive confirmation prompt.",
)
@click.option(
    "--no-backup",
    is_flag=True,
    default=False,
    help="[NOT SUPPORTED] Backups are required for safety; this flag is rejected.",
)
def apply_suggestion(suggestion_id, experiment, force, rollback_id, merge, yes, no_backup):
    """Apply an approved suggestion to its target file.

    Checks the instruction budget before applying. Uses delta-based
    writing (merge if >80% similar to an existing rule). If the budget
    is near capacity, triggers automatic consolidation.

    --no-backup is NOT supported (raises BackupRequired). Backups are
    mandatory for safety and cannot be disabled.

    Examples:
        sio apply 5                 # Normal apply with budget check
        sio apply 5 --force         # Skip budget check
        sio apply 5 --experiment    # Apply on experiment branch
        sio apply --rollback 42     # Roll back applied change #42
        sio apply 5 --merge         # Consent to merge with similar rule
        sio apply 5 --yes           # Skip confirmation prompt
    """
    # Reject --no-backup immediately (FR-004, BackupRequired)
    if no_backup:
        click.echo(
            "Error: --no-backup is not supported. "
            "Backups are required for safe rollback (BackupRequired).",
            err=True,
        )
        raise SystemExit(1)

    # Handle rollback path — does not require suggestion_id
    if rollback_id is not None:
        from sio.core.applier.writer import (  # noqa: PLC0415
            BackupMissingError,
            rollback_applied_change,
        )

        db_path = os.path.expanduser("~/.sio/sio.db")
        try:
            result = rollback_applied_change(rollback_id, db_path=db_path)
            click.echo(f"Rolled back applied change {rollback_id}: restored {result['target']}")
            raise SystemExit(0)
        except ValueError as exc:
            click.echo(f"Rollback failed: {exc}")
            raise SystemExit(1)
        except BackupMissingError as exc:
            click.echo(f"Rollback failed — backup missing: {exc}")
            raise SystemExit(1)

    if suggestion_id is None:
        click.echo("Error: missing argument 'SUGGESTION_ID'. Use --rollback for rollbacks.")
        raise SystemExit(1)

    from sio.applier.writer import apply_change
    from sio.core.config import load_config

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    config = load_config()

    if experiment:
        from sio.core.arena.experiment import create_experiment

        with _db_conn(db_path) as conn:
            try:
                branch = create_experiment(suggestion_id, conn)
                click.echo(f"Experiment branch created: {branch}")
                click.echo(
                    f"Suggestion will be validated after "
                    f"{config.validation_window_sessions} sessions."
                )
            except RuntimeError as exc:
                click.echo(f"Experiment creation failed: {exc}")
                raise SystemExit(1)
        return

    with _db_conn(db_path) as conn:
        result = apply_change(
            conn,
            suggestion_id,
            config=config,
            force=force,
        )

    if result["success"]:
        # Show budget info
        budget_msg = result.get("budget_message", "")
        if budget_msg:
            click.echo(f"  Budget: {budget_msg}")

        consolidation = result.get("consolidation_triggered", False)
        if consolidation:
            click.echo("  Consolidation triggered: merged similar rules")

        delta_type = result.get("delta_type", "append")
        if delta_type == "merge":
            click.echo("  Action: merge (similar to existing rule)")
        else:
            click.echo(f"  Action: {delta_type}")

        click.echo(f"Applied suggestion {suggestion_id} to {result['target_file']}")
        cid = result["change_id"]
        click.echo(f"Change ID: {cid} (use 'sio rollback {cid}' to undo)")
    else:
        reason = result.get("reason", "unknown")
        budget_msg = result.get("budget_message", "")
        consolidation = result.get("consolidation_triggered", False)

        if budget_msg:
            click.echo(f"  Budget: {budget_msg}")
        if consolidation:
            click.echo("  Consolidation attempted: no candidates found")

        click.echo(f"Apply failed: {reason}")
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
        "AZURE_OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "OLLAMA_HOST",
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
        console.print("  [llm]")
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


@schedule.command("run")
@click.option(
    "--mode",
    default="daily",
    type=click.Choice(["daily", "weekly"]),
    help="Analysis mode: daily (24h) or weekly (7d).",
)
def schedule_run(mode):
    """Run passive analysis pipeline (invoked by cron)."""
    from sio.scheduler.runner import run_analysis

    result = run_analysis(mode=mode)
    click.echo(f"Mode: {result['mode']}")
    click.echo(f"Errors found: {result['errors_found']}")
    click.echo(f"Patterns found: {result['patterns_found']}")
    click.echo(f"Suggestions generated: {result['suggestions_generated']}")


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
@click.option("--plain", is_flag=True, help="Plain text output (no Rich tables).")
def sio_status(plain: bool = False):
    """Show 5-section SIO pipeline health status.

    Sections: Hooks, Mining, Training, Audit, Database.
    Exit 0 if all healthy/warn; exit 1 if any error.
    Latency target: < 2s (SC-009).
    """
    import sys  # noqa: PLC0415
    import time  # noqa: PLC0415

    start = time.monotonic()

    try:
        from rich.console import Console  # noqa: PLC0415
        from rich.table import Table  # noqa: PLC0415

        _rich_available = True
    except ImportError:
        _rich_available = False

    db_path_str = os.path.expanduser("~/.sio/sio.db")
    db_exists = os.path.exists(db_path_str)

    any_error = False

    # -------------------------------------------------------------------
    # § 1: HOOKS — from hook_health.json via sio.cli.status.hook_health_rows()
    # -------------------------------------------------------------------
    from sio.cli.status import hook_health_rows  # noqa: PLC0415

    hook_rows = hook_health_rows()

    _STATE_ICONS = {
        "healthy": "[green]✓ healthy[/green]",
        "warn": "[yellow]⚠ warn[/yellow]",
        "error": "[red]✗ error[/red]",
        "never-seen": "[dim]○ never-seen[/dim]",
    }

    for _, state, _ in hook_rows:
        if state == "error":
            any_error = True

    # -------------------------------------------------------------------
    # § 2-5: DB sections (lazy queries only)
    # -------------------------------------------------------------------
    mining_data: dict = {}
    training_data: dict = {}
    audit_data: dict = {}
    db_data: dict = {}
    sync_drift_data: dict = {}
    schema_err = False

    if db_exists:
        try:
            from sio.core.db.schema import init_db  # noqa: PLC0415

            conn = init_db(db_path_str)
            try:
                # § 2: Mining
                try:
                    mining_data["error_records"] = conn.execute(
                        "SELECT COUNT(*) FROM error_records"
                    ).fetchone()[0]
                except Exception:
                    mining_data["error_records"] = "n/a"
                try:
                    mining_data["flow_events"] = conn.execute(
                        "SELECT COUNT(*) FROM flow_events"
                    ).fetchone()[0]
                except Exception:
                    mining_data["flow_events"] = "n/a"
                try:
                    mining_data["last_mined_at"] = (
                        conn.execute("SELECT MAX(mined_at) FROM processed_sessions").fetchone()[0]
                        or "never"
                    )
                except Exception:
                    mining_data["last_mined_at"] = "n/a"

                # § 3: Training
                try:
                    mining_data["behavior_invocations"] = conn.execute(
                        "SELECT COUNT(*) FROM behavior_invocations"
                    ).fetchone()[0]
                except Exception:
                    mining_data["behavior_invocations"] = "n/a"
                try:
                    training_data["gold_standards"] = conn.execute(
                        "SELECT COUNT(*) FROM ground_truth"
                    ).fetchone()[0]
                except Exception:
                    training_data["gold_standards"] = "n/a"
                try:
                    training_data["optimized_modules"] = conn.execute(
                        "SELECT COUNT(*) FROM optimized_modules"
                    ).fetchone()[0]
                except Exception:
                    training_data["optimized_modules"] = "n/a"
                try:
                    training_data["active_module"] = conn.execute(
                        "SELECT module_name FROM optimized_modules WHERE is_active = 1 LIMIT 1"
                    ).fetchone()
                    training_data["active_module"] = (
                        training_data["active_module"][0]
                        if training_data["active_module"]
                        else "none"
                    )
                except Exception:
                    training_data["active_module"] = "n/a"
                try:
                    training_data["optimization_runs"] = conn.execute(
                        "SELECT COUNT(*) FROM optimization_runs"
                    ).fetchone()[0]
                except Exception:
                    training_data["optimization_runs"] = "n/a"

                # § 4: Audit
                try:
                    audit_data["applied_active"] = conn.execute(
                        "SELECT COUNT(*) FROM applied_changes WHERE rolled_back_at IS NULL"
                    ).fetchone()[0]
                except Exception:
                    audit_data["applied_active"] = "n/a"
                try:
                    audit_data["autoresearch_24h"] = conn.execute(
                        "SELECT COUNT(*) FROM autoresearch_txlog "
                        "WHERE fired_at >= datetime('now', '-24 hours')"
                    ).fetchone()[0]
                except Exception:
                    audit_data["autoresearch_24h"] = "n/a"
                try:
                    audit_data["autoresearch_last"] = (
                        conn.execute("SELECT MAX(fired_at) FROM autoresearch_txlog").fetchone()[0]
                        or "never"
                    )
                except Exception:
                    audit_data["autoresearch_last"] = "n/a"

                # § 5: Database
                try:
                    schema_row = conn.execute(
                        "SELECT version, status FROM schema_version"
                        " ORDER BY applied_at DESC LIMIT 1"
                    ).fetchone()
                    db_data["schema_version"] = schema_row[0] if schema_row else "unknown"
                    db_data["schema_status"] = schema_row[1] if schema_row else "unknown"
                    if db_data["schema_status"] in ("applying", "error"):
                        any_error = True
                        schema_err = True
                except Exception:
                    db_data["schema_version"] = "n/a"
                    db_data["schema_status"] = "n/a"

            finally:
                conn.close()
        except Exception as exc:
            any_error = True
            db_data["_error"] = str(exc)

        # § 5: DB file size
        try:
            db_data["size_mb"] = os.path.getsize(db_path_str) / (1024 * 1024)
        except Exception:
            db_data["size_mb"] = 0.0

        # § 3 sync-drift (T096)
        try:
            from sio.core.db.sync import compute_sync_drift  # noqa: PLC0415

            sync_drift_data = compute_sync_drift()
            for platform, drift in sync_drift_data.items():
                if drift.get("drift_pct", 0.0) >= 0.05:
                    any_error = True
        except Exception:
            sync_drift_data = {}

    # -------------------------------------------------------------------
    # Render output
    # -------------------------------------------------------------------
    if _rich_available and not plain:
        console = Console()

        # — Section 1: Hooks —
        hooks_table = Table(title="Hooks", show_header=True, header_style="bold")
        hooks_table.add_column("Hook", style="cyan", min_width=20)
        hooks_table.add_column("State", min_width=14)
        hooks_table.add_column("Detail")
        for hook_name, state, detail in hook_rows:
            icon = _STATE_ICONS.get(state, state)
            hooks_table.add_row(hook_name, icon, detail)
        console.print(hooks_table)

        # — Section 2: Mining —
        mine_table = Table(title="Mining", show_header=True, header_style="bold")
        mine_table.add_column("Metric", style="cyan")
        mine_table.add_column("Value")
        mine_table.add_row("error_records", str(mining_data.get("error_records", "n/a")))
        mine_table.add_row("flow_events", str(mining_data.get("flow_events", "n/a")))
        mine_table.add_row("last_mined_at", str(mining_data.get("last_mined_at", "n/a")))
        console.print(mine_table)

        # — Section 3: Training (incl. sync-drift T096) —
        train_table = Table(title="Training", show_header=True, header_style="bold")
        train_table.add_column("Metric", style="cyan")
        train_table.add_column("Value")
        # Sync-drift row
        for platform, drift in sync_drift_data.items():
            canonical = drift.get("canonical_count", 0)
            per_plat = drift.get("per_platform_count", 0)
            pct = drift.get("drift_pct", 0.0)
            if pct >= 0.05:
                drift_icon = "[red]✗[/red]"
            elif pct >= 0.01:
                drift_icon = "[yellow]⚠[/yellow]"
            else:
                drift_icon = "[green]✓ in sync[/green]"
            train_table.add_row(
                f"behavior_invocations ({platform})",
                f"{canonical}  ↔  per-platform: {per_plat}  {drift_icon}",
            )
        if not sync_drift_data:
            train_table.add_row(
                "behavior_invocations (sio.db)",
                str(mining_data.get("behavior_invocations", "n/a")),
            )
        train_table.add_row("gold_standards", str(training_data.get("gold_standards", "n/a")))
        train_table.add_row(
            "optimized_modules", str(training_data.get("optimized_modules", "n/a"))
        )
        train_table.add_row("active_module", str(training_data.get("active_module", "n/a")))
        train_table.add_row(
            "optimization_runs", str(training_data.get("optimization_runs", "n/a"))
        )
        console.print(train_table)

        # — Section 4: Audit —
        audit_table = Table(title="Audit", show_header=True, header_style="bold")
        audit_table.add_column("Metric", style="cyan")
        audit_table.add_column("Value")
        audit_table.add_row(
            "applied_changes (active)", str(audit_data.get("applied_active", "n/a"))
        )
        audit_table.add_row(
            "autoresearch_txlog (24h)", str(audit_data.get("autoresearch_24h", "n/a"))
        )
        audit_table.add_row(
            "autoresearch last fired", str(audit_data.get("autoresearch_last", "n/a"))
        )
        console.print(audit_table)

        # — Section 5: Database —
        db_table = Table(title="Database", show_header=True, header_style="bold")
        db_table.add_column("Metric", style="cyan")
        db_table.add_column("Value")
        db_table.add_row("path", db_path_str)
        db_table.add_row("size_mb", f"{db_data.get('size_mb', 0.0):.1f} MB")
        schema_v = db_data.get("schema_version", "n/a")
        schema_s = db_data.get("schema_status", "n/a")
        schema_display = (
            f"[red]{schema_v} ({schema_s})[/red]" if schema_err else f"{schema_v} ({schema_s})"
        )
        db_table.add_row("schema_version", schema_display)
        db_table.add_row("exists", "[green]yes[/green]" if db_exists else "[red]no[/red]")
        console.print(db_table)

        elapsed = time.monotonic() - start
        status_color = "red" if any_error else "green"
        console.print(
            f"[{status_color}]Status: {'ERROR' if any_error else 'OK'}[/{status_color}]"
            f"  ({elapsed * 1000:.0f}ms)"
        )
    else:
        # Plain text fallback
        click.echo("=== Hooks ===")
        for hook_name, state, detail in hook_rows:
            click.echo(f"  {hook_name}: {state} — {detail}")
        click.echo("\n=== Mining ===")
        for k, v in mining_data.items():
            click.echo(f"  {k}: {v}")
        click.echo("\n=== Training ===")
        for platform, drift in sync_drift_data.items():
            click.echo(
                f"  behavior_invocations (sio.db) {drift.get('canonical_count', 0)} "
                f"<-> {platform}: {drift.get('per_platform_count', 0)} "
                f"({drift.get('drift_pct', 0.0) * 100:.1f}% drift)"
            )
        for k, v in training_data.items():
            click.echo(f"  {k}: {v}")
        click.echo("\n=== Audit ===")
        for k, v in audit_data.items():
            click.echo(f"  {k}: {v}")
        click.echo("\n=== Database ===")
        click.echo(f"  path: {db_path_str}")
        click.echo(f"  size_mb: {db_data.get('size_mb', 0.0):.1f}")
        click.echo(f"  schema_version: {db_data.get('schema_version', 'n/a')}")
        elapsed = time.monotonic() - start
        click.echo(f"\nStatus: {'ERROR' if any_error else 'OK'}  ({elapsed * 1000:.0f}ms)")

    sys.exit(1 if any_error else 0)


# ---------------------------------------------------------------------------
# Session briefing command
# ---------------------------------------------------------------------------


@cli.command("briefing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def briefing(as_json):
    """Show a brief session-start briefing of actionable SIO insights."""
    from sio.core.config import load_config
    from sio.suggestions.consultant import build_session_briefing

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No SIO database found. Run 'sio mine' first.")
        return

    config = load_config()

    with _db_conn(db_path) as conn:
        text = build_session_briefing(conn, config=config)

    if as_json:
        click.echo(_json.dumps({"briefing": text}))
    else:
        click.echo(text)


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
                pattern,
                dataset,
                conn,
                config,
                n_candidates=candidates,
            )
            total_ids.extend(ids)

    click.echo(
        f"Generated {len(total_ids)} ground truth candidates from {len(patterns)} patterns."
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
            console.print(
                Panel(
                    f"[bold]Pattern:[/bold] {entry.get('pattern_summary', '')[:120]}\n\n"
                    f"[bold]Surface:[/bold] {entry.get('target_surface', '')}\n"
                    f"[bold]Rule:[/bold] {entry.get('rule_title', '')}\n\n"
                    f"[bold]Prevention:[/bold]\n{entry.get('prevention_instructions', '')}\n\n"
                    f"[bold]Rationale:[/bold] {entry.get('rationale', '')}",
                    title=f"Ground Truth {i}/{len(pending)} (ID: {entry['id']})",
                )
            )

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
    to optimize the DSPy SuggestionGenerator (PatternToRule signature
    per contracts/dspy-module-api.md §3) on approved ground truth.
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
        f"\n[bold]Optimizing suggestions[/bold] (optimizer={optimizer}, dry_run={dry_run})\n"
    )

    with _db_conn(db_path) as conn:
        try:
            result = optimize_suggestions(
                conn,
                optimizer=optimizer,
                dry_run=dry_run,
                config=config,
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
            console.print(
                Panel(
                    result.message,
                    title="Result",
                    style="green" if result.status == "success" else "yellow",
                )
            )

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

            console.print(
                Panel(
                    Syntax(demos_text, "json", theme="monokai"),
                    title="Optimized Module (few-shot examples)",
                    subtitle=f"File: {active['file_path']}",
                )
            )
    except Exception:
        # Non-critical display — don't crash
        pass


# ---------------------------------------------------------------------------
# sio optimize — GEPA closed-loop optimizer (T044 / FR-036, FR-038)
# ---------------------------------------------------------------------------


@cli.command("optimize")
@click.option(
    "--module",
    "module_name",
    default="suggestion_generator",
    show_default=True,
    help="Module to optimize (e.g. suggestion_generator).",
)
@click.option(
    "--optimizer",
    "optimizer_name",
    type=click.Choice(["gepa", "mipro", "bootstrap"]),
    default="gepa",
    show_default=True,
    help="Prompt optimizer to use.",
)
@click.option(
    "--trainset-size",
    default=200,
    show_default=True,
    type=int,
    help="Max gold_standards rows to use for training.",
)
@click.option(
    "--valset-size",
    default=50,
    show_default=True,
    type=int,
    help="Max gold_standards rows to use for validation.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print config and exit without running optimization.",
)
def optimize_cmd(module_name, optimizer_name, trainset_size, valset_size, dry_run):
    """Run prompt optimization against the gold_standards corpus.

    Uses GEPA (or mipro/bootstrap in Wave 6) to compile an optimized
    DSPy program and save the artifact to ~/.sio/optimized/.
    Records the run in the optimized_modules table.
    """
    from rich.console import Console  # noqa: PLC0415

    console = Console()

    if dry_run:
        console.print("[bold]Dry run — config:[/bold]")
        console.print(f"  module:        {module_name}")
        console.print(f"  optimizer:     {optimizer_name}")
        console.print(f"  trainset_size: {trainset_size}")
        console.print(f"  valset_size:   {valset_size}")
        raise SystemExit(0)

    from sio.core.dspy.optimizer import (  # noqa: PLC0415
        InsufficientData,
        OptimizationError,
        UnknownOptimizer,
        run_optimize,
    )

    console.print(
        f"\n[bold]Optimizing[/bold] module=[cyan]{module_name}[/cyan] "
        f"optimizer=[cyan]{optimizer_name}[/cyan] ...\n"
    )

    try:
        result = run_optimize(
            module_name=module_name,
            optimizer_name=optimizer_name,
            trainset_size=trainset_size,
            valset_size=valset_size,
        )
    except InsufficientData as exc:
        console.print(
            f"[red]Insufficient data:[/red] {exc}\n"
            "Run [bold]sio mine[/bold] and promote invocations with "
            "[bold]sio promote-to-gold[/bold] first."
        )
        raise SystemExit(1)
    except (UnknownOptimizer, NotImplementedError) as exc:
        console.print(f"[red]Optimizer error:[/red] {exc}")
        raise SystemExit(1)
    except OptimizationError as exc:
        console.print(f"[red]Optimization failed:[/red] {exc}")
        raise SystemExit(1)

    console.print("[green]Optimization complete.[/green]")
    console.print(f"  artifact: {result['artifact']}")
    console.print(f"  score:    {result['score']:.4f}")
    console.print(f"  optimizer: {result['optimizer']}")


# ---------------------------------------------------------------------------
# Dataset export commands (v2.1 — training data generation)
# ---------------------------------------------------------------------------


@cli.command(name="export-dataset")
@click.option(
    "--task",
    type=click.Choice(["routing", "recovery", "flow", "all"]),
    required=True,
    help="Dataset type to export.",
)
@click.option(
    "--since",
    default="14 days",
    help='Time window: "7 days", "14 days", "30 days".',
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["jsonl", "parquet"]),
    default="jsonl",
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file path (default: ~/.sio/datasets/<task>_<date>.<fmt>).",
)
def export_dataset(task, since, fmt, output):
    """Export structured training datasets for DSPy/ML.

    Generates labeled training data from mined sessions:
    - routing: (user_query, tool_choice) pairs
    - recovery: (error, fix_applied, success) triples
    - flow: (current_state, next_tools) sequence predictions
    - all: exports all three types

    Examples:
        sio export-dataset --task routing
        sio export-dataset --task all --format parquet
        sio export-dataset --task recovery --since "30 days" -o ./data/recovery.jsonl
    """
    from datetime import datetime

    from sio.export.dataset_builder import (
        build_flow_dataset,
        build_recovery_dataset,
        build_routing_dataset,
        export_jsonl,
        export_parquet,
    )

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    from pathlib import Path

    output_dir = Path(os.path.expanduser("~/.sio/datasets"))
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")

    tasks_to_run = [task] if task != "all" else ["routing", "recovery", "flow"]
    total_records = 0

    with _db_conn(db_path) as conn:
        for t in tasks_to_run:
            if t == "routing":
                records = build_routing_dataset(conn, since=since)
            elif t == "recovery":
                records = build_recovery_dataset(conn, since=since)
            elif t == "flow":
                records = build_flow_dataset(conn, since=since)
            else:
                continue

            if not records:
                click.echo(f"  {t}: No data found. Run 'sio mine' and 'sio flows' first.")
                continue

            # Determine output path
            if output and task != "all":
                out_path = output
            else:
                ext = fmt
                out_path = str(output_dir / f"{t}_{date_str}.{ext}")

            # Export
            if fmt == "parquet":
                count = export_parquet(records, out_path)
            else:
                count = export_jsonl(records, out_path)

            total_records += count
            click.echo(f"  {t}: {count} records → {out_path}")

    click.echo(f"\nTotal: {total_records} training records exported")


# ---------------------------------------------------------------------------
# DSPy training commands (v2.1 — ML pipeline)
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--task",
    type=click.Choice(["router", "distiller", "recovery", "flow", "all"]),
    default="all",
    help="Which module to train.",
)
@click.option(
    "--optimizer",
    type=click.Choice(["bootstrap", "gepa"]),
    default="bootstrap",
    help="DSPy optimizer (bootstrap for <50 examples, gepa for 50+).",
)
@click.option(
    "--model",
    default=None,
    help="LLM model for training (default: DSPY_MODEL env or gpt-4o-mini).",
)
@click.option(
    "--max-examples",
    default=200,
    type=int,
    help="Maximum training examples per task.",
)
def train(task, optimizer, model, max_examples):
    """Train DSPy modules on exported datasets.

    Uses BootstrapFewShot (<50 examples) or GEPA (50+) to optimize
    recall modules. Trained models are saved to ~/.sio/models/ and
    used by `sio recall` for inference.

    Prerequisites:
        1. Run `sio mine --since "14 days"` to mine sessions
        2. Run `sio flows --since "14 days"` to extract flow patterns
        3. Run `sio export-dataset --task all` to create training data
        4. Run `sio train` to optimize modules

    Examples:
        sio train                             # Train all modules
        sio train --task distiller            # Train only the recall distiller
        sio train --optimizer gepa            # Use GEPA optimizer
        sio train --model gpt-4o             # Use specific model
    """
    from sio.training.recall_trainer import (
        load_training_data,
        save_trained_module,
        train_recall_module,
    )

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        # Load all training data
        click.echo("Loading training data...")
        data = load_training_data(conn)

        for key, records in data.items():
            click.echo(f"  {key}: {len(records)} examples")

        tasks_to_train = [task] if task != "all" else ["router", "distiller", "recovery", "flow"]

        for t in tasks_to_train:
            click.echo(f"\nTraining: {t} (optimizer={optimizer})...")
            result = train_recall_module(
                data,
                task=t,
                optimizer=optimizer,
                model=model,
                max_examples=max_examples,
            )

            if result["error"]:
                click.echo(f"  ERROR: {result['error']}")
                continue

            metrics = result["metrics"]
            click.echo(
                f"  Before: {metrics.get('before', '?')} → "
                f"After: {metrics.get('after', '?')} "
                f"({metrics.get('examples', 0)} train, {metrics.get('val_size', 0)} val)"
            )

            if result["output_path"]:
                # Register in DB
                save_trained_module(conn, t, optimizer, result["output_path"], metrics)
                click.echo(f"  Saved → {result['output_path']}")
            else:
                click.echo("  WARNING: Module not saved to disk.")

    click.echo("\nTraining complete.")


@cli.command(name="collect-recall")
@click.argument("query")
@click.option("--session", default=None, help="Session JSONL path.")
@click.option("--project", default=None, help="Filter by project name.")
@click.option("--runbook", default=None, help="Path to polished runbook (from Gemini).")
@click.option(
    "--label",
    type=click.Choice(["positive", "negative", "pending"]),
    default="pending",
    help="Quality label for this example.",
)
def collect_recall(query, session, project, runbook, label):
    """Collect a recall example for training.

    This is the data collection step: distill a session, optionally attach
    a Gemini-polished runbook, and store as a training example.

    The pipeline: collect → (optional: LLM polish) → label → train

    Examples:
        sio collect-recall "dbt hhdev" --project dev
        sio collect-recall "dbt hhdev" --runbook polished.md --label positive
    """
    from pathlib import Path

    from sio.mining.jsonl_parser import parse_jsonl
    from sio.mining.recall import detect_struggles, format_recall_output, topic_filter
    from sio.mining.session_distiller import distill_session

    db_path = os.path.expanduser("~/.sio/sio.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Find session
    if session:
        jsonl_file = Path(session)
    else:
        projects_dir = Path(os.path.expanduser("~/.claude/projects"))
        jsonl_files = sorted(
            projects_dir.rglob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if project:
            jsonl_files = [f for f in jsonl_files if project.lower() in str(f).lower()]
        if not jsonl_files:
            click.echo("No sessions found.")
            return
        jsonl_file = jsonl_files[0]

    # Distill + filter
    parsed = parse_jsonl(jsonl_file)
    if not parsed:
        click.echo("No messages found.")
        return

    distilled = distill_session(parsed)
    filtered = topic_filter(distilled, query)
    struggles = detect_struggles(filtered["steps"])
    raw_output = format_recall_output(filtered, struggles)

    # Load polished runbook if provided
    polished = None
    polish_model = None
    if runbook:
        runbook_path = Path(runbook)
        if runbook_path.exists():
            polished = runbook_path.read_text()
            polish_model = "gemini_brainstorm"  # Or detect from content

    # Store in DB
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    with _db_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO recall_examples
               (query, session_id, raw_steps, polished_runbook, label, polish_model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                query,
                jsonl_file.stem,
                raw_output[:10000],  # Cap at 10KB
                polished,
                label,
                polish_model,
                now,
            ),
        )
        conn.commit()

    step_count = len(filtered["steps"])
    click.echo(f"Collected recall example: '{query}' ({step_count} steps, label={label})")
    if polished:
        click.echo(f"  Polished runbook attached ({len(polished)} chars)")
    else:
        click.echo("  No polished runbook yet. Use Gemini to polish, then re-run with --runbook")
    click.echo(f"  Session: {jsonl_file.name}")


# ---------------------------------------------------------------------------
# Learning velocity tracking (US3 — FR-014, FR-015, FR-016)
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--error-type",
    default=None,
    help="Filter to specific error type.",
)
@click.option(
    "--window",
    default=7,
    type=int,
    help="Rolling window in days (default: 7).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format (default: table).",
)
@click.option(
    "--skills",
    is_flag=True,
    default=False,
    help="Show per-skill effectiveness metrics.",
)
def velocity(error_type, window, fmt, skills):
    """Show learning velocity trends — how error rates change after rules.

    Computes error frequency per type over a rolling window, measures
    correction decay after rule application, and flags ineffective rules.

    Examples:
        sio velocity                          # All error types, 7-day window
        sio velocity --error-type unused_import
        sio velocity --window 14 --format json
    """
    from sio.core.metrics.velocity import (
        compute_velocity_snapshot,
        get_velocity_trends,
    )

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        # Determine which error types to compute snapshots for
        if error_type:
            error_types = [error_type]
        else:
            rows = conn.execute(
                "SELECT DISTINCT error_type FROM error_records WHERE error_type IS NOT NULL"
            ).fetchall()
            error_types = [r[0] for r in rows]

        if not error_types:
            click.echo("No error records found. Run 'sio mine --since \"7 days\"' first.")
            return

        # Compute fresh snapshots for each error type
        snapshots = []
        for etype in error_types:
            snap = compute_velocity_snapshot(conn, etype, window_days=window)
            snapshots.append(snap)

        # Get historical trends for delta computation
        all_trends: dict[str, list[dict]] = {}
        for etype in error_types:
            all_trends[etype] = get_velocity_trends(conn, error_type=etype)

    if fmt == "json":
        click.echo(_json.dumps(snapshots, indent=2, default=str))
        return

    # Table output using Rich
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()

        table = Table(
            title=f"Learning Velocity Report ({window}-day rolling window)",
            title_style="bold cyan",
        )
        table.add_column("Error Type", style="cyan")
        table.add_column("Rate", justify="right")
        table.add_column("\u0394", justify="right")
        table.add_column("Count", justify="right")
        table.add_column("Rule Applied", justify="center")

        warnings: list[str] = []

        for snap in snapshots:
            etype = snap["error_type"]
            rate_str = f"{snap['error_rate']:.2f}"
            count_str = str(snap["error_count_in_window"])

            # Compute delta from previous snapshot
            trends = all_trends.get(etype, [])
            if len(trends) >= 2:
                prev_rate = trends[-2]["error_rate"]
                curr_rate = snap["error_rate"]
                if prev_rate > 0:
                    delta_pct = ((curr_rate - prev_rate) / prev_rate) * 100
                    if delta_pct < 0:
                        delta_str = f"[green]{delta_pct:+.0f}%[/green]"
                    elif delta_pct > 0:
                        delta_str = f"[red]{delta_pct:+.0f}%[/red]"
                    else:
                        delta_str = "0%"
                else:
                    delta_str = "N/A"
            else:
                delta_str = "-"

            # Rule info
            if snap["rule_applied"]:
                sug_id = snap.get("rule_suggestion_id", "?")
                rule_str = f"#{sug_id}"

                # Flag ineffective rules: applied but no improvement after 5+ sessions
                if snap["adaptation_speed"] is not None and snap["adaptation_speed"] >= 5:
                    decay = snap.get("correction_decay_rate")
                    if decay is not None and decay <= 0:
                        warnings.append(
                            f"[red]\u2717[/red] Rule #{sug_id} ({etype}): "
                            f"no improvement after {snap['adaptation_speed']} "
                            f"sessions -- review recommended"
                        )
                    elif snap["adaptation_speed"] <= 5:
                        warnings.append(
                            f"[yellow]\u26a0[/yellow] Rule #{sug_id} ({etype}): "
                            f"only {snap['adaptation_speed']} sessions since "
                            f"applied -- velocity uncertain"
                        )
            else:
                rule_str = "none"

            table.add_row(etype, rate_str, delta_str, count_str, rule_str)

        console.print()
        console.print(table)

        # Show warnings
        if warnings:
            console.print()
            for w in warnings:
                console.print(f"  {w}")

    except ImportError:
        # Fallback without Rich
        click.echo(f"\nLearning Velocity Report ({window}-day rolling window)\n")
        click.echo(f"{'Error Type':<25} {'Rate':>6} {'Count':>6} {'Rule Applied':>14}")
        click.echo("-" * 55)
        for snap in snapshots:
            rule_str = (
                f"#{snap.get('rule_suggestion_id', '?')}" if snap["rule_applied"] else "none"
            )
            click.echo(
                f"{snap['error_type']:<25} "
                f"{snap['error_rate']:>6.2f} "
                f"{snap['error_count_in_window']:>6} "
                f"{rule_str:>14}"
            )

    # --- Skill effectiveness section (--skills flag) ---
    if skills:
        from sio.core.metrics.velocity import get_skill_effectiveness

        with _db_conn(db_path) as conn:
            skill_metrics = get_skill_effectiveness(conn)

        if not skill_metrics:
            click.echo(
                "\nNo skill effectiveness data. Promote flows to skills and track velocity first."
            )
        else:
            if fmt == "json":
                click.echo(_json.dumps(skill_metrics, indent=2, default=str))
            else:
                try:
                    from rich.console import Console
                    from rich.table import Table

                    console = Console()
                    sk_table = Table(
                        title="Skill Effectiveness",
                        title_style="bold cyan",
                    )
                    sk_table.add_column("Skill", style="cyan", max_width=40)
                    sk_table.add_column("Error Type")
                    sk_table.add_column("Pre Rate", justify="right")
                    sk_table.add_column("Post Rate", justify="right")
                    sk_table.add_column("Improvement", justify="right")
                    sk_table.add_column("Snapshots", justify="right")

                    for sm in skill_metrics:
                        path = sm["skill_path"] or "N/A"
                        # Show just the filename
                        short_path = path.split("/")[-1] if "/" in path else path
                        error_type_str = sm["target_error_type"] or "unknown"
                        pre = f"{sm['pre_rate']:.3f}" if sm["pre_rate"] is not None else "N/A"
                        post = f"{sm['post_rate']:.3f}" if sm["post_rate"] is not None else "N/A"
                        imp = sm["improvement_pct"]
                        if imp is not None:
                            if imp > 0:
                                imp_str = f"[green]{imp:+.1f}%[/green]"
                            elif imp < 0:
                                imp_str = f"[red]{imp:+.1f}%[/red]"
                            else:
                                imp_str = "0.0%"
                        else:
                            imp_str = "N/A"

                        sk_table.add_row(
                            short_path,
                            error_type_str,
                            pre,
                            post,
                            imp_str,
                            str(sm["sessions_tracked"]),
                        )

                    console.print()
                    console.print(sk_table)

                except ImportError:
                    click.echo("\nSkill Effectiveness:")
                    click.echo(
                        f"{'Skill':<40} {'Error':>15} {'Pre':>6} "
                        f"{'Post':>6} {'Impr':>8} {'Snaps':>6}"
                    )
                    click.echo("-" * 85)
                    for sm in skill_metrics:
                        path = (sm["skill_path"] or "N/A").split("/")[-1]
                        click.echo(
                            f"{path:<40} "
                            f"{(sm['target_error_type'] or 'N/A'):>15} "
                            f"{sm.get('pre_rate', 'N/A'):>6} "
                            f"{sm.get('post_rate', 'N/A'):>6} "
                            f"{sm.get('improvement_pct', 'N/A'):>8} "
                            f"{sm['sessions_tracked']:>6}"
                        )


# ---------------------------------------------------------------------------
# Rule violation detection (US5 — FR-026, FR-027)
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--since",
    default=None,
    help="Filter errors after this date (ISO-8601).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format.",
)
def violations(since, fmt):
    """Show detected rule violations (existing rules the assistant ignored).

    Scans CLAUDE.md and all files in the rules/ directory for imperative
    constraints (NEVER, ALWAYS, MUST, DO NOT), then compares mined errors
    against them to detect enforcement failures.

    Violations are flagged at higher priority than new patterns since they
    indicate the rule text is insufficient or the assistant is failing to
    follow it.

    Examples:
        sio violations                          # Default: scan all rule files
        sio violations --since 2026-03-01       # Only recent errors
        sio violations --format json            # JSON output for piping
    """
    from pathlib import Path

    from sio.mining.violation_detector import get_violation_report

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    # Discover rule files: CLAUDE.md + rules/ directory.
    rule_file_paths: list[str] = []

    # Check for CLAUDE.md in common locations.
    claude_md_candidates = [
        Path.home() / ".claude" / "CLAUDE.md",
        Path.cwd() / "CLAUDE.md",
    ]
    for candidate in claude_md_candidates:
        if candidate.exists():
            rule_file_paths.append(str(candidate))

    # Check for rules/ directory files.
    rules_dir = Path.home() / ".claude" / "rules"
    if rules_dir.exists():
        for md_file in sorted(rules_dir.rglob("*.md")):
            rule_file_paths.append(str(md_file))

    # Also check project-level CLAUDE.md and rules/.
    project_claude_md = Path.cwd() / "CLAUDE.md"
    if project_claude_md.exists() and str(project_claude_md) not in rule_file_paths:
        rule_file_paths.append(str(project_claude_md))

    project_rules_dir = Path.cwd() / "rules"
    if project_rules_dir.exists():
        for md_file in sorted(project_rules_dir.rglob("*.md")):
            if str(md_file) not in rule_file_paths:
                rule_file_paths.append(str(md_file))

    if not rule_file_paths:
        click.echo("No instruction files found to scan.")
        click.echo("  Checked: ~/.claude/CLAUDE.md, ./CLAUDE.md, ~/.claude/rules/, ./rules/")
        return

    with _db_conn(db_path) as conn:
        report = get_violation_report(conn, rule_file_paths, since=since)

    if fmt == "json":
        click.echo(_json.dumps(report, indent=2, default=str))
        return

    # Table output using Rich.
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()

        summary = report["violation_summary"]
        date_range = report["date_range"]

        # Build title with date range.
        title = "Rule Violation Report"
        if date_range["start"] and date_range["end"]:
            start_short = (date_range["start"] or "")[:10]
            end_short = (date_range["end"] or "")[:10]
            title += f" ({start_short} to {end_short})"

        if summary:
            table = Table(title=title)
            table.add_column("#", style="bold", width=4)
            table.add_column("Rule", style="cyan", max_width=50)
            table.add_column("Count", justify="right")
            table.add_column("Last", justify="right")
            table.add_column("Sessions", justify="right")

            for i, s in enumerate(summary, 1):
                last_display = (s["last_seen"] or "")[:10]
                table.add_row(
                    str(i),
                    s["rule_text"][:50],
                    str(s["count"]),
                    last_display,
                    str(s["sessions"]),
                )

            console.print()
            console.print(table)

        compliant = report["compliant_rules"]
        if compliant > 0:
            console.print()
            console.print(f"No violations: {compliant} rules fully complied with")

        if not summary:
            console.print()
            console.print("[green]All rules are being followed.[/green]")
            console.print(
                f"  Checked {report['total_rules']} rules across {len(rule_file_paths)} files"
            )

        # Show which files were scanned.
        console.print()
        console.print("[dim]Files scanned:[/dim]")
        for fp in rule_file_paths:
            console.print(f"  [dim]{fp}[/dim]")

    except ImportError:
        # Fallback without Rich.
        summary = report["violation_summary"]
        if summary:
            click.echo("\nRule Violation Report\n")
            click.echo(f"{'#':>3}  {'Rule':<50} {'Count':>5} {'Last':>10} {'Sessions':>8}")
            click.echo("-" * 80)
            for i, s in enumerate(summary, 1):
                click.echo(
                    f"{i:>3}  {s['rule_text'][:50]:<50} "
                    f"{s['count']:>5} "
                    f"{(s['last_seen'] or '')[:10]:>10} "
                    f"{s['sessions']:>8}"
                )

        compliant = report["compliant_rules"]
        if compliant > 0:
            click.echo(f"\nNo violations: {compliant} rules fully complied with")

        if not summary:
            click.echo(f"\nAll rules are being followed. Checked {report['total_rules']} rules.")


# ---------------------------------------------------------------------------
# Instruction budget management (US4 — T041)
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--file",
    "file_path",
    default=None,
    help="Check specific file only.",
)
def budget(file_path):
    """Show instruction budget usage per file.

    Scans CLAUDE.md and supplementary rule files, counting meaningful
    lines (non-blank, non-comment) and comparing against the configured
    caps (default: 100 for CLAUDE.md, 50 for supplementary files).

    Examples:
        sio budget                          # All tracked files
        sio budget --file ~/.claude/CLAUDE.md   # Specific file
    """
    from pathlib import Path

    from rich.console import Console
    from rich.table import Table

    from sio.applier.budget import count_meaningful_lines
    from sio.core.config import load_config

    config = load_config()
    console = Console()

    # Discover files to check
    files_to_check: list[Path] = []

    if file_path:
        target = Path(file_path).expanduser().resolve()
        if target.exists():
            files_to_check.append(target)
        else:
            click.echo(f"File not found: {file_path}")
            return
    else:
        # Auto-discover: CLAUDE.md + rules/ directory files
        claude_md_candidates = [
            Path.home() / ".claude" / "CLAUDE.md",
            Path.cwd() / "CLAUDE.md",
        ]
        for candidate in claude_md_candidates:
            if candidate.exists() and candidate not in files_to_check:
                files_to_check.append(candidate)

        rules_dir = Path.home() / ".claude" / "rules"
        if rules_dir.exists():
            for md_file in sorted(rules_dir.rglob("*.md")):
                if md_file not in files_to_check:
                    files_to_check.append(md_file)

        project_claude_md = Path.cwd() / "CLAUDE.md"
        if project_claude_md.exists() and project_claude_md not in files_to_check:
            files_to_check.append(project_claude_md)

        project_rules_dir = Path.cwd() / "rules"
        if project_rules_dir.exists():
            for md_file in sorted(project_rules_dir.rglob("*.md")):
                if md_file not in files_to_check:
                    files_to_check.append(md_file)

    # Dedupe by resolved realpath — symlinks to the same target count once.
    _seen_real: set[Path] = set()
    _unique: list[Path] = []
    for fp in files_to_check:
        try:
            real = fp.resolve()
        except OSError:
            real = fp
        if real in _seen_real:
            continue
        _seen_real.add(real)
        _unique.append(fp)
    files_to_check = _unique

    if not files_to_check:
        click.echo("No instruction files found to check.")
        return

    table = Table(
        title="Instruction Budget Report",
        title_style="bold cyan",
    )
    table.add_column("File", style="cyan", max_width=40)
    table.add_column("Lines", justify="right")
    table.add_column("Cap", justify="right")
    table.add_column("Status", justify="right")

    for fp in files_to_check:
        lines = count_meaningful_lines(fp)
        name_upper = fp.name.upper()
        cap = (
            config.budget_cap_primary
            if name_upper == "CLAUDE.MD"
            else config.budget_cap_supplementary
        )
        utilization = lines / cap if cap > 0 else 1.0
        pct = utilization * 100

        if pct >= 95:
            status = f"[red]{pct:.0f}%[/red]"
        elif pct >= 80:
            status = f"[yellow]{pct:.0f}%[/yellow]"
        else:
            status = f"[green]{pct:.0f}%[/green]"

        # Show abbreviated path
        try:
            display_path = str(fp.relative_to(Path.home()))
            display_path = "~/" + display_path
        except ValueError:
            display_path = str(fp)

        table.add_row(display_path, str(lines), str(cap), status)

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Deduplication command (US4 — T042)
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--threshold",
    default=0.85,
    type=float,
    help="Similarity threshold (default: 0.85).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show proposals without applying.",
)
@click.option(
    "--auto",
    "auto_apply",
    is_flag=True,
    default=False,
    help="Apply all proposals without confirmation.",
)
def dedupe(threshold, dry_run, auto_apply):
    """Find and consolidate semantically duplicate rules.

    Scans all instruction files (CLAUDE.md + rules/) for rule blocks
    that are semantically similar above the threshold. Shows duplicate
    pairs with proposed merges.

    Examples:
        sio dedupe                          # Default: threshold 0.85
        sio dedupe --threshold 0.80         # Lower threshold
        sio dedupe --dry-run                # Show without applying
        sio dedupe --auto                   # Apply all without prompts
    """
    from pathlib import Path

    from rich.console import Console

    from sio.applier.deduplicator import (
        DuplicatePair,
        find_duplicates,
        propose_merge,
    )

    console = Console()

    # Discover instruction files
    file_paths: list[str] = []

    claude_md_candidates = [
        Path.home() / ".claude" / "CLAUDE.md",
        Path.cwd() / "CLAUDE.md",
    ]
    for candidate in claude_md_candidates:
        if candidate.exists():
            file_paths.append(str(candidate))

    rules_dir = Path.home() / ".claude" / "rules"
    if rules_dir.exists():
        for md_file in sorted(rules_dir.rglob("*.md")):
            file_paths.append(str(md_file))

    project_claude_md = Path.cwd() / "CLAUDE.md"
    if project_claude_md.exists() and str(project_claude_md) not in file_paths:
        file_paths.append(str(project_claude_md))

    project_rules_dir = Path.cwd() / "rules"
    if project_rules_dir.exists():
        for md_file in sorted(project_rules_dir.rglob("*.md")):
            if str(md_file) not in file_paths:
                file_paths.append(str(md_file))

    if not file_paths:
        click.echo("No instruction files found to scan.")
        return

    console.print(f"Scanning {len(file_paths)} files (threshold: {threshold:.2f})...")

    pairs: list[DuplicatePair] = find_duplicates(file_paths, threshold)

    if not pairs:
        console.print(f"\n[green]No duplicates found above threshold {threshold:.2f}.[/green]")
        return

    console.print(f"\n[bold]Duplicate Rule Analysis (threshold: {threshold:.2f})[/bold]\n")

    applied_count = 0
    for i, pair in enumerate(pairs, 1):
        # Show the pair
        console.print(f"[bold]Pair {i}[/bold] (similarity: {pair.similarity:.2f}):")

        # Abbreviate file paths for display
        try:
            display_a = str(Path(pair.file_a).relative_to(Path.home()))
            display_a = "~/" + display_a
        except ValueError:
            display_a = pair.file_a

        try:
            display_b = str(Path(pair.file_b).relative_to(Path.home()))
            display_b = "~/" + display_b
        except ValueError:
            display_b = pair.file_b

        text_a_short = pair.text_a[:80].replace("\n", " ")
        text_b_short = pair.text_b[:80].replace("\n", " ")

        console.print(f'  A: "{text_a_short}" ({display_a}:{pair.line_a})')
        console.print(f'  B: "{text_b_short}" ({display_b}:{pair.line_b})')

        merged = propose_merge(pair)
        merged_short = merged[:100].replace("\n", " ")
        console.print(f'  Proposed merge: "{merged_short}"')

        if dry_run:
            console.print("  [dim][dry-run] Skipped.[/dim]")
            console.print()
            continue

        if auto_apply:
            from sio.applier.deduplicator import apply_merge

            apply_merge(pair, merged)
            console.print("  [green]Auto-applied.[/green]")
            applied_count += 1
            console.print()
            continue

        choice = click.prompt(
            "  Apply? [y/n]",
            type=click.Choice(["y", "n"]),
            default="n",
        )
        if choice == "y":
            from sio.applier.deduplicator import apply_merge

            apply_merge(pair, merged)
            applied_count += 1
            console.print("  [green]Applied.[/green]")
        else:
            console.print("  [dim]Skipped.[/dim]")
        console.print()

    if applied_count > 0 and not dry_run:
        # If any were applied, trigger consolidation on affected files
        from sio.applier.budget import trigger_consolidation
        from sio.core.config import load_config

        config = load_config()
        affected_files = {pair.file_a for pair in pairs}
        affected_files |= {pair.file_b for pair in pairs}
        for fp in affected_files:
            trigger_consolidation(fp, config)

        console.print(f"[green]Consolidated {applied_count} duplicate pair(s).[/green]")
    elif dry_run:
        console.print(
            f"[yellow]{len(pairs)} duplicate pair(s) found. "
            f"Run without --dry-run to apply.[/yellow]"
        )


# ---------------------------------------------------------------------------
# Autoresearch commands (T076 / US8)
# ---------------------------------------------------------------------------


@cli.group()
def autoresearch():
    """Autonomous optimisation loop — mine, cluster, grade, generate, experiment."""
    pass


@autoresearch.command("start")
@click.option(
    "--interval",
    default=30,
    type=int,
    help="Minutes between cycles (default: 30).",
)
@click.option(
    "--max-cycles",
    default=None,
    type=int,
    help="Stop after N cycles (default: unlimited).",
)
@click.option(
    "--max-experiments",
    default=3,
    type=int,
    help="Max concurrent experiments (default: 3).",
)
@click.option("--dry-run", is_flag=True, help="Run pipeline but don't create experiments.")
def autoresearch_start(interval, max_cycles, max_experiments, dry_run):
    """Start the autonomous optimisation loop."""
    from sio.core.arena.autoresearch import AutoResearchLoop
    from sio.core.config import load_config

    db_path = os.path.expanduser("~/.sio/sio.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    config = load_config()
    config.max_experiments = max_experiments

    click.echo(
        f"AutoResearch Loop started (interval: {interval}m, max experiments: {max_experiments})"
    )

    with _db_conn(db_path) as conn:
        loop = AutoResearchLoop(conn, config)
        loop.start(
            interval_minutes=interval,
            max_cycles=max_cycles,
        )

    click.echo("AutoResearch Loop stopped.")


@autoresearch.command("stop")
def autoresearch_stop():
    """Stop the autonomous optimisation loop."""
    sentinel = os.path.expanduser("~/.sio/autoresearch.stop")
    os.makedirs(os.path.dirname(sentinel), exist_ok=True)
    with open(sentinel, "w") as f:
        f.write("")
    click.echo("Stop sentinel written. Loop will exit after current cycle.")


@autoresearch.command("status")
def autoresearch_status():
    """Show autoresearch loop status."""
    from sio.core.arena.txlog import TxLog

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    sentinel = os.path.expanduser("~/.sio/autoresearch.stop")
    running = not os.path.exists(sentinel)

    with _db_conn(db_path) as conn:
        txlog = TxLog(conn)
        entries = txlog.read_log()
        active = txlog.active_experiment_count()

    if not entries:
        click.echo("AutoResearch has not been run yet.")
        return

    cycles = set(e.get("cycle_number") for e in entries)
    promoted = sum(
        1 for e in entries if e.get("action") == "promote" and e.get("status") == "success"
    )
    rolled_back = sum(
        1 for e in entries if e.get("action") == "rollback" and e.get("status") == "success"
    )

    click.echo("AutoResearch Status")
    click.echo(f"  Running:           {'yes' if running else 'no (stopped)'}")
    click.echo(f"  Cycles completed:  {len(cycles)}")
    click.echo(f"  Active experiments: {active}")
    click.echo(f"  Promoted:          {promoted}")
    click.echo(f"  Rolled back:       {rolled_back}")


# ---------------------------------------------------------------------------
# Interactive reporting (US9)
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--html", "html_flag", is_flag=True, help="Generate HTML report.")
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file path (default: ~/.sio/reports/report-YYYYMMDD.html).",
)
@click.option(
    "--days",
    default=30,
    type=int,
    help="Lookback period in days (default: 30).",
)
@click.option(
    "--open",
    "open_flag",
    is_flag=True,
    help="Open report in browser after generation.",
)
def report(html_flag, output, days, open_flag):
    """Generate a session report (terminal or HTML).

    Without --html: show a plain text summary via Rich.
    With --html: generate a self-contained HTML file.

    Examples:
        sio report                          # Terminal summary
        sio report --html                   # HTML report (default path)
        sio report --html -o my-report.html # Custom output path
        sio report --html --open            # Generate and open in browser
    """
    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    if html_flag:
        _report_html(db_path, output, days, open_flag)
    else:
        _report_terminal(db_path, days)


def _report_html(
    db_path: str,
    output: str | None,
    days: int,
    open_flag: bool,
) -> None:
    """Generate and write an HTML report."""
    from datetime import datetime as _dt

    from sio.reports.html_report import generate_html_report

    with _db_conn(db_path) as conn:
        html = generate_html_report(conn, days=days)

    if output is None:
        reports_dir = os.path.expanduser("~/.sio/reports")
        os.makedirs(reports_dir, exist_ok=True)
        datestamp = _dt.now().strftime("%Y%m%d")
        output = os.path.join(reports_dir, f"report-{datestamp}.html")

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)

    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    click.echo(f"Report saved: {output}")

    if open_flag:
        import webbrowser

        webbrowser.open(f"file://{os.path.abspath(output)}")
        click.echo("Opening in browser...")


def _report_terminal(db_path: str, days: int) -> None:
    """Show a plain text summary using Rich."""
    from datetime import datetime as _dt
    from datetime import timedelta, timezone

    with _db_conn(db_path) as conn:
        cutoff = (_dt.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Session metrics summary
        metrics = conn.execute(
            "SELECT COUNT(*) as cnt, "
            "COALESCE(SUM(total_input_tokens + total_output_tokens), 0) as tokens, "
            "COALESCE(SUM(total_cost_usd), 0) as cost, "
            "COALESCE(SUM(error_count), 0) as errors, "
            "COALESCE(AVG(cache_hit_ratio), 0) as cache_avg "
            "FROM session_metrics WHERE mined_at >= ?",
            (cutoff,),
        ).fetchone()

        # Pattern count
        pattern_count = conn.execute(
            "SELECT COUNT(*) FROM patterns",
        ).fetchone()[0]

        # Suggestion count
        suggestion_count = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE status IN ('pending', 'approved')",
        ).fetchone()[0]

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(
            title=f"SIO Report ({days}-day window)",
            show_header=False,
            title_style="bold cyan",
            border_style="dim",
        )
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right")

        table.add_row("Sessions analyzed", str(metrics[0]))
        table.add_row("Total tokens", f"{metrics[1]:,}")
        table.add_row("Total cost", f"${metrics[2]:.2f}")
        table.add_row("Total errors", str(metrics[3]))
        table.add_row(
            "Avg cache efficiency",
            f"{metrics[4] * 100:.1f}%",
        )
        table.add_row("Patterns discovered", str(pattern_count))
        table.add_row("Pending suggestions", str(suggestion_count))

        console.print()
        console.print(table)
        console.print()
        console.print(
            "[dim]Use --html for a full interactive report with charts.[/dim]",
        )

    except ImportError:
        click.echo(f"SIO Report ({days}-day window)")
        click.echo(f"  Sessions:    {metrics[0]}")
        click.echo(f"  Tokens:      {metrics[1]:,}")
        click.echo(f"  Cost:        ${metrics[2]:.2f}")
        click.echo(f"  Errors:      {metrics[3]}")
        click.echo(f"  Cache:       {metrics[4] * 100:.1f}%")
        click.echo(f"  Patterns:    {pattern_count}")
        click.echo(f"  Suggestions: {suggestion_count}")


# ---------------------------------------------------------------------------
# Flow-to-skill promotion (Phase 3)
# ---------------------------------------------------------------------------


@cli.command("promote-flow")
@click.argument("flow_hash")
def promote_flow(flow_hash):
    """Promote a flow pattern to a Claude Code skill file.

    Takes a flow hash (from `sio flows` output) and generates a skill
    Markdown file in ~/.claude/skills/ based on the observed tool sequence.

    Examples:
        sio promote-flow abc123def456
    """
    from sio.clustering.grader import promote_flow_to_skill

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        raise SystemExit(1)

    with _db_conn(db_path) as conn:
        result = promote_flow_to_skill(conn, flow_hash)

    if result is None:
        click.echo(f"Could not promote flow '{flow_hash}'. Flow not found or insufficient data.")
        raise SystemExit(1)

    click.echo(f"Skill generated: {result}")


# ---------------------------------------------------------------------------
# Skill candidate discovery (Phase 5)
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--repo",
    default=".",
    help="Repository path for repo-specific pattern detection.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format (default: table).",
)
def discover(repo, fmt):
    """Discover skill candidates from mined patterns and flows.

    Cross-references error patterns with positive flow events to find
    candidates worth promoting to Claude Code skills.

    Candidate types:
        tool-specific     -- Concentrated on a single tool (e.g. "Edit safety")
        workflow-sequence  -- Recurring multi-tool flows (e.g. "Read -> Edit -> Test")
        repo-specific     -- Patterns unique to a specific project

    Examples:
        sio discover
        sio discover --repo /home/user/myproject
        sio discover --format json
    """
    from sio.suggestions.discoverer import discover_skill_candidates

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        candidates = discover_skill_candidates(conn, repo_path=repo)

    if not candidates:
        click.echo("No skill candidates found. Mine more sessions or lower thresholds.")
        return

    if fmt == "json":
        click.echo(_json.dumps(candidates, indent=2, default=str))
        return

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()

        table = Table(
            title="Skill Candidates",
            title_style="bold cyan",
        )
        table.add_column("#", style="dim", width=3)
        table.add_column("Description", style="cyan", max_width=55)
        table.add_column("Type", justify="center")
        table.add_column("Errors", justify="right")
        table.add_column("Sessions", justify="right")
        table.add_column("Confidence", justify="right")
        table.add_column("Flows", justify="right")

        for i, c in enumerate(candidates, 1):
            conf = c["confidence"]
            if conf >= 0.7:
                conf_str = f"[green]{conf:.2f}[/green]"
            elif conf >= 0.4:
                conf_str = f"[yellow]{conf:.2f}[/yellow]"
            else:
                conf_str = f"[dim]{conf:.2f}[/dim]"

            type_str = c["suggested_skill_type"]
            if type_str == "workflow-sequence":
                type_str = f"[blue]{type_str}[/blue]"
            elif type_str == "repo-specific":
                type_str = f"[magenta]{type_str}[/magenta]"

            table.add_row(
                str(i),
                c["description"][:55],
                type_str,
                str(c["error_count"]),
                str(c["session_count"]),
                conf_str,
                str(len(c["flow_hashes"])),
            )

        console.print()
        console.print(table)

    except ImportError:
        click.echo("\nSkill Candidates:\n")
        click.echo(
            f"{'#':>3}  {'Description':<55} {'Type':<20} {'Errors':>6} {'Sess':>5} {'Conf':>6}"
        )
        click.echo("-" * 100)
        for i, c in enumerate(candidates, 1):
            click.echo(
                f"{i:>3}  {c['description'][:55]:<55} "
                f"{c['suggested_skill_type']:<20} "
                f"{c['error_count']:>6} {c['session_count']:>5} "
                f"{c['confidence']:>6.2f}"
            )


# ---------------------------------------------------------------------------
# sio db — schema migration commands (T013, FR-017)
# ---------------------------------------------------------------------------


@cli.group()
def db():
    """Database schema management commands."""


@db.command("migrate")
@click.option(
    "--db-path",
    default=os.path.expanduser("~/.sio/sio.db"),
    help="Path to the SIO database.",
    show_default=True,
)
def db_migrate(db_path):
    """Apply any pending schema migrations to the SIO database.

    Runs ensure_schema_version() to seed the baseline row, then
    executes any scripts/migrate_00N.py found in the project root.
    """
    import glob as _glob
    import importlib.util
    from pathlib import Path

    from sio.core.db.schema import ensure_schema_version, refuse_to_start

    click.echo(f"Opening database: {db_path}")
    conn = _get_sio_db_conn()
    if conn is None:
        click.echo(
            f"Database not found at {db_path}. Run 'sio install' first.",
            err=True,
        )
        raise SystemExit(1)

    try:
        refuse_to_start(conn)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"ERROR: {exc}", err=True)
        click.echo("Run 'sio db repair' to resolve partial migrations.", err=True)
        raise SystemExit(1)

    ensure_schema_version(conn)
    click.echo("schema_version: baseline row confirmed.")

    # Discover migration scripts in the scripts/ directory relative to the
    # installed package or CWD as fallback.
    project_candidates = [
        Path(__file__).parents[4] / "scripts",  # editable install
        Path.cwd() / "scripts",
    ]
    scripts_dir = next((p for p in project_candidates if p.is_dir()), None)
    if scripts_dir is None:
        click.echo("No scripts/ directory found — nothing to migrate.")
        conn.close()
        return

    migration_scripts = sorted(_glob.glob(str(scripts_dir / "migrate_*.py")))
    if not migration_scripts:
        click.echo("No migration scripts found.")
        conn.close()
        return

    applied = 0
    for script_path in migration_scripts:
        spec = importlib.util.spec_from_file_location("_migration", script_path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if hasattr(mod, "migrate"):
            click.echo(f"Applying {Path(script_path).name}...")
            mod.migrate(Path(db_path))
            applied += 1

    conn.close()
    click.echo(f"Migration complete. {applied} script(s) applied.")


@db.command("repair")
@click.option(
    "--db-path",
    default=os.path.expanduser("~/.sio/sio.db"),
    help="Path to the SIO database.",
    show_default=True,
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
def db_repair(db_path, yes):
    """Mark stuck 'applying' migration rows as 'failed'.

    Use this command after a migration crashed mid-run.  Operator confirmation
    is required unless --yes is passed.
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT version, description FROM schema_version WHERE status='applying'"
        ).fetchall()
    except _sqlite3.OperationalError:
        click.echo("schema_version table not found — nothing to repair.")
        conn.close()
        return

    if not rows:
        click.echo("No stuck migrations found.")
        conn.close()
        return

    click.echo(f"Found {len(rows)} stuck migration(s):")
    for version, desc in rows:
        click.echo(f"  version={version}: {desc!r}")

    if not yes:
        confirmed = click.confirm("Mark all as 'failed'? This allows SIO to start again.")
        if not confirmed:
            click.echo("Repair cancelled.")
            conn.close()
            return

    conn.execute("UPDATE schema_version SET status='failed' WHERE status='applying'")
    conn.commit()
    conn.close()
    click.echo(f"Marked {len(rows)} migration(s) as 'failed'. SIO can now start.")


# ---------------------------------------------------------------------------
# autoresearch commands (T077 — US4)
# ---------------------------------------------------------------------------


@cli.group()
def autoresearch():
    """Autoresearch pipeline — automated suggestion evaluation and scheduling."""


@autoresearch.command("run-once")
@click.option(
    "--auto-approve-above",
    default=None,
    type=float,
    help=(
        "When set, suggestions with arena_passed=1 and metric_score >= this "
        "threshold are automatically promoted. Default: None (pending approval)."
    ),
)
@click.option("--db-path", default=None, help="Path to sio.db (overrides SIO_DB_PATH).")
def autoresearch_run_once_cmd(auto_approve_above, db_path):
    """Evaluate active suggestions once and record outcomes in autoresearch_txlog."""
    import json

    from sio.autoresearch.scheduler import autoresearch_run_once

    conn = _db_conn(db_path) if db_path else _get_sio_db_conn()
    try:
        result = autoresearch_run_once(conn, auto_approve_above=auto_approve_above)
    finally:
        conn.close()

    click.echo(json.dumps(result, indent=2))


@autoresearch.command("install-schedule")
@click.argument("method", type=click.Choice(["cron", "systemd"]), default="systemd")
def autoresearch_install_schedule(method):
    """Install the autoresearch recurring schedule (cron or systemd timer)."""
    import subprocess
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"

    if method == "systemd":
        script = scripts_dir / "install_autoresearch_systemd.sh"
        if not script.exists():
            click.echo(f"Systemd install script not found: {script}", err=True)
            sys.exit(1)
        result = subprocess.run(["bash", str(script)], check=False)
        sys.exit(result.returncode)
    else:
        click.echo(
            "Cron install: add the following entry to your crontab (crontab -e):\n"
            "  0 4 * * * python -m scripts.autoresearch_cron\n"
            "Or run: bash scripts/install_autoresearch_systemd.sh for systemd.",
        )


if __name__ == "__main__":
    cli()
