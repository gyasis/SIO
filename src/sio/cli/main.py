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
@click.option("--since", required=True, help='Time window: "3 days", "2 weeks", "1 month", "6h", "yesterday", "3 days ago", "2026-01-15".')
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
@click.option(
    "--type", "error_type", default=None,
    type=click.Choice(["tool_failure", "user_correction", "repeated_attempt", "undo", "agent_admission"]),
    help="Filter by error type.",
)
def patterns(error_type):
    """Show discovered error patterns ranked by importance."""
    from rich.console import Console
    from rich.table import Table

    from sio.clustering.pattern_clusterer import cluster_errors
    from sio.clustering.ranker import rank_patterns
    from sio.core.db.queries import get_error_records
    from sio.core.db.schema import init_db

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

    # Filter by type if requested
    if error_type:
        errors = [e for e in errors if e.get("error_type") == error_type]
        if not errors:
            click.echo(f"No '{error_type}' errors found.")
            conn.close()
            return

    # Cluster and rank
    clustered = cluster_errors(errors)
    ranked = rank_patterns(clustered)

    title = f"Error Patterns — {error_type}" if error_type else "Error Patterns (ranked by importance)"
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
    conn.close()


@cli.command()
@click.option(
    "--type", "error_type", default=None,
    type=click.Choice(["tool_failure", "user_correction", "repeated_attempt", "undo", "agent_admission"]),
    help="Filter by error type.",
)
@click.option("--limit", "-n", default=20, help="Max errors to show.")
@click.option(
    "--grep", "-g", "grep_term", default=None,
    help="Search error text, user message, and context for a keyword (case-insensitive).",
)
def errors(error_type, limit, grep_term):
    """Browse mined errors with optional type and content filters."""
    from rich.console import Console
    from rich.table import Table

    from sio.core.db.schema import init_db

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    conn = init_db(db_path)

    # Build query based on filters
    where_clauses = ["1=1"]
    params: list = []

    if error_type:
        where_clauses.append("error_type = ?")
        params.append(error_type)

    if grep_term:
        # Search across error_text, user_message, context_before, context_after, source_file
        where_clauses.append(
            "(error_text LIKE ? OR user_message LIKE ? OR "
            "context_before LIKE ? OR context_after LIKE ? OR "
            "source_file LIKE ?)"
        )
        like_term = f"%{grep_term}%"
        params.extend([like_term] * 5)

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
        conn.close()
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

    conn.close()


@cli.group(invoke_without_command=True)
@click.pass_context
def datasets(ctx):
    """Manage pattern datasets."""
    if ctx.invoked_subcommand is None:
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


@cli.command()
@click.option(
    "--type", "error_type", default=None,
    type=click.Choice(["tool_failure", "user_correction", "repeated_attempt", "undo", "agent_admission"]),
    help="Only analyze errors of this type.",
)
@click.option("--min-examples", default=3, help="Min examples to build a dataset.")
@click.option(
    "--grep", "-g", "grep_term", default=None,
    help="Filter errors by keyword in content (e.g. 'databricks', 'SQL', 'snowflake').",
)
@click.option(
    "--verbose", "-v", is_flag=True, default=False,
    help="Enable verbose DSPy trace logging.",
)
def suggest(error_type, min_examples, grep_term, verbose):
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
    from sio.core.db.schema import init_db
    from sio.datasets.builder import build_dataset
    from sio.suggestions.generator import generate_suggestions

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    conn = init_db(db_path)
    console = Console()

    # 1. Get all errors (no limit)
    all_errors = get_error_records(conn, limit=0)
    if not all_errors:
        click.echo("No errors mined yet. Run 'sio mine --since \"7 days\"' first.")
        conn.close()
        return

    errors_to_cluster = all_errors

    # Apply error type filter
    if error_type:
        errors_to_cluster = [e for e in errors_to_cluster if e.get("error_type") == error_type]

    # Apply content grep filter — searches across error_text, user_message,
    # context_before, context_after, and source_file
    if grep_term:
        term_lower = grep_term.lower()

        def _matches_grep(e: dict) -> bool:
            for field in ("error_text", "user_message", "context_before", "context_after", "source_file"):
                val = e.get(field) or ""
                if term_lower in val.lower():
                    return True
            return False

        errors_to_cluster = [e for e in errors_to_cluster if _matches_grep(e)]

    if not errors_to_cluster:
        filter_desc = []
        if error_type:
            filter_desc.append(f"type='{error_type}'")
        if grep_term:
            filter_desc.append(f"grep='{grep_term}'")
        click.echo(f"No errors matching {', '.join(filter_desc)} found.")
        conn.close()
        return

    filter_msg = ""
    if grep_term:
        filter_msg = f" matching '{grep_term}'"
    console.print(f"[bold]Step 1:[/bold] Clustering {len(errors_to_cluster)} errors{filter_msg}...")

    # 2. Cluster and rank
    clustered = cluster_errors(errors_to_cluster)
    ranked = rank_patterns(clustered)
    console.print(f"  Found {len(ranked)} patterns")

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

    # 4. Build datasets
    console.print("[bold]Step 3:[/bold] Building datasets...")
    datasets: dict[str, dict] = {}
    for p in persisted_patterns:
        metadata = build_dataset(p, all_errors, conn, min_threshold=min_examples)
        if metadata is not None:
            pid = metadata["pattern_id"]
            # Store/update dataset record in DB
            existing = conn.execute(
                "SELECT id FROM datasets WHERE pattern_id = ? ORDER BY id DESC LIMIT 1",
                (p["id"],),
            ).fetchone()
            if existing:
                metadata["id"] = existing[0]
            else:
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
    suggestions = generate_suggestions(
        persisted_patterns, datasets, conn, verbose=verbose,
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

    conn.close()


@cli.command("suggest-review")
def suggest_review():
    """Review pending improvement suggestions interactively."""
    from rich.console import Console
    from rich.table import Table

    from sio.core.db.schema import init_db
    from sio.review.reviewer import approve as do_approve
    from sio.review.reviewer import defer as do_defer
    from sio.review.reviewer import reject as do_reject
    from sio.review.reviewer import review_pending

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    conn = init_db(db_path)
    pending = review_pending(conn)

    if not pending:
        click.echo("No pending suggestions to review.")
        conn.close()
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

    conn.close()


@cli.command()
@click.argument("suggestion_id", type=int)
@click.option("--note", "-n", default=None, help="Optional note.")
def approve(suggestion_id, note):
    """Approve a suggestion by ID."""
    from sio.core.db.schema import init_db
    from sio.review.reviewer import approve as do_approve

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    conn = init_db(db_path)
    ok = do_approve(conn, suggestion_id, note)
    conn.close()
    if ok:
        click.echo(f"Suggestion {suggestion_id} approved.")
    else:
        click.echo(f"Suggestion {suggestion_id} not found.")
        raise SystemExit(1)


@cli.command()
@click.argument("suggestion_id", type=int)
@click.option("--note", "-n", default=None, help="Optional note.")
def reject(suggestion_id, note):
    """Reject a suggestion by ID."""
    from sio.core.db.schema import init_db
    from sio.review.reviewer import reject as do_reject

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    conn = init_db(db_path)
    ok = do_reject(conn, suggestion_id, note)
    conn.close()
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
    from sio.core.db.schema import init_db

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    conn = init_db(db_path)
    result = apply_change(conn, suggestion_id)
    conn.close()

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
    from sio.core.db.schema import init_db

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    conn = init_db(db_path)
    result = rollback_change(conn, change_id)
    conn.close()
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

    from sio.core.db.schema import init_db

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No database found.")
        return

    conn = init_db(db_path)
    rows = conn.execute(
        "SELECT ac.id, ac.suggestion_id, ac.target_file, ac.applied_at, "
        "ac.rolled_back_at, s.description "
        "FROM applied_changes ac "
        "LEFT JOIN suggestions s ON s.id = ac.suggestion_id "
        "ORDER BY ac.applied_at DESC"
    ).fetchall()
    conn.close()

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
    from sio.core.db.schema import init_db

    db_path = os.path.expanduser("~/.sio/sio.db")
    if not os.path.exists(db_path):
        click.echo("No SIO database found. Run 'sio mine' to start.")
        return

    conn = init_db(db_path)
    errors = conn.execute("SELECT COUNT(*) FROM error_records").fetchone()[0]
    patterns = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
    datasets = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM suggestions WHERE status = 'pending'"
    ).fetchone()[0]
    applied = conn.execute(
        "SELECT COUNT(*) FROM applied_changes WHERE rolled_back_at IS NULL"
    ).fetchone()[0]
    conn.close()

    click.echo("SIO v2 Status")
    click.echo("-" * 30)
    click.echo(f"Errors mined:      {errors}")
    click.echo(f"Patterns found:    {patterns}")
    click.echo(f"Datasets built:    {datasets}")
    click.echo(f"Pending reviews:   {pending}")
    click.echo(f"Applied changes:   {applied}")


if __name__ == "__main__":
    cli()
