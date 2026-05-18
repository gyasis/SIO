"""SIO CLI — Self-Improving Organism command-line interface."""

import json as _json
import os
import time
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from importlib.metadata import version as pkg_version

import click

from sio.core.constants import DEFAULT_PLATFORM
from sio.core.observability import log_failure
from sio.core.runlog import current as _runlog_current, runlogged

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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        return None
    return init_db(db_path)


try:
    _sio_version = pkg_version("self-improving-organism")
except PackageNotFoundError:
    try:
        # Fall back to legacy package name during dev / mid-rename installs.
        _sio_version = pkg_version("sio")
    except PackageNotFoundError:
        _sio_version = "0.0.0-dev"


@click.group()
@click.version_option(version=_sio_version)
def cli():
    """SIO: Self-Improving Organism for AI coding CLIs."""
    pass


@cli.command()
@click.option(
    "--harness",
    default=None,
    help="Target harness (claude-code, cursor, windsurf, opencode). "
    "If omitted, auto-detects every harness installed on this system.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview the file changes without writing anything.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite user-modified files (default: skip + report drift).",
)
@click.option(
    "--uninstall",
    is_flag=True,
    help="Remove SIO-managed assets instead of installing.",
)
@click.option(
    "--status",
    is_flag=True,
    help="Show what's installed vs what the package ships, without changing anything.",
)
@click.option(
    "--link-path",
    is_flag=True,
    help=(
        "Append a managed `export PATH=...` block to the user's shell rc file "
        "(~/.zshrc, ~/.bashrc, etc.) so the `sio` binary is reachable from "
        "subprocesses with sanitized environments (e.g., the Bash tool inside "
        "Claude Code). Skipped on --status / --dry-run unless explicit."
    ),
)
@runlogged("init")
def init(
    harness: str | None,
    dry_run: bool,
    force: bool,
    uninstall: bool,
    status: bool,
    link_path: bool,
) -> None:
    """Stage SIO's bundled skills and rules into your AI coding harness.

    By default, copies the package's bootstrap content (skills, tool rules)
    into the user's harness config directory (e.g., ~/.claude/) using a
    sidecar manifest to track managed files. Re-running is idempotent.
    User-modified files are preserved unless --force is set.

    \b
    Examples:
        sio init                    # auto-detect harness, install
        sio init --dry-run          # preview only
        sio init --status           # what's installed where
        sio init --uninstall        # remove SIO-managed files
        sio init --harness claude-code --force
    """
    from rich.console import Console
    from rich.table import Table

    from sio.harnesses import ALL_ADAPTERS, detect_adapters, get_adapter
    from sio.harnesses.bootstrap import BootstrapMissingError, seed_sio_home

    console = Console()

    # Step 0 — seed ~/.sio/ data dir + config.toml template before any
    # harness adapter runs. This is harness-agnostic infrastructure
    # (DB, datasets cache, LM config) and should land regardless of
    # which adapter we're about to invoke. Skip on --status (read-only).
    if not status:
        home_report = seed_sio_home(dry_run=dry_run)
        console.print(
            f"\n[bold magenta]→ ~/.sio/ data dir[/bold magenta]  "
            f"({home_report.sio_home})"
        )
        for action, path, reason in home_report.actions:
            tag = "[dim](dry-run)[/dim] " if dry_run else ""
            color = {"create": "green", "skip": "white"}.get(
                action.replace("would-", ""), "white"
            )
            console.print(f"  {tag}[{color}]{action:<14}[/{color}] {path}  {reason}")

        # Harness-agnostic canonical-DB bootstrap. Runs ONCE before any
        # adapter so the canonical sio.db is ready (schema, schema_version
        # baseline, 004 migration, split-brain backfill) regardless of
        # which harness was selected. Skip on --uninstall (would create
        # the very files we're about to remove) and --dry-run (read-only).
        if not uninstall and not dry_run:
            from sio.core.db.bootstrap import ensure_canonical_db_ready  # noqa: PLC0415

            try:
                canonical_db = ensure_canonical_db_ready()
                console.print(
                    f"  [green]{'ready':<14}[/green] {canonical_db}  "
                    f"canonical DB schema verified"
                )
            except Exception as exc:  # noqa: BLE001
                console.print(
                    f"  [yellow]{'warn':<14}[/yellow] canonical DB bootstrap "
                    f"failed: {exc} — continuing"
                )
        elif dry_run and not uninstall:
            console.print(
                "  [dim](dry-run)[/dim] [white]would-ready    [/white] "
                "~/.sio/sio.db  canonical DB schema verify"
            )

    if harness:
        try:
            adapters = [get_adapter(harness)]
        except ValueError as e:
            console.print(f"[red]error:[/red] {e}")
            raise SystemExit(2) from None
        # Risk #3 fix: when the user passes --harness explicitly, auto-create
        # the harness's config dir if it doesn't exist yet. Without this,
        # a fresh box where Claude Code has never launched gets "no harnesses
        # detected" and `sio init` exits with nothing staged — looks like a
        # silent install failure to the user.
        for adapter in adapters:
            if not adapter.detect() and not status and not uninstall and not dry_run:
                adapter.config_dir.mkdir(parents=True, exist_ok=True)
                console.print(
                    f"[yellow]→ created {adapter.config_dir}[/yellow] "
                    f"(harness config dir didn't exist; --harness was explicit)"
                )
    else:
        adapters = detect_adapters()
        if not adapters:
            known = ", ".join(c.name for c in ALL_ADAPTERS)
            console.print(
                f"[yellow]No supported harnesses detected.[/yellow] Known: {known}\n"
                f"To force-install for a specific harness even if its config "
                f"dir doesn't exist yet:\n"
                f"  sio init --harness claude-code"
            )
            raise SystemExit(1)

    # Track whether anything was actually staged across all adapters so we
    # can surface a clear restart-Claude-Code message at the end (Risk #2).
    any_creates = False

    for adapter in adapters:
        console.print(f"\n[bold cyan]→ {adapter.name}[/bold cyan]  ({adapter.config_dir})")

        if status:
            sr = adapter.status()
            tbl = Table(show_header=True, header_style="bold")
            tbl.add_column("State")
            tbl.add_column("Count")
            tbl.add_row("installed", str(len(sr.installed_files)))
            tbl.add_row("missing", str(len(sr.missing_files)))
            tbl.add_row("drifted", str(len(sr.drifted_files)))
            console.print(tbl)
            for note in sr.notes:
                console.print(f"  [dim]· {note}[/dim]")
            continue

        if uninstall:
            ir = adapter.uninstall(dry_run=dry_run)
        else:
            try:
                # Lifecycle: pre_install (DB schema + migrations) →
                # install (file staging) → post_install (hook registration
                # + platform_config write). Default pre/post are no-op so
                # harnesses without orchestration concerns are unaffected.
                pre_ir = adapter.pre_install(dry_run=dry_run)
                ir = adapter.install(dry_run=dry_run, force=force)
                post_ir = adapter.post_install(dry_run=dry_run)
            except BootstrapMissingError as e:
                console.print(f"[red]bootstrap missing:[/red] {e}")
                raise SystemExit(3) from None
            # Merge lifecycle reports into the main one for unified rendering
            ir.changes = [*pre_ir.changes, *ir.changes, *post_ir.changes]
            ir.errors = [*pre_ir.errors, *ir.errors, *post_ir.errors]

        for ch in ir.changes:
            tag = "[dim](dry-run)[/dim] " if dry_run else ""
            color = {"create": "green", "update": "yellow", "remove": "red"}.get(
                ch.action.replace("would-", ""), "white"
            )
            if ch.action in ("create", "update", "would-create", "would-update"):
                any_creates = True
            console.print(f"  {tag}[{color}]{ch.action:<14}[/{color}] {ch.path}  {ch.reason}")

        for err in ir.errors:
            console.print(f"  [red]error:[/red] {err}")

        if ir.success and not ir.errors:
            verb = "would " if dry_run else ""
            kind = "uninstall" if uninstall else "install"
            console.print(
                f"  [green]✓[/green] {verb}{kind} complete — {len(ir.changes)} change(s)"
            )

    # PATH integration step (opt-in via --link-path). On uninstall, removes
    # any managed block left behind. Adversarial bug-hunter B1 — pip --user
    # often puts the `sio` binary at ~/.local/bin which isn't on the PATH
    # used by non-login subprocesses (Claude Code's Bash tool, for one).
    if link_path or uninstall:
        from sio.harnesses.path_link import link_path as do_link
        from sio.harnesses.path_link import unlink_path

        if uninstall:
            pl = unlink_path(dry_run=dry_run)
        else:
            pl = do_link(dry_run=dry_run)
        console.print(
            f"\n[bold blue]→ shell PATH integration[/bold blue]  ({pl.rc_file})"
        )
        color = {
            "create": "green",
            "would-create": "green",
            "remove": "red",
            "would-remove": "red",
            "skip": "white",
            "skip-not-managed": "dim",
        }.get(pl.action, "white")
        console.print(f"  [{color}]{pl.action:<14}[/{color}] {pl.detail}")
        for n in pl.notes:
            console.print(f"  [dim]· {n}[/dim]")

    # Risk #2 fix: Claude Code reads ~/.claude/skills/ at process start —
    # newly staged SKILL.md files don't appear as slash commands until the
    # user restarts. Surface this clearly so a successful install doesn't
    # look broken to anyone running `/sio` immediately after.
    if not status and not dry_run and not uninstall and any_creates:
        console.print(
            "\n[bold yellow]→ Restart your AI coding agent[/bold yellow] "
            "for newly-staged skills to appear (Claude Code only reads the "
            "skills dir at startup)."
        )
        console.print(
            "  [dim]Also: open ~/.sio/config.toml and uncomment one [llm] "
            "block before running `sio suggest`.[/dim]"
        )


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
@runlogged("health")
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
@runlogged("review")
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
@runlogged("optimize")
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
@runlogged("doctor")
def doctor() -> None:
    """Diagnose `sio` install / config problems.

    Runs a battery of checks (Python version, package collision, PATH
    visibility, ~/.sio/ data dir, config.toml, bundled bootstrap content,
    harness install state) and prints a color-coded report. Each problem
    detected comes with a one-line fix command. Exits 0 if everything is
    OK, 1 if any errors were found.
    """
    from rich.console import Console
    from rich.table import Table

    from sio.cli.doctor import run_doctor

    console = Console()
    report = run_doctor()

    table = Table(title="sio doctor — diagnostics", show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    color_map = {"ok": "green", "warn": "yellow", "error": "red"}
    glyph_map = {"ok": "✓", "warn": "!", "error": "✗"}
    for c in report.checks:
        color = color_map[c.status]
        glyph = glyph_map[c.status]
        table.add_row(c.name, f"[{color}]{glyph} {c.status}[/{color}]", c.detail)
    console.print(table)

    fixes = [c for c in report.checks if c.fix_hint]
    if fixes:
        console.print("\n[bold]Suggested fixes:[/bold]")
        for c in fixes:
            console.print(f"  [{color_map[c.status]}]{c.name}[/{color_map[c.status]}]: {c.fix_hint}")

    if report.has_errors:
        raise SystemExit(1)


@cli.command()
@runlogged("install")
def install():
    """[REMOVED] Use `sio init` instead.

    The legacy `sio install` path silently no-op'd on wheel installs because
    it read skill files from a directory that wasn't packaged into the
    wheel. Removed in v0.1.2 to eliminate that failure mode entirely.
    """
    raise click.ClickException(
        "`sio install` was removed in v0.1.2. Use `sio init` — see `sio init --help`. "
        "If you ran `sio install` previously and saw 'success' but no skills appeared, "
        "that's the bug v0.1.2 fixes. Run `sio init` now to actually stage the skills."
    )


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
@runlogged("purge")
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
@runlogged("export")
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
@runlogged("mine")
def mine(since, project, source, exclude_sidechains):
    """Mine recent sessions for errors and failures."""
    from pathlib import Path

    from sio.mining.pipeline import run_mine

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("flows")
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("distill")
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
@runlogged("recall")
def recall(query, session, project, polish, output):
    """Recall how a specific task was solved in a previous session.

    Topic-filters a distilled session to only the steps matching your query,
    detects struggle→fix transitions, and optionally polishes via Gemini.

    Examples:
        sio recall "dbt setup"                    # Cheap: filter + format
        sio recall "dbt setup" --polish            # Expensive: + Gemini runbook
        sio recall "auth fix" --project my-app     # Filter by project
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
@runlogged("patterns")
def patterns(error_type, project):
    """Show discovered error patterns ranked by importance."""
    from rich.console import Console
    from rich.table import Table

    from sio.clustering.pattern_clusterer import cluster_errors
    from sio.clustering.ranker import rank_patterns
    from sio.core.db.queries import get_error_records

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("errors")
def errors(error_type, limit, grep_term, project, exclude_types):
    """Browse mined errors with optional type and content filters."""
    from rich.console import Console
    from rich.table import Table

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
        db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@click.option(
    "--refine",
    "refine_term",
    default=None,
    help=(
        "Hop-2 refinement: narrow Hop-1's (--grep) error set by a second AND-filter. "
        "Comma-separated terms use OR logic within Hop-2, AND-composed with Hop-1. "
        "See --strategy for how narrowing is applied."
    ),
)
@click.option(
    "--strategy",
    "hop2_strategy",
    type=click.Choice(["filter", "recluster", "hybrid"], case_sensitive=False),
    default="filter",
    help=(
        "Hop-2 narrowing strategy (used with --refine). "
        "'filter' (default): narrow errors by --refine, feed subset to DSPy. Fast, shallow. "
        "'recluster': re-cluster Hop-1's errors and select sub-clusters matching --refine. Slower, deep. "
        "'hybrid': filter by --refine, then re-cluster the survivors. Balance."
    ),
)
@click.option(
    "--recluster-threshold",
    "recluster_threshold",
    type=click.FloatRange(0.50, 0.99, clamp=True),
    default=0.85,
    show_default=True,
    help=(
        "Cosine-similarity threshold for the second clustering pass under "
        "--strategy recluster|hybrid. Higher = tighter sub-clusters. "
        "First pass uses 0.70; recluster uses 0.85 by default since the "
        "Hop-1 error set is already theme-coherent."
    ),
)
@click.option(
    "--within",
    "within_csv",
    default=None,
    help=(
        "Path to a Hop-1 errors CSV (from a previous --preview run). "
        "Skips DB load + --grep / --project / --type filters (those were applied in Hop-1). "
        "Feeds the cached errors directly into clustering + Hop-2. "
        "Use '~/.sio/previews/errors_preview.csv' (latest preview) by default if --use-cache is set."
    ),
)
@click.option(
    "--use-cache",
    "use_cache",
    is_flag=True,
    default=False,
    help=(
        "Use the most recent Hop-1 preview CSV at ~/.sio/previews/errors_preview.csv. "
        "Warns if the cache is older than --cache-ttl hours (default 24)."
    ),
)
@click.option(
    "--cache-ttl",
    "cache_ttl_hours",
    type=int,
    default=24,
    help="Max age in hours for --use-cache to accept without warning. Default: 24.",
)
@runlogged("suggest")
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
    refine_term,
    hop2_strategy,
    recluster_threshold,
    within_csv,
    use_cache,
    cache_ttl_hours,
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        console = Console()

        # Generate a new cycle_id for this suggest run (FR-003, data-model.md §2.8)
        cycle_id = str(uuid.uuid4())

        # --- Hop-1 cache shortcut (T4) -----------------------------------
        # When --within or --use-cache is set, skip the DB load + Hop-1 filters
        # entirely. The CSV is the frozen output of a prior Hop-1 run.
        csv_path = within_csv
        if not csv_path and use_cache:
            csv_path = os.path.expanduser("~/.sio/previews/errors_preview.csv")

        if csv_path:
            import csv as _csv
            from datetime import datetime as _dt, timezone as _tz

            csv_abs = os.path.expanduser(csv_path)
            if not os.path.exists(csv_abs):
                click.echo(
                    f"--within CSV not found: {csv_abs}\n"
                    f"Run 'sio suggest ... --preview' first to generate it."
                )
                return

            # TTL check
            age_hours = (time.time() - os.path.getmtime(csv_abs)) / 3600.0
            if age_hours > cache_ttl_hours:
                console.print(
                    f"[yellow]⚠ Hop-1 cache is {age_hours:.1f}h old (>{cache_ttl_hours}h TTL). "
                    f"Data may be stale. Consider re-running --preview.[/yellow]"
                )
            else:
                console.print(
                    f"[dim]Using Hop-1 cache from {csv_abs} "
                    f"({age_hours:.1f}h old, TTL={cache_ttl_hours}h)[/dim]"
                )

            loaded_errors: list[dict] = []
            with open(csv_abs, newline="") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    loaded_errors.append(
                        {
                            "id": int(row["id"]) if row.get("id", "").isdigit() else row.get("id"),
                            "error_type": row.get("error_type") or "",
                            "error_text": row.get("error_text") or "",
                            "tool_name": row.get("tool_name") or "",
                            "session_id": row.get("session_id") or "",
                            "timestamp": row.get("timestamp") or "",
                            "source_file": row.get("source_file") or "",
                            "user_message": row.get("user_message") or "",
                            # fields truncated in CSV but still sufficient for Hop-2 filtering
                            "context_before": "",
                            "context_after": "",
                        }
                    )

            if not loaded_errors:
                click.echo(f"--within CSV is empty: {csv_abs}")
                return

            errors_to_cluster = loaded_errors
            all_errors = loaded_errors  # so downstream doesn't mis-reference
            console.print(
                f"[dim]Hop-1 cache loaded: {len(loaded_errors)} errors "
                f"(skipping DB query + --grep/--project/--type Hop-1 filters)[/dim]"
            )
        else:
            # Normal path — load from DB and apply Hop-1 filters
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

        # ---------------------------------------------------------------
        # Hop-2 refinement (PRD: sio_multi_hop_search_2026-04-24)
        # When --refine is set, narrow Hop-1's result set by a second AND-filter
        # before or after clustering, per --strategy. 'filter' (default) narrows
        # the error set pre-cluster. 'recluster' defers narrowing to post-cluster
        # (sub-cluster selection). 'hybrid' does both.
        # ---------------------------------------------------------------
        hop2_refine_terms: list = []
        if refine_term:
            hop2_refine_terms = [t.strip().lower() for t in refine_term.split(",") if t.strip()]

        def _hop2_matches(e: dict) -> bool:
            if not hop2_refine_terms:
                return True
            searchable = (
                "error_text",
                "user_message",
                "context_before",
                "context_after",
                "source_file",
            )
            for field in searchable:
                val = (e.get(field) or "").lower()
                for term in hop2_refine_terms:
                    if term in val:
                        return True
            return False

        hop1_error_count = len(errors_to_cluster)
        if hop2_refine_terms and hop2_strategy.lower() in ("filter", "hybrid"):
            # Pre-cluster narrowing — shrinks the set that clustering sees
            errors_to_cluster = [e for e in errors_to_cluster if _hop2_matches(e)]

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
        if hop2_refine_terms:
            filter_msg += (
                f" | Hop-2 strategy={hop2_strategy.lower()} refine='{refine_term}'"
                f" ({hop1_error_count} -> {len(errors_to_cluster)} errors after pre-cluster narrowing)"
            )
        console.print(
            f"[bold]Step 1:[/bold] Clustering {len(errors_to_cluster)} errors{filter_msg}..."
        )

        # 2. Cluster and rank
        clustered = cluster_errors(errors_to_cluster)
        ranked = rank_patterns(clustered)
        console.print(f"  Found {len(ranked)} patterns")

        # ---------------------------------------------------------------
        # Hop-2 sub-cluster decomposition (PRD: sio_multi_hop_search_2026-04-24,
        # graduated L003; resolves drift documented in sio_ship_pickup B7
        # and implemented in sio_v0_1_4_scope_2026-05-11).
        #
        # For 'recluster' and 'hybrid' the original L003 design promises
        # *re-clustering* the Hop-1 set with tighter params — not just
        # post-filtering patterns. Prior to v0.1.4 this block sub-selected
        # patterns by description match, which collapsed `recluster` into
        # "stricter filter" and made `hybrid` meaningless.
        #
        # Implementation:
        #   1. Identify patterns whose description / sample errors match
        #      any refine term (theme-coherent Hop-1 candidates).
        #   2. Collect their underlying error set.
        #   3. Re-invoke cluster_errors() on that set with a tighter
        #      similarity threshold (--recluster-threshold, default 0.85
        #      vs 0.70 on first pass).
        #   4. Re-rank → these are the actual sub-clusters.
        #   5. Fall back to plain pattern-filtering when the matching set is
        #      too small to re-cluster meaningfully (< 2 errors).
        # ---------------------------------------------------------------
        if hop2_refine_terms and hop2_strategy.lower() in ("recluster", "hybrid"):
            error_index = {e.get("id"): e for e in errors_to_cluster}

            def _pattern_matches_hop2(p: dict) -> bool:
                desc = (p.get("description") or "").lower()
                for term in hop2_refine_terms:
                    if term in desc:
                        return True
                for eid in p.get("error_ids", []):
                    e = error_index.get(eid)
                    if e and _hop2_matches(e):
                        return True
                return False

            pre_count = len(ranked)
            matching_patterns = [p for p in ranked if _pattern_matches_hop2(p)]

            matching_eids: set = set()
            for p in matching_patterns:
                for eid in p.get("error_ids", []):
                    matching_eids.add(eid)
            matching_errors = [
                error_index[eid] for eid in matching_eids if eid in error_index
            ]

            if len(matching_errors) < 2:
                ranked = matching_patterns
                console.print(
                    f"  Hop-2 recluster fallback: only {len(matching_errors)}"
                    f" theme-coherent error(s); using pattern-filter behavior."
                    f" {pre_count} -> {len(ranked)} patterns."
                )
            else:
                sub_clustered = cluster_errors(
                    matching_errors, threshold=recluster_threshold
                )
                ranked = rank_patterns(sub_clustered)
                console.print(
                    f"  Hop-2 sub-cluster decomposition: {pre_count} patterns -> "
                    f"{len(matching_errors)} theme-coherent errors -> "
                    f"{len(ranked)} sub-cluster(s)"
                    f" (threshold={recluster_threshold}, strategy={hop2_strategy.lower()})"
                )

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
            metadata = build_dataset(
                p, all_errors, conn, min_threshold=min_examples, cycle_id=cycle_id
            )
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
@runlogged("suggest-review")
def suggest_review():
    """Review pending improvement suggestions interactively."""
    from rich.console import Console
    from rich.table import Table

    from sio.review.reviewer import approve as do_approve
    from sio.review.reviewer import defer as do_defer
    from sio.review.reviewer import reject as do_reject
    from sio.review.reviewer import review_pending

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("approve")
def approve(suggestion_id, note):
    """Approve a suggestion by ID and promote to ground truth."""
    from sio.ground_truth.corpus import promote_to_ground_truth
    from sio.review.reviewer import approve as do_approve

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("reject")
def reject(suggestion_id, note):
    """Reject a suggestion by ID."""
    from sio.review.reviewer import reject as do_reject

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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


@cli.command(name="promote-to-gold")
@click.argument("invocation_id", type=int, required=False, default=None)
@click.option(
    "--all-eligible",
    is_flag=True,
    default=False,
    help="Bulk-promote ALL invocations with user_satisfied=1 AND correct_outcome=1.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be promoted without writing.",
)
@runlogged("promote-to-gold")
def promote_to_gold_cmd(invocation_id, all_eligible, dry_run):
    """Promote behavior_invocations to gold_standards for DSPy training.

    A row is eligible when user_satisfied=1 AND correct_outcome=1.
    Use --all-eligible to bulk-promote, or pass an INVOCATION_ID for one row.
    """
    from sio.core.arena.gold_standards import promote_to_gold as do_promote

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found.")
        raise SystemExit(1)

    if invocation_id is None and not all_eligible:
        click.echo(
            "Usage: sio promote-to-gold <INVOCATION_ID> | --all-eligible "
            "[--dry-run]"
        )
        raise SystemExit(1)

    with _db_conn(db_path) as conn:
        if all_eligible:
            rows = conn.execute(
                "SELECT id FROM behavior_invocations "
                "WHERE user_satisfied=1 AND correct_outcome=1"
            ).fetchall()
            if not rows:
                click.echo(
                    "No eligible invocations found "
                    "(need user_satisfied=1 AND correct_outcome=1)."
                )
                click.echo(
                    "Hint: invocations may not have been labeled yet. "
                    "See `sio review` to label them."
                )
                raise SystemExit(1)
            if dry_run:
                click.echo(
                    f"DRY RUN — would attempt to promote {len(rows)} "
                    "eligible invocations."
                )
                return
            promoted = 0
            skipped = 0
            for row in rows:
                gid = do_promote(row["id"], db_path=conn)
                if gid:
                    promoted += 1
                else:
                    skipped += 1
            click.echo(
                f"Promoted: {promoted}  Skipped "
                f"(already in gold_standards): {skipped}"
            )
        else:
            if dry_run:
                inv = conn.execute(
                    "SELECT id, user_satisfied, correct_outcome "
                    "FROM behavior_invocations WHERE id=?",
                    (invocation_id,),
                ).fetchone()
                if inv is None:
                    click.echo(f"Invocation {invocation_id} not found.")
                    raise SystemExit(1)
                eligible = (
                    inv["user_satisfied"] == 1 and inv["correct_outcome"] == 1
                )
                click.echo(
                    f"DRY RUN — invocation {invocation_id}: "
                    f"user_satisfied={inv['user_satisfied']} "
                    f"correct_outcome={inv['correct_outcome']} "
                    f"eligible={eligible}"
                )
                return
            gid = do_promote(invocation_id, db_path=conn)
            if gid:
                click.echo(
                    f"Invocation {invocation_id} promoted to "
                    f"gold_standards (ID: {gid})."
                )
            else:
                click.echo(
                    f"Invocation {invocation_id} NOT promoted. "
                    "Needs user_satisfied=1 AND correct_outcome=1, "
                    "or may already be promoted."
                )
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
@click.option(
    "--auto-threshold",
    type=float,
    default=None,
    help=(
        "BULK MODE: auto-apply ALL pending suggestions with confidence >= "
        "this threshold. Cannot combine with a positional SUGGESTION_ID. "
        "Recommended: 0.9 for conservative auto-apply."
    ),
)
@click.option(
    "--skip-dupes/--no-skip-dupes",
    default=True,
    show_default=True,
    help=(
        "(With --auto-threshold) skip suggestions whose target rule "
        "duplicates an existing one in ~/.claude/rules/. Reuses sio dedupe "
        "logic at threshold 0.85."
    ),
)
@runlogged("apply")
def apply_suggestion(
    suggestion_id, experiment, force, rollback_id, merge, yes, no_backup,
    auto_threshold, skip_dupes,
):
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

    # BULK MODE: --auto-threshold N applies ALL pending suggestions
    # whose confidence ≥ N. Optional --skip-dupes filters out suggestions
    # whose proposed rule duplicates an existing one. T2.A from PRD
    # sio_backend_dead_loop_2026-05-15.
    if auto_threshold is not None:
        if suggestion_id is not None:
            click.echo(
                "Error: --auto-threshold cannot combine with a positional "
                "SUGGESTION_ID. Pass either one suggestion OR --auto-threshold.",
                err=True,
            )
            raise SystemExit(1)
        if rollback_id is not None:
            click.echo("Error: --auto-threshold cannot combine with --rollback.",
                       err=True)
            raise SystemExit(1)

        db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
        if not os.path.exists(db_path):
            click.echo("No database found.")
            raise SystemExit(1)

        with _db_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT id, confidence FROM suggestions "
                "WHERE status='pending' AND confidence >= ? "
                "ORDER BY confidence DESC",
                (auto_threshold,),
            ).fetchall()

        if not rows:
            click.echo(
                f"No pending suggestions with confidence >= {auto_threshold}."
            )
            raise SystemExit(0)

        click.echo(
            f"Bulk-apply: {len(rows)} pending suggestions with "
            f"confidence >= {auto_threshold:.2f}"
        )
        if not yes:
            if not click.confirm(
                f"Apply all {len(rows)} suggestions? "
                f"(skip-dupes={skip_dupes})"
            ):
                click.echo("Cancelled.")
                raise SystemExit(0)

        from sio.core.applier.writer import apply_suggestion as do_apply  # noqa: PLC0415

        applied = 0
        skipped_dupes = 0
        failed = 0
        for r in rows:
            sid = r["id"] if hasattr(r, "keys") else r[0]
            try:
                # When skip_dupes is on, the writer's built-in similarity
                # check (>80% match → merge or skip) gives us free duplicate
                # filtering without a second pass.
                ok = do_apply(
                    suggestion_id=sid,
                    db_path=db_path,
                    experiment_branch=experiment,
                    force=force,
                    consent_merge=False if skip_dupes else merge,
                )
                if ok:
                    applied += 1
                else:
                    skipped_dupes += 1
            except Exception as exc:  # noqa: BLE001
                click.echo(f"  failed id={sid}: {str(exc)[:120]}", err=True)
                failed += 1

        click.echo(
            f"Applied: {applied}  Skipped (dupes/merge-conflict): "
            f"{skipped_dupes}  Failed: {failed}"
        )
        raise SystemExit(0 if failed == 0 else 1)

    # Handle rollback path — does not require suggestion_id
    if rollback_id is not None:
        from sio.core.applier.writer import (  # noqa: PLC0415
            BackupMissingError,
            rollback_applied_change,
        )

        db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("rollback")
def rollback(change_id):
    """Rollback an applied change by ID."""
    from sio.applier.rollback import rollback_change

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("changes")
def changes():
    """List applied changes and their status."""
    from rich.console import Console
    from rich.table import Table

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
            ("OPENAI_API_KEY", "openai"),
            ("ANTHROPIC_API_KEY", "anthropic"),
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
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
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
        console.print("  export OPENAI_API_KEY=...")
        console.print("  export ANTHROPIC_API_KEY=...")
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
@runlogged("status")
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

    db_path_str = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
                    # Try newer column ``module_name`` first; fall back to
                    # the legacy ``module_type`` column that all current rows
                    # actually populate.
                    try:
                        row = conn.execute(
                            "SELECT module_name FROM optimized_modules "
                            "WHERE is_active = 1 "
                            "ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                    except Exception:
                        row = conn.execute(
                            "SELECT module_type FROM optimized_modules "
                            "WHERE is_active = 1 "
                            "ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                    training_data["active_module"] = (
                        row[0] if row else "none"
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
@runlogged("briefing")
def briefing(as_json):
    """Show a brief session-start briefing of actionable SIO insights."""
    from sio.core.config import load_config
    from sio.suggestions.consultant import build_session_briefing

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("optimize-suggestions")
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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


@cli.command("differential-flows")
@click.option("--min-success", default=3, show_default=True, type=int,
              help="Minimum successful events per flow_hash to qualify as a twin.")
@click.option("--min-failure", default=3, show_default=True, type=int,
              help="Minimum failed events per flow_hash to qualify as a twin.")
@click.option("--per-cohort", default=5, show_default=True, type=int,
              help="Samples drawn from each cohort (success / failure) per twin.")
@click.option("--max-hashes", default=None, type=int,
              help="Cap the number of twin-hashes processed (debug).")
@click.option("-o", "--output", default=None,
              help="Output JSONL path. Default: ~/.sio/differential/<ts>.jsonl.")
@click.option("--positives-for-builder", is_flag=True, default=False,
              help=(
                  "Instead of paired-cohort JSONL, emit FLAT positive examples "
                  "(one per successful sample) in the shape consumed by "
                  "src/sio/export/dataset_builder.py. Wires T1.V.3 — populates "
                  "the long-empty positive side of training datasets."
              ))
@runlogged("differential-flows")
def differential_flows_cmd(min_success, min_failure, per_cohort, max_hashes,
                           output, positives_for_builder):
    """Find twin flows (same sequence, both success and failure outcomes).

    Outputs paired success/failure samples by flow_hash. The differential
    is the cheapest training signal SIO can produce — no LLM call required.

    With --positives-for-builder: emit only successful rows in canonical
    PatternToRule shape so the existing dataset_builder can append them
    as positive examples (T1.V.3).

    Examples:
        sio differential-flows
        sio differential-flows --min-success 5 --per-cohort 10
        sio differential-flows --positives-for-builder
    """
    import datetime as _dt
    from pathlib import Path
    from sio.flows.differential import export_pairs, export_positives_for_dataset_builder

    if output is None:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        suffix = "_positives" if positives_for_builder else "_pairs"
        output = os.path.expanduser(f"~/.sio/differential/differential{suffix}_{ts}.jsonl")
    out_path = Path(output)

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if positives_for_builder:
        result = export_positives_for_dataset_builder(
            db_path, out_path,
            min_success=min_success, min_failure=min_failure,
            per_cohort=per_cohort,
        )
        click.echo(f"Twins:          {result['twins']}")
        click.echo(f"Positive rows:  {result['positive_rows_written']}")
        click.echo(f"Output:         {result['path']}")
        _rows_written = result.get("positive_rows_written", 0)
        _source = "differential-flows-positives"
    else:
        result = export_pairs(
            db_path, out_path,
            min_success=min_success, min_failure=min_failure,
            per_cohort=per_cohort, max_hashes=max_hashes,
        )
        click.echo(f"Twin hashes:   {result['twin_hashes']}")
        click.echo(f"Rows written:  {result['rows_written']}")
        click.echo(f"Output:        {result['path']}")
        _rows_written = result.get("rows_written", 0)
        _source = "differential-flows-pairs"

    # Principle XIII (observability) + XV-proposed (reproducibility): same
    # auto-register pattern as curate/amplify/promote-positives. Differential
    # flow outputs are training-grade artifacts (consumed by sio optimize
    # --trainset-file or by dataset_builder). Without content-hashing, an
    # optimize run that USED a specific differential JSONL has no DB lineage
    # back to the flow_events corpus that produced it. Closes Agent D top-3
    # gap #3 (2026-05-18 CLI audit). Failure isolated — does NOT delete the
    # JSONL the user already has on disk.
    if _rows_written > 0 and Path(result["path"]).exists():
        try:
            from sio.core.datasets import register_dataset  # noqa: PLC0415
            slug = Path(result["path"]).stem
            ds_id = register_dataset(
                source_path=Path(result["path"]),
                slug=slug,
                description=(
                    f"Differential flows ({_source}): "
                    f"min_success={min_success} min_failure={min_failure} "
                    f"per_cohort={per_cohort} rows={_rows_written}"
                ),
                source=_source,
            )
            click.echo(f"Dataset:       registered as trainset id={ds_id} (slug={slug})")
        except Exception as exc:  # noqa: BLE001
            click.echo(
                f"\nWARNING: differential-flows JSONL saved but trainset "
                f"registration failed: {exc}. Run 'sio reproduce <id>' to verify."
            )


@cli.group("analyze")
def analyze_group():
    """Read-only diagnostics over the mined corpus."""


@analyze_group.command("same-error")
@click.option("--min-count", default=3, show_default=True, type=int,
              help="Minimum repetition count to surface.")
@click.option("--since", default=None,
              help='Time window, e.g. "30 days", "1 week".')
@click.option("--limit", default=30, show_default=True, type=int,
              help="Max findings to display.")
@click.option("--with-context", is_flag=True, default=False,
              help="Include up to 3 context_before snippets per finding.")
@runlogged("analyze-same-error")
def analyze_same_error_cmd(min_count, since, limit, with_context):
    """Find error signatures repeated >= N times across sessions.

    The unit of analysis is the normalised error_text signature_hash —
    same hash space as sio.clustering.classifier. Surfaces the cognitive
    failure modes: the same error hitting the agent N times implies the
    agent failed to learn from each occurrence.

    Examples:
        sio analyze same-error
        sio analyze same-error --min-count 5 --since "7 days"
        sio analyze same-error --with-context  # include agent intent
    """
    from sio.analyze import same_error_analysis

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found.")
        raise SystemExit(1)

    findings = same_error_analysis(
        db_path=db_path,
        min_count=min_count,
        since=since,
        limit=limit,
        with_context=with_context,
    )

    if not findings:
        click.echo(f"No error signatures repeated >= {min_count} times.")
        return

    click.echo(f"Found {len(findings)} signatures repeated >= {min_count} times.")
    click.echo("")
    for i, f in enumerate(findings, 1):
        click.echo(f"## {i}. {f['signature_hash']}  count={f['count']}  "
                   f"sessions={f['session_count']}")
        click.echo(f"     tools: {f['tools']}")
        click.echo(f"     types: {f['error_types']}")
        click.echo(f"     first: {f['first_seen'][:19]}  last: {f['last_seen'][:19]}")
        click.echo(f"     sample: {f['sample_error']!r}")
        if with_context and f["contexts"]:
            for c in f["contexts"]:
                click.echo(f"     context: {c!r}")
        click.echo("")


@cli.command("curate")
@click.option("--since", default="7 days", show_default=True,
              help='Time window: "7 days", "30 days", or ISO date.')
@click.option("--emphasis", is_flag=True, default=False,
              help='Require !! or ?? in user_message (frustration markers).')
@click.option("--classified", is_flag=True, default=False,
              help='Require pattern_id NOT NULL (skip unclassified records).')
@click.option("--pattern", default=None,
              help='Exact pattern_id slug to filter on.')
@click.option("--pattern-prefix", default=None,
              help='LIKE prefix for pattern_id (e.g. tool_failure__).')
@click.option("--error-type", "error_types", multiple=True,
              help='Restrict to error_type(s). Repeat flag for multiple.')
@click.option("--exclude-corrections/--include-corrections", default=True,
              show_default=True, help='Drop user_correction rows.')
@click.option("--exclude-cascade/--include-cascade", default=True,
              show_default=True, help='Drop cascade-failure rows.')
@click.option("--has-positive-recovery", is_flag=True, default=False,
              help='Require a positive_records event within --recovery-window-seconds.')
@click.option("--recovery-window-seconds", default=600, show_default=True, type=int)
@click.option("--limit", default=None, type=int,
              help='Max rows to emit (DESC by timestamp; newest first).')
@click.option("-o", "--output", default=None,
              help='Output JSONL path. Defaults to ~/.sio/curated/<timestamp>.jsonl.')
@runlogged("curate")
def curate_cmd(
    since, emphasis, classified, pattern, pattern_prefix, error_types,
    exclude_corrections, exclude_cascade, has_positive_recovery,
    recovery_window_seconds, limit, output,
):
    """Produce a curated training dataset (JSONL + preview .md).

    Wraps the filter chain in ``sio.curate``. Outputs a JSONL of canonical
    PatternToRule dspy.Example shapes plus a Markdown preview with row
    count, category distribution, and 10 sample rows.

    The curated file is consumed by ``sio optimize --trainset-file <path>``.
    """
    import datetime as _dt  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from sio.curate import CurateFilters, curate  # noqa: PLC0415

    filters = CurateFilters(
        since=since,
        emphasis=emphasis,
        classified=classified,
        pattern=pattern,
        pattern_prefix=pattern_prefix,
        error_types=tuple(error_types),
        exclude_corrections=exclude_corrections,
        exclude_cascade=exclude_cascade,
        has_positive_recovery=has_positive_recovery,
        recovery_window_seconds=recovery_window_seconds,
        limit=limit,
    )

    if output is None:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        output = os.path.expanduser(f"~/.sio/curated/curated_{ts}.jsonl")

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    out = curate(db_path, filters, Path(output))
    click.echo(f"Rows:    {out['rows']}")
    click.echo(f"JSONL:   {out['jsonl_path']}")
    click.echo(f"Preview: {out['preview_path']}")
    if out["rows"] == 0:
        click.echo(
            "\nWARNING: 0 rows — filters too tight. "
            "Try widening --since or removing --emphasis / --has-positive-recovery."
        )
        raise SystemExit(1)

    # Principle XIII (observability) + Principle XV-proposed (reproducibility):
    # auto-register every curate output in trainsets so the dataset is
    # content-addressable, permanently stored, and joinable to optimized_modules.
    # Idempotent — re-running curate with same filters returns the existing id.
    try:
        from sio.core.datasets import register_dataset  # noqa: PLC0415
        slug = Path(output).stem  # e.g. "curated_20260517_124102"
        ds_id = register_dataset(
            source_path=Path(out["jsonl_path"]),
            slug=slug,
            description=(
                f"Curate output: since={filters.since!r} emphasis={filters.emphasis!r} "
                f"rows={out['rows']}"
            ),
            source="curate",
        )
        click.echo(f"Dataset: registered as trainset id={ds_id} (slug={slug})")
    except Exception as exc:  # noqa: BLE001
        # Registration failure must NOT break the curate output the user
        # already has on disk. Log loudly and continue.
        click.echo(f"\nWARNING: curate output saved but trainset registration "
                   f"failed: {exc}. Run 'sio reproduce <module_id>' to verify.")


@cli.command("promote-positives")
@click.option("--since", default="7 days", show_default=True,
              help="Time window of positive_records to consider.")
@click.option("--min-confidence", default=0.0, show_default=True, type=float,
              help="Drop positives with sentiment_score below this.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be promoted without writing.")
@runlogged("promote-positives")
def promote_positives_cmd(since, min_confidence, dry_run):
    """Promote positive_records to ground_truth(label='pending').

    Wires up the 1,702-row positive_records table (built but never joined
    into trainsets) so that confirmations/gratitude/session_success events
    flow into the review queue. From there ``sio approve`` lifts them to
    label='positive' and they enter the next ``sio optimize`` trainset.

    Bridges the session_id schema gap (error_records uses bare UUIDs,
    positive_records uses ``<path>:<hash>``) via the shared source_file.
    """
    from datetime import datetime, timezone, timedelta  # noqa: PLC0415

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found.")
        raise SystemExit(1)

    # Resolve --since
    n_str, _, unit = since.partition(" ")
    delta = (
        timedelta(days=int(n_str))
        if unit.startswith("day")
        else timedelta(hours=int(n_str))
        if unit.startswith("hour")
        else timedelta(days=7)
    )
    cutoff = (datetime.now(timezone.utc) - delta).isoformat()

    with _db_conn(db_path) as conn:
        # Find positive_records with a preceding error_records row in the
        # same source_file (the bridge), within a recovery window.
        rows = conn.execute(
            """
            SELECT p.id AS pos_id, p.source_file, p.timestamp AS pos_ts,
                   p.signal_type, p.signal_text, p.sentiment_score,
                   er.id AS err_id, er.error_type, er.error_text,
                   er.pattern_id, er.tool_name
            FROM positive_records p
            JOIN error_records er ON er.source_file = p.source_file
            WHERE p.timestamp >= ?
              AND p.signal_type IN ('confirmation','session_success','implicit_approval')
              AND COALESCE(p.sentiment_score, 1.0) >= ?
              AND er.timestamp < p.timestamp
              AND (julianday(p.timestamp) - julianday(er.timestamp))*86400 < 600
              AND NOT EXISTS (SELECT 1 FROM ground_truth gt
                              WHERE gt.pattern_id = er.pattern_id
                                AND gt.label = 'pending'
                                AND gt.source = 'positive_record')
            ORDER BY p.timestamp DESC
            """,
            (cutoff, min_confidence),
        ).fetchall()

        if not rows:
            click.echo(
                "No new positive_record→error_record pairs match. "
                "Try widening --since or lowering --min-confidence."
            )
            raise SystemExit(1)

        click.echo(f"Found {len(rows)} (positive, error) pairs to promote.")
        if dry_run:
            for r in rows[:10]:
                click.echo(
                    f"  [{r['signal_type']}] {r['pattern_id']} <- "
                    f"{(r['signal_text'] or '')[:60]}"
                )
            click.echo(f"  ... (+{max(0,len(rows)-10)} more)")
            click.echo("DRY RUN — nothing written.")
            return

        # Insert into ground_truth as label='pending'
        from datetime import datetime as _dt  # noqa: PLC0415
        now = _dt.now(timezone.utc).isoformat()
        inserted = 0
        promoted_records = []  # captured for the trainsets snapshot below
        for r in rows:
            try:
                conn.execute(
                    """
                    INSERT INTO ground_truth
                        (pattern_id, error_examples_json, error_type,
                         pattern_summary, target_surface, rule_title,
                         prevention_instructions, rationale, label, source,
                         created_at)
                    VALUES (?, ?, ?, ?, 'claude_md_rule', ?, '', '', 'pending',
                            'positive_record', ?)
                    """,
                    (
                        r["pattern_id"] or "tool_failure__unclassified",
                        '[{"error_text": "' + (r["error_text"] or "").replace('"', '\\"')[:300] + '"}]',
                        r["error_type"],
                        f"User-confirmed recovery: {(r['signal_text'] or '')[:200]}",
                        f"Recovery-paired rule for {r['pattern_id'] or 'unclassified'}",
                        now,
                    ),
                )
                promoted_records.append({
                    "pattern_id": r["pattern_id"] or "tool_failure__unclassified",
                    "error_type": r["error_type"],
                    "error_text": (r["error_text"] or "")[:300],
                    "signal_type": r["signal_type"],
                    "signal_text": (r["signal_text"] or "")[:200],
                    "source_file": r["source_file"],
                    "promoted_at": now,
                    "_meta": {
                        "positive_record_id": r["pos_id"],
                        "error_record_id": r["err_id"],
                    },
                })
                inserted += 1
            except Exception as exc:  # noqa: BLE001
                click.echo(f"  skipped one: {exc}", err=True)
        conn.commit()
        click.echo(f"Inserted {inserted} new ground_truth rows (label='pending').")

        # Principle XIII (observability) + Principle XV-proposed (reproducibility):
        # snapshot the promoted batch as a JSONL trainset row. Without this, a
        # ground_truth row's lineage stops at the (pattern_id, source) pair —
        # there's no way to ask "what was the promotion batch on 2026-05-18?"
        # The snapshot file + content-hash close the audit chain so a future
        # `sio reproduce` can walk: optimize → ground_truth slice → promotion
        # JSONL → original positive_records ids.
        if promoted_records:
            try:
                import json as _json  # noqa: PLC0415
                from pathlib import Path as _P  # noqa: PLC0415
                from sio.core.datasets import register_dataset  # noqa: PLC0415
                _ts_safe = now.replace(":", "-").replace("+", "_").replace(".", "_")
                snapshot_dir = _P.home() / ".sio" / "promoted"
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot_path = snapshot_dir / f"promote_positives_{_ts_safe}.jsonl"
                with snapshot_path.open("w") as f:
                    for rec in promoted_records:
                        f.write(_json.dumps(rec) + "\n")
                ds_id = register_dataset(
                    source_path=snapshot_path,
                    slug=f"promote_positives_{_ts_safe}",
                    description=(
                        f"Promotion batch: {inserted} positive_records → "
                        f"ground_truth(label='pending'). Filters: "
                        f"since={since!r} min_confidence={min_confidence}"
                    ),
                    source="promote-positives",
                )
                click.echo(
                    f"Snapshot: registered as trainset id={ds_id} "
                    f"({snapshot_path})"
                )
            except Exception as exc:  # noqa: BLE001
                click.echo(
                    f"WARNING: promotion succeeded but snapshot registration "
                    f"failed: {exc}. ground_truth rows are still in place; "
                    f"only the audit-trail snapshot is missing.",
                    err=True,
                )

        click.echo("Run `sio suggest-review` or `sio approve <id>` to promote them.")


@cli.command("amplify")
@click.option("-i", "--input", "input_path", required=True,
              help="Input JSONL produced by `sio curate`.")
@click.option("-o", "--output", "output_path", default=None,
              help="Output JSONL path. Defaults to ~/.sio/amplified/<input>_amplified.jsonl.")
@click.option("-n", "--n-per-row", default=10, show_default=True, type=int,
              help="Synthetic variants to generate per input row.")
@click.option("--min-judge-score", default=0.6, show_default=True, type=float,
              help="Drop variants whose LLM-judge score is below this.")
@click.option("--max-workers", default=8, show_default=True, type=int,
              help="Thread-pool parallelism for LLM calls.")
@click.option("--task-mode",
              type=click.Choice(["work", "cheap", "free", "personal", "personal-strong"]),
              default=None,
              help=(
                  "LM tier for amplification generation. Defaults to whatever "
                  "[llm.task] in ~/.sio/config.toml resolves to. "
                  "cheap=Flash (recommended), work=Pro, free=Ollama, "
                  "personal=gpt-4o-mini, personal-strong=gpt-5."
              ))
@click.option("--budget-override", type=float, default=None,
              help="Override 24h spend cap for this invocation (XII clause 6).")
@click.option("--no-diversity-filter", is_flag=True, default=False,
              help="Disable cosine-similarity de-duplication of variants "
                   "(Step 4 of 2026-05-18 paired-debate). Default: ENABLED.")
@click.option("--diversity-threshold", default=0.95, show_default=True, type=float,
              help="Cosine similarity above which variants from the same "
                   "source row are deduplicated. Lower = more aggressive.")
@runlogged("amplify")
def amplify_cmd(input_path, output_path, n_per_row, min_judge_score, max_workers,
                task_mode, budget_override, no_diversity_filter, diversity_threshold):
    """Amplify a curated JSONL by synthesizing N variants per row.

    Each input row is passed through Gemini Flash with a "preserve the
    category" prompt to generate variants that vary surface features
    (paths, tool names, phrasing) while keeping the same pattern_id.
    An LLM-as-judge filter drops variants that drift to a different
    category.

    Output is a JSONL that includes the originals AND the synthesized
    variants — can be consumed directly by ``sio optimize --trainset-file``.
    """
    from pathlib import Path  # noqa: PLC0415
    from sio.amplify import amplify  # noqa: PLC0415

    inp = Path(input_path).expanduser()
    if not inp.exists():
        click.echo(f"Input not found: {inp}", err=True)
        raise SystemExit(1)

    if output_path is None:
        output_path = os.path.expanduser(
            f"~/.sio/amplified/{inp.stem}_amplified.jsonl"
        )
    out = Path(output_path)

    # XII clauses 3 + 6: tier selection + budget guard
    if task_mode:
        _MODE = {
            "work":            "gemini/gemini-pro-latest",
            "cheap":           "gemini/gemini-flash-latest",
            "free":            "ollama_chat/qwen3-coder:30b",
            "personal":        "openai/gpt-4o-mini",
            "personal-strong": "openai/gpt-5",
        }
        os.environ["SIO_TASK_LM"] = _MODE[task_mode]
        click.echo(f"  --task-mode={task_mode} → SIO_TASK_LM={os.environ['SIO_TASK_LM']}")
    from sio.core.cost import BudgetExceeded, check_budget  # noqa: PLC0415
    try:
        bstate = check_budget(override_usd=budget_override)
        click.echo(
            f"  budget: ${bstate['spend_24h_usd']:.2f}/24h used "
            f"of ${bstate['effective_cap_usd']:.2f} cap"
        )
    except BudgetExceeded as exc:
        click.echo(f"BUDGET EXCEEDED: {exc}", err=True)
        raise SystemExit(1)

    rl = _runlog_current()
    with rl.stage("amplify") as s:
        result = amplify(
            input_path=inp,
            output_path=out,
            n_per_row=n_per_row,
            min_judge_score=min_judge_score,
            max_workers=max_workers,
            diversity_filter=(not no_diversity_filter),
            diversity_threshold=diversity_threshold,
        )
        # Each input row generates n_per_row candidate variants
        expected = result["input_rows"] * n_per_row
        kept = result["kept"]
        # rows_in = expected variants, rows_out = kept (triggers COVERAGE_DROP if low)
        s.set_rows(rows_in=expected, rows_out=kept)
        if result["dropped"] > 0:
            rl.warn(
                "JUDGE_DROPPED",
                f"{result['dropped']} of {result['total_generated']} variants "
                f"dropped by judge (threshold={min_judge_score})",
                stage="amplify",
            )

    rl.output("path", result["path"])
    rl.output("kept", kept)
    rl.output("dropped", result["dropped"])

    click.echo(f"\nInput rows:       {result['input_rows']}")
    click.echo(f"Total generated:  {result['total_generated']}")
    click.echo(f"Kept (judge≥{min_judge_score}): {result['kept']}")
    click.echo(f"Dropped:          {result['dropped']}")
    click.echo(f"Output:           {result['path']}")

    # Principle XIII (observability) + proposed XV (reproducibility): same
    # auto-registration as curate_cmd. amplify's output is a new dataset
    # derived FROM the input curate dataset — record the lineage via
    # parent_dataset_id so `sio reproduce <id>` can walk back to the source.
    # Idempotent (content-hash dedup before insert). Failure here MUST NOT
    # destroy the amplified file the user already has on disk.
    try:
        from sio.core.datasets import find_by_hash, hash_file, register_dataset  # noqa: PLC0415
        # Resolve the input dataset's id by sha lookup (if it was registered
        # via `sio curate -o`, it'll be there; if not, parent stays NULL).
        parent_id = None
        try:
            parent_row = find_by_hash(hash_file(inp))
            if parent_row:
                parent_id = parent_row["id"]
        except Exception:  # noqa: BLE001
            pass
        slug = Path(out).stem  # e.g. "curated_amplified"
        ds_id = register_dataset(
            source_path=out,
            slug=slug,
            description=(
                f"Amplify output: input={inp.name!r} n_per_row={n_per_row} "
                f"min_judge_score={min_judge_score} kept={kept} "
                f"task_mode={task_mode or '(default)'}"
            ),
            source="amplify",
            parent_dataset_id=parent_id,
        )
        click.echo(
            f"Dataset:          registered as trainset id={ds_id} "
            f"(slug={slug}, parent={parent_id})"
        )
    except Exception as exc:  # noqa: BLE001
        click.echo(
            f"\nWARNING: amplify output saved but trainset registration "
            f"failed: {exc}. Run 'sio reproduce <module_id>' to verify."
        )


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
@click.option(
    "--trainset-file",
    default=None,
    help=(
        "Path to a curated JSONL produced by `sio curate`. "
        "When set, the optimizer reads trainset from this file instead of "
        "the live ground_truth table — recommended to avoid concept drift."
    ),
)
@click.option(
    "--baseline-against",
    default=None,
    type=int,
    help=(
        "Compare the new optimization score against an existing "
        "optimized_modules.id. If new score < baseline, refuse to mark active "
        "(treats the new artifact as a candidate, not a promotion)."
    ),
)
@click.option(
    "--task-mode",
    type=click.Choice(["work", "cheap", "free", "personal", "personal-strong"]),
    default=None,
    help=(
        "LM tier for the task LM (per-example evals). "
        "work=gemini-pro, cheap=gemini-flash, free=ollama, personal=gpt-4o-mini, "
        "personal-strong=gpt-5. Overrides SIO_TASK_LM."
    ),
)
@click.option(
    "--reflection-mode",
    type=click.Choice(["work", "cheap", "free", "personal", "personal-strong"]),
    default=None,
    help=(
        "LM tier for the reflection LM (GEPA's critic). "
        "Same tiers as --task-mode. Overrides SIO_REFLECTION_LM. "
        "personal-strong (gpt-5) requires explicit opt-in per Principle XII."
    ),
)
@click.option(
    "--gepa-budget",
    type=click.Choice(["light", "medium", "heavy"]),
    default=None,
    help=(
        "GEPA budget tier (auto=light|medium|heavy). Overrides SIO_GEPA_BUDGET. "
        "light=$5-8, medium=$15-25, heavy=$40-80 (with gpt-5 reflection)."
    ),
)
@click.option(
    "--budget-override",
    type=float,
    default=None,
    help=(
        "Override the 24h rolling spend cap from [budget] in ~/.sio/config.toml "
        "for this invocation (XII clause 6 escape hatch). Pass a USD amount."
    ),
)
@click.option(
    "--skip-ladder",
    is_flag=True,
    default=False,
    help=(
        "Bypass the optimizer-ladder discipline gate (Constitution XIV proposed): "
        "by default, `--optimizer gepa` refuses to run on a registered trainset "
        "if no prior MIPROv2 run exists for the same module on the same dataset. "
        "The ladder is Bootstrap → MIPROv2 → GEPA; skipping rungs wastes the "
        "expensive Pro/gpt-5 reflection budget on configurations MIPROv2 may "
        "already have found near-optimum. Pass this flag to override (a note is "
        "logged so SIO mining can track ladder-skip frequency)."
    ),
)
@click.option(
    "--skip-data-gate",
    is_flag=True,
    default=False,
    help=(
        "Bypass the MIPROv2 data-size gate: by default, `--optimizer mipro` "
        "refuses to run when valset_size < max(25, trainset_size * 0.2). "
        "MIPROv2's Bayesian search needs ~25-50+ valset rows to reliably "
        "outperform Bootstrap; below threshold it often UNDER-performs "
        "(see optimized_modules row #17 vs #16 on 2026-05-18). Override "
        "logged via runlog so SIO mining can track data-gate-skip frequency."
    ),
)
@click.option(
    "--resume-from",
    type=int,
    default=None,
    metavar="MODULE_ID",
    help=(
        "Resume the optimizer ladder after a prior successful run. Pass the "
        "optimized_modules.id of the most recent successful rung (Bootstrap "
        "or MIPROv2). Auto-resolves --trainset-file from that row's "
        "trainset_id so the new run uses the same dataset. Records the "
        "lineage in runlog metadata for traceability. Useful for crash "
        "recovery in background-SIO cron runs: if GEPA crashed after "
        "Bootstrap+MIPROv2 landed, rerun with --resume-from <mipro_id> "
        "to pick up at GEPA without re-running the prior rungs."
    ),
)
@click.option(
    "--skip-amplify-gate",
    is_flag=True,
    default=False,
    help=(
        "Bypass the amplify-first discipline gate: by default, "
        "`--optimizer mipro|gepa` refuses to run on a trainset with "
        "source='curate' (un-amplified) because the optimizer ladder "
        "discipline is Bootstrap → AMPLIFY → MIPROv2 → GEPA. Empirically: "
        "today's GEPA on the 93-row curated baseline timed out at 60 min "
        "($1.11 wasted) while GEPA #14/#15 on the same baseline amplified "
        "to 372 rows produced 0.7224 / 0.8653 scores. Override logged "
        "via runlog so SIO mining can track amplify-skip frequency."
    ),
)
@runlogged("optimize")
def optimize_cmd(
    module_name,
    optimizer_name,
    trainset_size,
    valset_size,
    dry_run,
    trainset_file,
    baseline_against,
    task_mode,
    reflection_mode,
    gepa_budget,
    budget_override,
    skip_ladder,
    skip_data_gate,
    resume_from,
    skip_amplify_gate,
):
    """Run prompt optimization against the gold_standards corpus.

    Uses GEPA (or mipro/bootstrap in Wave 6) to compile an optimized
    DSPy program and save the artifact to ~/.sio/optimized/.
    Records the run in the optimized_modules table.

    Use ``--trainset-file <path>`` to point at a curated JSONL produced by
    ``sio curate`` — this is the recommended path for production runs to
    avoid concept-drift in the trainset.
    """
    from rich.console import Console  # noqa: PLC0415

    console = Console()

    # T4 (XII clause 3 / XIV LM split): resolve --task-mode / --reflection-mode
    # into env vars that lm_factory honors. This wraps the env-var contract
    # in CLI-flag ergonomics for the multi-train driver.
    _MODE_TO_MODEL = {
        "task": {
            "work":            "gemini/gemini-pro-latest",
            "cheap":           "gemini/gemini-flash-latest",
            "free":            "ollama_chat/qwen3-coder:30b",
            "personal":        "openai/gpt-4o-mini",
            "personal-strong": "openai/gpt-5",
        },
        "reflection": {
            "work":            "gemini/gemini-pro-latest",
            "cheap":           "gemini/gemini-flash-latest",
            "free":            "ollama_chat/deepseek-r1:32b",
            "personal":        "openai/gpt-4o-mini",
            "personal-strong": "openai/gpt-5",
        },
    }
    if task_mode:
        os.environ["SIO_TASK_LM"] = _MODE_TO_MODEL["task"][task_mode]
        console.print(f"  [dim]--task-mode={task_mode} → SIO_TASK_LM={os.environ['SIO_TASK_LM']}[/dim]")
    if reflection_mode:
        os.environ["SIO_REFLECTION_LM"] = _MODE_TO_MODEL["reflection"][reflection_mode]
        console.print(f"  [dim]--reflection-mode={reflection_mode} → SIO_REFLECTION_LM={os.environ['SIO_REFLECTION_LM']}[/dim]")
    if gepa_budget:
        os.environ["SIO_GEPA_BUDGET"] = gepa_budget
        console.print(f"  [dim]--gepa-budget={gepa_budget} → SIO_GEPA_BUDGET={gepa_budget}[/dim]")

    # --resume-from: thread context from a prior successful rung. Look up
    # the named module's trainset_id, auto-populate --trainset-file if the
    # user didn't pass one. Log resume metadata for lineage.
    # Use case: background-SIO cron crashes mid-GEPA after Bootstrap+MIPROv2
    # landed; restart with --resume-from <mipro_id> picks up at GEPA.
    if resume_from is not None:
        try:
            import sqlite3 as _sql  # noqa: PLC0415
            _db = os.environ.get(
                "SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db")
            )
            with _sql.connect(_db) as _c:
                _c.row_factory = _sql.Row
                _prior = _c.execute(
                    "SELECT id, optimizer_used, score, trainset_id "
                    "FROM optimized_modules WHERE id=?",
                    (resume_from,),
                ).fetchone()
            if _prior is None:
                console.print(
                    f"[red]--resume-from id={resume_from} not found in "
                    f"optimized_modules.[/red]"
                )
                raise SystemExit(4)
            if _prior["score"] is None:
                console.print(
                    f"[yellow]--resume-from id={resume_from} has no score "
                    f"(failed run). Refusing to resume from a failed rung.[/yellow]"
                )
                raise SystemExit(4)
            # Auto-resolve --trainset-file from the prior row's trainset_id
            if trainset_file is None and _prior["trainset_id"] is not None:
                try:
                    from sio.core.datasets import find_by_hash  # noqa: PLC0415  # noqa: F401
                    # registry exposes the trainsets row; just look it up by id
                    with _sql.connect(_db) as _c:
                        _c.row_factory = _sql.Row
                        _ts = _c.execute(
                            "SELECT stored_path, slug FROM trainsets WHERE id=?",
                            (_prior["trainset_id"],),
                        ).fetchone()
                    if _ts and _ts["stored_path"]:
                        trainset_file = _ts["stored_path"]
                        console.print(
                            f"  [dim]--resume-from {resume_from}: auto-resolved "
                            f"--trainset-file={trainset_file} (trainset_id="
                            f"{_prior['trainset_id']}, slug={_ts['slug']})[/dim]"
                        )
                except Exception as exc:  # noqa: BLE001
                    console.print(
                        f"  [yellow]--resume-from: trainset auto-resolve failed: "
                        f"{exc}[/yellow]"
                    )
            console.print(
                f"  [dim]--resume-from: lineage anchored to module_id="
                f"{_prior['id']} optimizer={_prior['optimizer_used']} "
                f"score={_prior['score']:.4f}[/dim]"
            )
            # Log resume metadata to the active runlog stage
            rl = _runlog_current()
            try:
                rl.note(
                    f"resume_from_module_id={resume_from} "
                    f"prior_optimizer={_prior['optimizer_used']} "
                    f"prior_score={_prior['score']:.4f}"
                )
            except Exception:  # noqa: BLE001
                pass
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"  [yellow]--resume-from check failed ({exc}); ignoring flag.[/yellow]"
            )

    # Amplify-first + row-floor discipline gate (Constitution XIV proposed —
    # Tier 6). Bootstrap → AMPLIFY → MIPROv2 → GEPA is the canonical ladder.
    # TWO checks fire here, BOTH for MIPROv2 and GEPA:
    #   (a) source must NOT be 'curate' (i.e. data must be amplified or have
    #       been auto-promoted from a manual file)
    #   (b) row_count must meet a per-optimizer empirical floor
    #
    # Empirical floors (2026-05-18 evidence):
    #   MIPROv2 min 200 rows — #18 success at 93 was borderline (0.7705 vs
    #     Bootstrap 0.7154 by only +7.7%; could be noise on small data)
    #   GEPA min 300 rows — #15 success at 372 was solid (0.8653); today's
    #     failure at 93 timed out at 60 min with $1.11 wasted in gpt-5
    #     reflection that never converged
    _MIN_ROWS = {"mipro": 200, "gepa": 300}
    if optimizer_name in ("mipro", "gepa") and trainset_file and not skip_amplify_gate:
        try:
            from pathlib import Path as _P  # noqa: PLC0415
            from sio.core.datasets import find_by_hash, hash_file  # noqa: PLC0415
            tf = _P(trainset_file).expanduser()
            sha = hash_file(tf)
            ds_row = find_by_hash(sha)
            if ds_row is not None:
                _src = ds_row["source"] or ""
                _rows = ds_row["row_count"] or 0
                _min_rows = _MIN_ROWS[optimizer_name]
                if _src == "curate":
                    console.print(
                        f"[red]AMPLIFY-FIRST VIOLATION:[/red] trainset "
                        f"id=[cyan]{ds_row['id']}[/cyan] (sha={sha[:12]}) is "
                        f"un-amplified (source=[cyan]curate[/cyan]). "
                        f"[cyan]{optimizer_name}[/cyan] requires amplified data.\n"
                        f"\n  The ladder discipline is [bold]Bootstrap → "
                        f"AMPLIFY → MIPROv2 → GEPA[/bold]. Empirically: "
                        f"GEPA on un-amplified 93-row curate timed out at "
                        f"60 min on 2026-05-18 ($1.11 wasted), while GEPA "
                        f"on the same baseline amplified to 372 rows produced "
                        f"0.8653 (#15). Run amplify first:\n"
                        f"    [dim]sio amplify -i {trainset_file} --n-per-row 3[/dim]\n"
                        f"  Then re-run with the amplified output (auto-registered "
                        f"in trainsets with parent_dataset_id pointing here).\n"
                        f"\n  Or override with [bold]--skip-amplify-gate[/bold] "
                        f"(logged for SIO mining)."
                    )
                    raise SystemExit(6)
                if _rows < _min_rows:
                    # Calculate the n_per_row needed to reach the floor
                    _ratio_needed = (_min_rows + _rows - 1) // max(_rows, 1)
                    _n_per_row_suggested = max(_ratio_needed, 3)
                    console.print(
                        f"[red]ROW-FLOOR VIOLATION:[/red] trainset id="
                        f"[cyan]{ds_row['id']}[/cyan] has [cyan]{_rows}[/cyan] "
                        f"rows; [cyan]{optimizer_name}[/cyan] requires >= "
                        f"[cyan]{_min_rows}[/cyan] for reliable optimization.\n"
                        f"\n  Empirical floors per optimizer:\n"
                        f"    MIPROv2: 200 rows (Bayesian search needs candidate signal)\n"
                        f"    GEPA:    300 rows (reflection converges with diverse examples)\n"
                        f"\n  Grow the dataset:\n"
                        f"    [dim]sio amplify -i {trainset_file} "
                        f"--n-per-row {_n_per_row_suggested}[/dim]\n"
                        f"  to reach ~{_rows * _n_per_row_suggested} rows. Then "
                        f"re-run {optimizer_name} on the amplified output.\n"
                        f"\n  Or override with [bold]--skip-amplify-gate[/bold] "
                        f"(logged for SIO mining)."
                    )
                    raise SystemExit(6)
            # If trainset is unregistered, can't check source — let it through
            # (the auto-register on optimize will tag source='manual')
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"  [yellow]amplify-gate: check failed ({exc}); allowing run.[/yellow]"
            )
    elif optimizer_name in ("mipro", "gepa") and skip_amplify_gate:
        console.print(
            f"  [yellow]--skip-amplify-gate:[/yellow] bypassing amplify-first "
            f"discipline for {optimizer_name}. Logged for SIO mining."
        )
        rl = _runlog_current()
        try:
            rl.warn(
                "AMPLIFY_SKIP",
                f"{optimizer_name} run on module={module_name} "
                f"trainset={trainset_file} bypassed amplify-first gate "
                f"via --skip-amplify-gate",
                stage="optimize_run",
            )
        except Exception:  # noqa: BLE001
            pass

    # MIPROv2 data-size gate (companion to Constitution XIV — Optimizer
    # Ladder Discipline). MIPROv2's Bayesian search over instructions needs
    # ~25-50+ valset rows to reliably outperform Bootstrap; below threshold
    # it overfits to the small valset and often UNDER-performs (empirical:
    # row #17 MIPROv2 0.6970 < row #16 Bootstrap 0.7154 on the same dataset
    # with valset_size=5, 2026-05-18). Threshold: max(25, trainset * 0.2).
    # When refused, the recommended path is to GROW the dataset via
    # `sio amplify` first, then re-run MIPROv2 on the amplified output.
    if optimizer_name == "mipro" and not skip_data_gate:
        _min_valset = max(25, int(trainset_size * 0.2))
        if valset_size < _min_valset:
            _suggest_input = trainset_file or "<your-curate-output.jsonl>"
            console.print(
                f"[red]DATA-SIZE VIOLATION:[/red] MIPROv2 needs "
                f"valset_size >= [cyan]{_min_valset}[/cyan] (max(25, "
                f"trainset_size * 0.2)). Got valset_size=[cyan]{valset_size}[/cyan].\n"
                f"\n  MIPROv2's Bayesian search overfits below this threshold "
                f"and typically under-performs Bootstrap (see optimized_modules "
                f"#17 vs #16, 2026-05-18). The recommended path is to GROW the "
                f"dataset via amplify first:\n"
                f"    [dim]sio amplify -i {_suggest_input} --n-per-row 3[/dim]\n"
                f"  Then re-run MIPROv2 against the amplified output (which "
                f"will be auto-registered in trainsets).\n"
                f"\n  Or override with [bold]--skip-data-gate[/bold] (logged for "
                f"SIO mining)."
            )
            raise SystemExit(3)
    elif optimizer_name == "mipro" and skip_data_gate:
        _min_valset = max(25, int(trainset_size * 0.2))
        if valset_size < _min_valset:
            console.print(
                f"  [yellow]--skip-data-gate:[/yellow] bypassing MIPROv2 "
                f"data-size gate (valset_size={valset_size} < "
                f"min={_min_valset}). Logged for SIO mining."
            )
            rl = _runlog_current()
            try:
                rl.warn(
                    "DATA_SIZE_SKIP",
                    f"MIPROv2 run on module={module_name} "
                    f"trainset_size={trainset_size} valset_size={valset_size} "
                    f"bypassed min_valset={_min_valset} via --skip-data-gate",
                    stage="optimize_run",
                )
            except Exception:  # noqa: BLE001
                pass

    # Proposed Constitution XIV (Optimizer Ladder Discipline): refuse GEPA
    # without a prior successful MIPROv2 run on the same module + same
    # dataset. The ladder is Bootstrap → MIPROv2 → GEPA. Skipping rungs
    # wastes Pro/gpt-5 reflection budget on configurations MIPROv2 may
    # already have found near-optimum.
    #
    # The gate only fires when:
    #   - optimizer_name == "gepa"  (other optimizers are upstream rungs)
    #   - trainset_file is set      (can't enforce on live ground_truth)
    #   - --skip-ladder is NOT set  (explicit user override)
    # If skipped via flag, log to runlog so SIO mining can track frequency.
    if optimizer_name == "gepa" and trainset_file and not skip_ladder:
        try:
            from pathlib import Path as _P  # noqa: PLC0415
            import sqlite3 as _sql  # noqa: PLC0415
            from sio.core.datasets import find_by_hash, hash_file  # noqa: PLC0415
            tf = _P(trainset_file).expanduser()
            sha = hash_file(tf)
            ds_row = find_by_hash(sha)
            if ds_row is None:
                # Unregistered trainset → can't check ladder. Warn but allow
                # (the optimize wire-up will auto-register on success).
                console.print(
                    "  [yellow]ladder-gate:[/yellow] trainset is unregistered "
                    "(no `trainsets` row for this sha); skipping ladder check."
                )
            else:
                db_path = os.environ.get(
                    "SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db")
                )
                with _sql.connect(db_path) as _c:
                    _c.row_factory = _sql.Row
                    prior_mipro = _c.execute(
                        "SELECT id, score, created_at FROM optimized_modules "
                        "WHERE module_type=? AND trainset_id=? "
                        "AND (optimizer_name='mipro' OR optimizer_used='mipro') "
                        "AND score IS NOT NULL ORDER BY id DESC LIMIT 1",
                        (module_name, ds_row["id"]),
                    ).fetchone()
                if prior_mipro is None:
                    console.print(
                        f"[red]LADDER VIOLATION:[/red] No prior MIPROv2 run exists "
                        f"for module=[cyan]{module_name}[/cyan] on trainset "
                        f"id=[cyan]{ds_row['id']}[/cyan] (sha={sha[:12]}).\n"
                        f"\n  The optimizer ladder is "
                        f"[bold]Bootstrap → MIPROv2 → GEPA[/bold]. Run MIPROv2 first:\n"
                        f"    [dim]sio optimize --optimizer mipro --trainset-file {trainset_file}[/dim]\n"
                        f"\n  Or override with [bold]--skip-ladder[/bold] if you have "
                        f"a specific reason (logged for SIO mining)."
                    )
                    raise SystemExit(2)
                console.print(
                    f"  [dim]ladder-gate: ok (prior MIPROv2 run id="
                    f"{prior_mipro['id']} score={prior_mipro['score']:.4f})[/dim]"
                )
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            # Gate failure must NOT block — log and continue. The point is
            # discipline, not infrastructure-fragility.
            console.print(
                f"  [yellow]ladder-gate: check failed ({exc}); allowing run.[/yellow]"
            )
    elif optimizer_name == "gepa" and skip_ladder:
        console.print(
            "  [yellow]--skip-ladder:[/yellow] bypassing optimizer-ladder "
            "discipline gate. Logged for SIO mining (track ladder-skip frequency)."
        )
        rl = _runlog_current()
        try:
            rl.warn(
                "LADDER_SKIP",
                f"GEPA run on module={module_name} trainset={trainset_file} "
                f"bypassed MIPROv2 prerequisite via --skip-ladder",
                stage="optimize_run",
            )
        except Exception:  # noqa: BLE001
            pass

    # XII clause 6: budget guard. Halt if 24h rolling spend exceeds cap.
    if not dry_run:
        from sio.core.cost import BudgetExceeded, check_budget  # noqa: PLC0415
        try:
            state = check_budget(override_usd=budget_override)
            console.print(
                f"  [dim]budget: ${state['spend_24h_usd']:.2f}/24h used "
                f"of ${state['effective_cap_usd']:.2f} cap "
                f"(${state['remaining_usd']:.2f} remaining)[/dim]"
            )
        except BudgetExceeded as exc:
            console.print(f"[red]BUDGET EXCEEDED:[/red] {exc}")
            raise SystemExit(1)

    if dry_run:
        console.print("[bold]Dry run — config:[/bold]")
        console.print(f"  module:           {module_name}")
        console.print(f"  optimizer:        {optimizer_name}")
        console.print(f"  trainset_size:    {trainset_size}")
        console.print(f"  valset_size:      {valset_size}")
        console.print(f"  trainset_file:    {trainset_file or '(live ground_truth)'}")
        console.print(f"  baseline_against: {baseline_against or '(none)'}")
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

    # XIII clause 6: heartbeat so a long-running optimize is never silent
    from sio.core.runlog import Heartbeat  # noqa: PLC0415
    rl = _runlog_current()
    result = None
    try:
        with rl.stage("optimize_run") as s, Heartbeat(rl, s, interval=30) as hb:
            result = run_optimize(
                module_name=module_name,
                optimizer_name=optimizer_name,
                trainset_size=trainset_size,
                valset_size=valset_size,
                trainset_file=trainset_file,
            )
            hb.progress()
            if result is not None:
                s.note(f"score={result.get('score')} optimizer={result.get('optimizer')}")
    except InsufficientData as exc:
        console.print(
            f"[red]Insufficient data:[/red] {exc}\n"
            "Run [bold]sio curate --emphasis --classified[/bold] to build a "
            "curated trainset, OR [bold]sio promote-positives[/bold] to "
            "wire positive_records into the review queue."
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

    # Principle XIII (observability) + proposed XV (reproducibility):
    # close the optimize → trainsets loop. Without this, the resulting
    # optimized_modules row has trainset_id=NULL and the doctor's
    # Reproducibility-Gap warning fires ("N/N active modules have gaps").
    # When --trainset-file was given AND the file is registered in
    # trainsets, link the new row's trainset_id. If unregistered, register
    # it now (auto-promote a one-shot file into a permanent dataset).
    if trainset_file:
        try:
            from pathlib import Path as _P  # noqa: PLC0415
            import sqlite3 as _sql  # noqa: PLC0415
            from sio.core.datasets import (  # noqa: PLC0415
                find_by_hash, hash_file, link_optimized_module, register_dataset,
            )
            tf = _P(trainset_file).expanduser()
            sha = hash_file(tf)
            row = find_by_hash(sha)
            if row is None:
                # Auto-register so the dataset becomes content-addressable
                # even when the user pointed at an ad-hoc file. Slug derived
                # from filename stem; source='manual' marks the lineage gap.
                ds_id = register_dataset(
                    source_path=tf,
                    slug=tf.stem,
                    description=f"Auto-registered from sio optimize --trainset-file (was unregistered)",
                    source="manual",
                )
            else:
                ds_id = row["id"]
            # Look up the freshly-inserted module_id by max(id) for this module_type
            db_path = os.environ.get(
                "SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db")
            )
            with _sql.connect(db_path) as _c:
                _c.row_factory = _sql.Row
                latest = _c.execute(
                    "SELECT id FROM optimized_modules WHERE module_type=? "
                    "ORDER BY id DESC LIMIT 1",
                    (module_name,),
                ).fetchone()
            if latest:
                link_optimized_module(latest["id"], ds_id)
                console.print(
                    f"  trainset:  linked id={ds_id} (sha={sha[:12]}) → "
                    f"optimized_modules.id={latest['id']}"
                )
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"  [yellow]warn:[/yellow] trainset link failed: {exc} — "
                f"reproducibility-gap warning may persist for this run."
            )

    # --baseline-against gate: refuse to promote if score regresses
    if baseline_against is not None:
        import sqlite3 as _sql  # noqa: PLC0415
        db_path = os.environ.get(
            "SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db")
        )
        with _sql.connect(db_path) as _c:
            _c.row_factory = _sql.Row
            row = _c.execute(
                "SELECT metric_after, module_type FROM optimized_modules WHERE id=?",
                (baseline_against,),
            ).fetchone()
            if row is None:
                console.print(
                    f"[yellow]--baseline-against id={baseline_against} not found; "
                    f"skipping comparison.[/yellow]"
                )
            else:
                baseline_score = row["metric_after"] or 0.0
                new_score = result["score"]
                delta = new_score - baseline_score
                if delta < 0:
                    console.print(
                        f"[red]REGRESSION:[/red] new score {new_score:.4f} < "
                        f"baseline {baseline_score:.4f} (Δ {delta:+.4f}). "
                        "Marking new module as INACTIVE."
                    )
                    _c.execute(
                        "UPDATE optimized_modules SET is_active=0 "
                        "WHERE module_type=? AND created_at=("
                        "SELECT MAX(created_at) FROM optimized_modules WHERE module_type=?)",
                        (module_name, module_name),
                    )
                    _c.commit()
                else:
                    console.print(
                        f"[green]Δ {delta:+.4f} vs baseline {baseline_score:.4f}[/green] "
                        "— promotion confirmed."
                    )


# ---------------------------------------------------------------------------
# Compound `sio optimize-ladder` — auto-magic Bootstrap → AMPLIFY → MIPROv2 → GEPA
# (PRD sio_background_persistence_design_2026-05-18 Tier 1 MVP)
# ---------------------------------------------------------------------------


@cli.command("optimize-ladder")
@click.option("--trainset-file", required=True,
              help="Input JSONL (curate output). The compound command will "
                   "amplify it if rows < --target-amplified-rows, then run "
                   "Bootstrap → MIPROv2 → GEPA on the amplified output.")
@click.option("--module", default="suggestion_generator", show_default=True,
              help="Module to optimize.")
@click.option("--target-amplified-rows", default=300, show_default=True, type=int,
              help="Minimum amplified row count required for MIPROv2/GEPA. "
                   "Defaults to 300 (GEPA's floor; satisfies MIPROv2's 200 too).")
@click.option("--amplify-n-per-row", default=3, show_default=True, type=int,
              help="Variants generated per row during the amplify step.")
@click.option("--task-mode", default="cheap", show_default=True,
              type=click.Choice(["work", "cheap", "free", "personal", "personal-strong"]))
@click.option("--reflection-mode", default="personal-strong", show_default=True,
              type=click.Choice(["work", "cheap", "free", "personal", "personal-strong"]))
@click.option("--yes", is_flag=True, default=False,
              help="Skip the cost-confirmation prompt (still subject to "
                   "global budget cap from [budget] in ~/.sio/config.toml).")
@click.option("--budget-override", type=float, default=None,
              help="Per-invocation 24h budget cap override (XII clause 6).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print the plan + cost estimate, do nothing.")
@runlogged("optimize-ladder")
def optimize_ladder_cmd(
    trainset_file, module, target_amplified_rows, amplify_n_per_row,
    task_mode, reflection_mode, yes, budget_override, dry_run,
):
    """Run the full optimizer ladder (Bootstrap → AMPLIFY → MIPROv2 → GEPA).

    Auto-magic prereq chain that wraps the three discipline gates shipped
    today:
      - ladder gate (refuses GEPA without prior MIPROv2)
      - data-size gate (refuses MIPROv2 below valset floor)
      - amplify-first gate (refuses MIPROv2/GEPA on curate or <300 rows)

    Skips rungs that already have a successful row in optimized_modules
    for the relevant trainset_id (idempotent on re-run — useful for cron
    crash recovery).

    Empirical basis: GEPA on amplified 372-row trainset produced 0.8653
    (#15, 2026-05-16); GEPA on un-amplified 93-row curate timed out
    after 60 min wasting $1.11 (2026-05-18). This command makes the
    successful path the default.

    Example:
        sio optimize-ladder --trainset-file ~/.sio/datasets/curated.jsonl --yes
    """
    import subprocess as _sp  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415
    from rich.console import Console  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415

    # Constitution XVI — Background-mode hooks. When SIO_BACKGROUND_MODE=1
    # is set (cron / systemd / non-interactive callers), suppress all
    # interactive prompts AND Rich ANSI codes (cron mail is plaintext).
    # Force --yes implicitly; any gate refusal still aborts with non-zero
    # exit. Every state transition is also appended to a cron audit log.
    _background_mode = os.environ.get("SIO_BACKGROUND_MODE") == "1"
    if _background_mode:
        console = Console(force_terminal=False, no_color=True, width=200)
        yes = True  # implicit --yes; no human to confirm anything
    else:
        console = Console()

    # ---- Step 1: resolve input trainset + plan the rungs --------------------
    try:
        from sio.core.datasets import find_by_hash, hash_file  # noqa: PLC0415
        from sio.core.cost.estimator import estimate_optimize_run  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not load datasets/cost modules:[/red] {exc}")
        raise SystemExit(1)

    tf = _P(trainset_file).expanduser()
    if not tf.exists():
        console.print(f"[red]Trainset file not found:[/red] {tf}")
        raise SystemExit(1)

    sha = hash_file(tf)
    ds_row = find_by_hash(sha)

    plan = []  # list of (step_name, cmd_args, est_cost) tuples
    target_for_mipro_gepa = trainset_file  # may be replaced post-amplify

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))

    def _has_rung(module_type: str, trainset_id: int, optimizer: str) -> bool:
        import sqlite3 as _sql  # noqa: PLC0415
        with _sql.connect(db_path) as _c:
            _c.row_factory = _sql.Row
            r = _c.execute(
                "SELECT id FROM optimized_modules WHERE module_type=? AND "
                "trainset_id=? AND (optimizer_used=? OR optimizer_name=?) "
                "AND score IS NOT NULL LIMIT 1",
                (module_type, trainset_id, optimizer, optimizer),
            ).fetchone()
        return r is not None

    # Bootstrap on the ORIGINAL trainset (cheap; runs even on un-amplified)
    bootstrap_done = ds_row is not None and _has_rung(module, ds_row["id"], "bootstrap")
    if not bootstrap_done:
        est = estimate_optimize_run("bootstrap", "light")
        plan.append((
            "bootstrap (original trainset)",
            ["sio", "optimize", "--module", module, "--optimizer", "bootstrap",
             "--trainset-file", str(tf), "--trainset-size", "20", "--valset-size", "5"],
            est["total"]["mid"],
        ))

    # Amplify decision
    needs_amplify = False
    if ds_row is None:
        needs_amplify = True  # unregistered — assume amplification needed
        amplified_path = None
    elif (ds_row["source"] or "") == "curate" or (ds_row["row_count"] or 0) < target_amplified_rows:
        needs_amplify = True
        amplified_path = str(_P.home() / ".sio" / "amplified" /
                             f"{tf.stem}_amplified.jsonl")
    else:
        # Already amplified + meets row floor — use as-is for MIPRO/GEPA
        amplified_path = str(tf)

    if needs_amplify:
        # Cost: amplify produces ~n_per_row × row_count Flash calls
        rows_in = ds_row["row_count"] if ds_row else 93  # default guess
        amplify_calls = rows_in * amplify_n_per_row + rows_in  # gen + judge
        amplify_cost = (amplify_calls * 2000 * 0.075 +    # input tokens × $/M
                        amplify_calls * 2000 * 0.30) / 1_000_000
        plan.append((
            f"amplify ({rows_in} → ~{rows_in * (1 + amplify_n_per_row)} rows)",
            ["sio", "amplify", "-i", str(tf), "-n", str(amplify_n_per_row),
             "--task-mode", "cheap"],
            amplify_cost,
        ))
        target_for_mipro_gepa = amplified_path

    # MIPROv2 on the amplified target — cannot check `_has_rung` yet because
    # amplified trainset may not exist until the amplify step runs. Always
    # plan it; the inner `sio optimize` invocation's gates will short-circuit
    # if a row already exists (via --resume-from semantics in a future iter).
    mipro_est = estimate_optimize_run("mipro", "light")
    plan.append((
        "mipro (amplified trainset)",
        ["sio", "optimize", "--module", module, "--optimizer", "mipro",
         "--trainset-file", str(target_for_mipro_gepa),
         "--trainset-size", "200", "--valset-size", "50",
         "--task-mode", task_mode, "--reflection-mode", reflection_mode],
        mipro_est["total"]["mid"],
    ))

    # GEPA on the amplified target
    gepa_est = estimate_optimize_run("gepa", "light",
                                     task_lm="gemini/gemini-flash-latest",
                                     reflection_lm="openai/gpt-5")
    plan.append((
        "gepa (amplified trainset)",
        ["sio", "optimize", "--module", module, "--optimizer", "gepa",
         "--trainset-file", str(target_for_mipro_gepa),
         "--trainset-size", "200", "--valset-size", "50",
         "--task-mode", task_mode, "--reflection-mode", reflection_mode],
        gepa_est["total"]["mid"],
    ))

    # ---- Step 2: show plan + cost estimate ----------------------------------
    table = Table(title="Compound Ladder Plan", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Step")
    table.add_column("Est. cost", justify="right")
    total_cost = 0.0
    for i, (step_name, _cmd, cost) in enumerate(plan, 1):
        table.add_row(str(i), step_name, f"${cost:.2f}")
        total_cost += cost
    table.add_row("", "[bold]TOTAL[/bold]", f"[bold]${total_cost:.2f}[/bold]")
    console.print(table)

    if dry_run:
        console.print("[yellow]--dry-run:[/yellow] not executing. Use --yes to run.")
        return

    if not yes:
        if not click.confirm(
            f"\nProceed with ladder run? Est. ${total_cost:.2f} of LLM "
            "budget. Each rung writes its row before the next starts, so a "
            "crash is recoverable.",
            default=False,
        ):
            console.print("[yellow]Aborted.[/yellow]")
            raise SystemExit(0)

    # ---- Step 2.5: write ladder state file (PRD Tier 2 — cron observability)
    # ~/.sio/state/ladder_status.json holds the in-flight + done state so
    # `sio doctor --ladder` (or any external monitor) can answer "is this
    # ladder making progress?" without needing to crawl the DB. Updated
    # after every rung. Self-cleaning on success at the end.
    import json as _json  # noqa: PLC0415
    import datetime as _dt2  # noqa: PLC0415
    state_dir = _P.home() / ".sio" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "ladder_status.json"

    def _now_iso() -> str:
        return _dt2.datetime.now(_dt2.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    ladder_state = {
        "started_at": _now_iso(),
        "trainset_file": str(tf),
        "trainset_sha": sha,
        "trainset_id": ds_row["id"] if ds_row else None,
        "module": module,
        "plan": [s for s, _c, _co in plan],
        "rungs": [],  # appended after each step
        "total_estimated_usd": round(total_cost, 4),
        "status": "in_flight",
        "process_id": os.getpid(),
    }
    try:
        state_file.write_text(_json.dumps(ladder_state, indent=2))
    except Exception:
        pass  # state file is observability — never crash on it

    # ---- Step 3: execute each rung ------------------------------------------
    for i, (step_name, cmd, cost) in enumerate(plan, 1):
        console.print(f"\n[bold cyan]═══ Rung {i}/{len(plan)}: {step_name} "
                      f"(est ${cost:.2f}) ═══[/bold cyan]")
        # Mark this rung as in-flight in state file
        rung_state = {
            "rung": i,
            "step": step_name,
            "status": "running",
            "started_at": _now_iso(),
            "finished_at": None,
            "exit_code": None,
            "est_cost_usd": round(cost, 4),
        }
        ladder_state["rungs"].append(rung_state)
        ladder_state["current_rung"] = i
        try:
            state_file.write_text(_json.dumps(ladder_state, indent=2))
        except Exception:
            pass

        # Pass budget_override through to each rung if set
        if budget_override is not None and "optimize" in cmd[1]:
            cmd = cmd + ["--budget-override", str(budget_override)]
        result = _sp.run(cmd, capture_output=False)
        rung_state["finished_at"] = _now_iso()
        rung_state["exit_code"] = result.returncode
        rung_state["status"] = "ok" if result.returncode == 0 else "failed"

        # Article XVII clause: outcomes must be surfaced. Pull the score
        # from optimized_modules for THIS rung's optimizer + trainset and
        # write it into the state file so cron / doctor / ladder-status
        # readers see quality signal, not just cost. Looks up by:
        # (module_type, optimizer_used, trainset_id) created after rung start.
        # Best-effort — never crash on observability.
        try:
            import sqlite3 as _sql3  # noqa: PLC0415
            _opt_name = "bootstrap" if "bootstrap" in step_name.lower() else (
                "mipro" if "mipro" in step_name.lower() else (
                    "gepa" if "gepa" in step_name.lower() else None
                )
            )
            if _opt_name:
                _db = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
                with _sql3.connect(_db) as _conn:
                    _conn.row_factory = _sql3.Row
                    _row = _conn.execute(
                        "SELECT id, score, metric_before, metric_after, "
                        "trainset_size, task_lm, reflection_lm "
                        "FROM optimized_modules "
                        "WHERE module_type = ? AND optimizer_used = ? "
                        "AND created_at >= ? "
                        "ORDER BY id DESC LIMIT 1",
                        (module, _opt_name, rung_state["started_at"]),
                    ).fetchone()
                    if _row is not None:
                        rung_state["score"] = (
                            round(_row["score"], 4) if _row["score"] is not None else None
                        )
                        rung_state["metric_after"] = (
                            round(_row["metric_after"], 4)
                            if _row["metric_after"] is not None else None
                        )
                        rung_state["trainset_rows"] = _row["trainset_size"]
                        rung_state["optimized_module_id"] = _row["id"]
                        rung_state["task_lm"] = _row["task_lm"]
                        rung_state["reflection_lm"] = _row["reflection_lm"]
        except Exception as _exc:
            # XIII: loud but non-fatal — score lookup failure means the
            # state file is degraded, not the run.
            import sys as _sys
            print(
                f"  [LADDER_SCORE_LOOKUP_FAIL] rung={i} step={step_name}: "
                f"{type(_exc).__name__}: {_exc}",
                file=_sys.stderr,
                flush=True,
            )

        if result.returncode != 0:
            ladder_state["status"] = "failed"
            try:
                state_file.write_text(_json.dumps(ladder_state, indent=2))
            except Exception:
                pass
            console.print(
                f"[red]Rung {i} ({step_name}) failed with exit "
                f"{result.returncode}.[/red] Subsequent rungs not attempted. "
                f"Re-run the same `sio optimize-ladder` command to resume — "
                f"completed rungs will be detected and skipped. "
                f"State at: {state_file}"
            )
            raise SystemExit(result.returncode)

        # Save state after successful rung — crash recovery anchor
        try:
            state_file.write_text(_json.dumps(ladder_state, indent=2))
        except Exception:
            pass

    ladder_state["status"] = "complete"
    ladder_state["completed_at"] = _now_iso()

    # MIPRO-vs-GEPA cost-justified verdict (origin 2026-05-18 paired-debate).
    # GEPA is ~30x the cost of MIPROv2 ($0.66 vs $0.02). If GEPA scores
    # within `gepa_justified_delta` (default 0.03) of MIPROv2, ship MIPROv2
    # instead. The verdict goes into state file so operator and downstream
    # readers see the recommendation explicitly.
    try:
        _scores = {r["step"].split()[0]: r.get("score")
                   for r in ladder_state["rungs"]
                   if r.get("score") is not None}
        _mipro = _scores.get("mipro")
        _gepa = _scores.get("gepa")
        _boot = _scores.get("bootstrap")
        _delta_thr = 0.03  # >=3% means GEPA cost-justified
        verdict = None
        verdict_reason = None
        if _mipro is not None and _gepa is not None:
            if _gepa < 0.80 and _mipro < 0.79:
                verdict = "both_fail"
                verdict_reason = (
                    f"Both optimizers under-perform their bars "
                    f"(MIPRO={_mipro:.4f}<0.79, GEPA={_gepa:.4f}<0.80). "
                    f"Fix the trainset (amplify quality), not the optimizer."
                )
            elif _gepa - _mipro >= _delta_thr:
                verdict = "gepa_justified"
                verdict_reason = (
                    f"GEPA={_gepa:.4f} beats MIPRO={_mipro:.4f} by "
                    f"{(_gepa - _mipro):.3f} ≥ {_delta_thr} threshold. "
                    f"30x cost is earned — ship GEPA module."
                )
            else:
                verdict = "mipro_wins_on_economics"
                verdict_reason = (
                    f"GEPA={_gepa:.4f} - MIPRO={_mipro:.4f} = "
                    f"{(_gepa - _mipro):+.3f} < {_delta_thr} threshold. "
                    f"GEPA does not earn its 30x cost — ship MIPROv2 "
                    f"module instead."
                )
        elif _gepa is None and _mipro is not None:
            verdict = "gepa_no_score"
            verdict_reason = (
                f"GEPA produced no score (likely aborted or stuck). "
                f"Ship MIPROv2 module (score={_mipro:.4f})."
            )
        if _boot is not None and _mipro is not None and _mipro < _boot:
            verdict = verdict or "mipro_dead_weight"
            verdict_reason = (
                (verdict_reason + " | " if verdict_reason else "")
                + f"WARNING: MIPRO={_mipro:.4f} LOST to Bootstrap="
                f"{_boot:.4f}. Amplification is net-negative for this "
                f"trainset — investigate judge calibration / variant "
                f"quality before next ladder."
            )
        if verdict:
            ladder_state["ladder_verdict"] = verdict
            ladder_state["ladder_verdict_reason"] = verdict_reason
            import sys as _sys  # noqa: PLC0415
            print(
                f"\n[LADDER_VERDICT] {verdict.upper()}: {verdict_reason}",
                file=_sys.stderr, flush=True,
            )
    except Exception as _exc:
        # Verdict is observability only — never crash on it
        import sys as _sys  # noqa: PLC0415
        print(
            f"[LADDER_VERDICT_FAIL] {type(_exc).__name__}: {_exc}",
            file=_sys.stderr, flush=True,
        )

    try:
        state_file.write_text(_json.dumps(ladder_state, indent=2))
    except Exception:
        pass

    # Constitution XVI clause: background-mode audit log. Every state
    # transition (start/finish/fail) appends a compact JSONL line so a
    # cron health check or post-mortem can reconstruct what happened.
    if _background_mode:
        try:
            audit_log = state_dir / "ladder_runs.jsonl"
            audit_log.parent.mkdir(parents=True, exist_ok=True)
            with audit_log.open("a") as _f:
                _f.write(_json.dumps({
                    "ts": _now_iso(),
                    "event": "ladder_complete",
                    "module": module,
                    "trainset_id": ds_row["id"] if ds_row else None,
                    "n_rungs": len(plan),
                    "total_estimated_usd": round(total_cost, 4),
                    "process_id": os.getpid(),
                }) + "\n")
        except Exception:
            pass

    console.print(f"\n[bold green]Ladder complete ({len(plan)} rungs).[/bold green]")
    console.print(f"[dim]State recorded at {state_file}[/dim]")


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
@runlogged("export-dataset")
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("train")
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("collect-recall")
def collect_recall(query, session, project, runbook, label):
    """Collect a recall example for training.

    This is the data collection step: distill a session, optionally attach
    a Gemini-polished runbook, and store as a training example.

    The pipeline: collect → (optional: LLM polish) → label → train

    Examples:
        sio collect-recall "dbt setup" --project dev
        sio collect-recall "dbt setup" --runbook polished.md --label positive
    """
    from pathlib import Path

    from sio.mining.jsonl_parser import parse_jsonl
    from sio.mining.recall import detect_struggles, format_recall_output, topic_filter
    from sio.mining.session_distiller import distill_session

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@click.option(
    "--by-rule",
    is_flag=True,
    default=False,
    help=(
        "Show per-rule error-rate attribution (T1.L.3). For each rule "
        "in ~/.claude/rules/, compute the error rate of records where "
        "that rule was active vs not-active. Requires active_rules "
        "column populated by recent mining."
    ),
)
@click.option(
    "--min-records",
    default=10,
    show_default=True,
    type=int,
    help="(With --by-rule) minimum 'with rule' records to include a rule.",
)
@runlogged("velocity")
def velocity(error_type, window, fmt, skills, by_rule, min_records):
    """Show learning velocity trends — how error rates change after rules.

    Computes error frequency per type over a rolling window, measures
    correction decay after rule application, and flags ineffective rules.

    With --by-rule: switches mode entirely — instead of per-error-type
    trends, computes per-rule attribution from the active_rules column
    on error_records (T1.L.3, PRD sio_backend_dead_loop_2026-05-15).

    Examples:
        sio velocity                          # All error types, 7-day window
        sio velocity --error-type unused_import
        sio velocity --by-rule                # per-rule attribution
        sio velocity --by-rule --format json  # machine-readable
    """
    # --by-rule mode takes the per-rule attribution path (PRD Tier 1)
    if by_rule:
        from sio.core.metrics.velocity import (  # noqa: PLC0415
            compute_rule_outcomes,
        )

        db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
        if not os.path.exists(db_path):
            click.echo("No database found. Run 'sio mine' first.")
            return

        with _db_conn(db_path) as conn:
            try:
                outcomes = compute_rule_outcomes(conn, window_days=window)
            except Exception as e:  # noqa: BLE001
                click.echo(f"Error computing rule outcomes: {e}")
                return

        # Build flat per-(rule, error_type, surface) result list for output.
        results: list[dict] = []
        for o in outcomes:
            if o["n_after_total"] < min_records:
                continue
            for bd in o["by_type"]:
                results.append({
                    "rule_id": bd["rule_id"],
                    "error_type": bd["error_type"],
                    "target_surface": bd["target_surface"],
                    "first_seen": o["first_seen"],
                    "n_before": bd["n_before"],
                    "n_after": bd["n_after"],
                    "delta_pct": bd["delta_pct"],
                    "confidence": bd["confidence"],
                    "recommend": bd["recommend"],
                })

        if not results:
            click.echo(
                "No rules with >= {} active_rules-stamped post-window records "
                "yet. Run `sio mine` so future records get stamped, then wait "
                "for organic rule churn.".format(min_records)
            )
            return

        if fmt == "json":
            click.echo(_json.dumps(results, indent=2, default=str))
            return

        # Table output (Rich)
        try:
            from rich.console import Console  # noqa: PLC0415
            from rich.table import Table  # noqa: PLC0415

            console = Console()
            t = Table(
                title=f"Per-rule outcomes ({len(results)} rows, "
                      f"window={window}d, min n_after={min_records})",
                title_style="bold cyan",
            )
            t.add_column("Rule", style="cyan", overflow="fold")
            t.add_column("Error type", style="magenta")
            t.add_column("Surface", style="dim")
            t.add_column("n_before", justify="right")
            t.add_column("n_after", justify="right")
            t.add_column("Δ %", justify="right")
            t.add_column("Conf", justify="center")
            t.add_column("Recommend")
            for r in results[:50]:
                rid = r["rule_id"]
                if len(rid) > 50:
                    rid = rid[:47] + "..."
                dpct = r["delta_pct"]
                if dpct is None:
                    delta_str = "-"
                elif dpct < 0:
                    delta_str = f"[green]{dpct:+.0f}%[/green]"
                elif dpct > 0:
                    delta_str = f"[red]{dpct:+.0f}%[/red]"
                else:
                    delta_str = "0%"
                t.add_row(
                    rid, r["error_type"] or "-", r["target_surface"],
                    str(r["n_before"]), str(r["n_after"]),
                    delta_str, r["confidence"], r["recommend"],
                )
            console.print(t)
            console.print(
                "\n[dim]Δ < 0 = errors decreased after rule first appeared. "
                "Recommendations are hints — you decide.[/dim]"
            )
        except ImportError:
            for r in results[:50]:
                dpct = r["delta_pct"]
                dstr = f"{dpct:+.0f}%" if dpct is not None else "-"
                click.echo(
                    f"{r['rule_id']:<50s} {r['error_type']:<20s} "
                    f"n_before={r['n_before']:4d} n_after={r['n_after']:4d} "
                    f"Δ={dstr:>6s} conf={r['confidence']:<6s} "
                    f"{r['recommend']}"
                )
        return

    from sio.core.metrics.velocity import (
        compute_velocity_snapshot,
        get_velocity_trends,
    )

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
# Rule outcomes & audit surfaces (PRD sio_rule_outcomes_audit_2026-05-18.md)
# ---------------------------------------------------------------------------


def _resolve_rule_title(rule_id: str) -> str:
    """Best-effort: read first H1/H2 from rule file referenced by rule_id.

    rule_id format: ``<rules-path>#<sha[:12]>``. The current rules root is
    ``~/.claude/rules/``. Returns empty string on any failure — failure
    isolation per task spec.
    """
    try:
        path_part = rule_id.split("#", 1)[0]
        full = Path.home() / ".claude" / "rules" / path_part
        if not full.exists():
            return ""
        with open(full) as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("# ") or ls.startswith("## "):
                    return ls.lstrip("# ").strip()[:120]
                if len(ls) > 0 and not ls.startswith("---"):
                    return ls[:120]
    except Exception:
        return ""
    return ""


@cli.command("rule-outcomes")
@click.argument("rule_id", required=False)
@click.option(
    "--window",
    default=7,
    type=int,
    show_default=True,
    help="Pre/post window in days around rule first-seen.",
)
@click.option(
    "--since",
    default=None,
    help=(
        "Only consider error_records on/after this date (ISO-8601 or 'N days')."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@runlogged("rule-outcomes")
def rule_outcomes_cmd(rule_id, window, since, fmt):
    """Per-rule outcomes drill-down (PRD Surface 2).

    Omit RULE_ID to list all rules with outcomes data. Provide a rule_id
    (format ``tools/foo.md#<sha[:12]>``) to print the per-rule detail
    block with before/after counts, confidence, related sibling rules.
    """
    from sio.core.metrics.velocity import compute_rule_outcomes  # noqa: PLC0415

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        try:
            outcomes = compute_rule_outcomes(
                conn, rule_id_filter=rule_id, window_days=window
            )
        except Exception as e:  # noqa: BLE001
            click.echo(f"Error computing rule outcomes: {e}")
            return

    # Optional since-filter on first_seen
    if since:
        from datetime import datetime as _dt, timedelta as _td  # noqa: PLC0415
        cutoff = None
        s = since.strip()
        if s.endswith("days") or s.endswith("day"):
            try:
                n = int(s.split()[0])
                cutoff = (_dt.now() - _td(days=n)).isoformat()
            except Exception:
                cutoff = None
        else:
            cutoff = s
        if cutoff:
            outcomes = [o for o in outcomes if (o["first_seen"] or "") >= cutoff]

    if not outcomes:
        click.echo(
            "No rule outcomes found"
            + (f" for rule_id={rule_id!r}" if rule_id else "")
            + ". Active-rules stamping may be too recent — wait for "
            "organic churn (≈2 weeks) or rerun `sio mine`."
        )
        return

    if fmt == "json":
        click.echo(_json.dumps(outcomes, indent=2, default=str))
        return

    try:
        from rich.console import Console  # noqa: PLC0415
        from rich.panel import Panel  # noqa: PLC0415
        console = Console()
    except ImportError:
        console = None

    for o in outcomes:
        rid = o["rule_id"]
        title = _resolve_rule_title(rid) or "(no title)"
        lines = [
            f"Rule: {rid}",
            f"  Title:           {title}",
            f"  First seen:      {o['first_seen']}",
            f"  Target surface:  {o['target_surface']}",
            f"  Window:          {o['window_days']} days each side",
            "",
            f"  Before (n_total):  {o['n_before_total']}",
            f"  After  (n_total):  {o['n_after_total']}",
            (
                f"  Δ total:           "
                f"{o['delta_pct_total']:+.1f}% "
                f"(confidence: {o['confidence_total']})"
                if o["delta_pct_total"] is not None
                else f"  Δ total:           N/A (confidence: {o['confidence_total']})"
            ),
            f"  Recommend:         {o['recommend_total']}",
            "",
            "  By (error_type, surface):",
        ]
        for bd in o["by_type"]:
            dpct = bd["delta_pct"]
            dstr = f"{dpct:+.1f}%" if dpct is not None else "N/A"
            lines.append(
                f"    {bd['error_type']:<22s}  "
                f"n_before={bd['n_before']:>4d}  "
                f"n_after={bd['n_after']:>4d}  "
                f"Δ={dstr:>7s}  "
                f"conf={bd['confidence']:<6s}  "
                f"{bd['recommend']}"
            )
        if o["related_rules"]:
            lines.append("")
            lines.append("  Related rules active in same window (possible confounds):")
            for s in o["related_rules"][:10]:
                lines.append(f"    {s}")
            if len(o["related_rules"]) > 10:
                lines.append(f"    ... and {len(o['related_rules']) - 10} more")
        lines.append("")
        lines.append(
            "  Verdict (informational only — you decide): "
            f"{o['recommend_total']}"
        )

        body = "\n".join(lines)
        if console is not None:
            console.print(Panel(body, title=rid, border_style="cyan"))
        else:
            click.echo(body)
            click.echo("-" * 60)


@cli.command("rule-audit")
@click.argument("rule_id")
@click.option(
    "--samples",
    default=10,
    type=int,
    show_default=True,
    help="Number of representative errors to display from each side.",
)
@click.option(
    "--window",
    default=7,
    type=int,
    show_default=True,
    help="Pre/post window in days around rule first-seen.",
)
@click.option(
    "--judge",
    is_flag=True,
    default=False,
    help=(
        "Run LLM-as-judge on AFTER-window samples. PAID — requires --yes or "
        "interactive confirmation."
    ),
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip the cost-confirmation prompt (--judge only).",
)
@click.option(
    "--write-report",
    is_flag=True,
    default=False,
    help=(
        "Write the audit output to ~/.sio/audits/<rule_hash>_<ts>.md."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@runlogged("rule-audit")
def rule_audit_cmd(rule_id, samples, window, judge, yes, write_report, fmt):
    """Audit a single rule with concrete error samples (PRD Surface 3).

    Default: pulls SAMPLES error rows from before & after the rule's
    first-seen window and prints them. With --judge: invokes a paid LLM
    to score whether the rule's prevention_instructions actually apply
    to each AFTER-window error. Cost callout fires before any LLM call.
    """
    from sio.core.metrics.velocity import sample_errors_around_rule  # noqa: PLC0415

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    with _db_conn(db_path) as conn:
        try:
            data = sample_errors_around_rule(
                conn, rule_id, n_samples=samples, window_days=window
            )
        except Exception as e:  # noqa: BLE001
            click.echo(f"Error sampling errors: {e}")
            return

    if not data["first_seen"]:
        click.echo(
            f"Rule {rule_id} not found in error_records.active_rules. "
            "Check rule_id format (path#sha)."
        )
        return

    title = _resolve_rule_title(rule_id) or "(no title)"
    out_lines: list[str] = [
        f"Rule:        {rule_id}",
        f"Title:       {title}",
        f"First seen:  {data['first_seen']}",
        f"Samples:     {samples} (window ±{window}d)",
        "",
        f"BEFORE the rule landed ({len(data['before'])} samples):",
    ]
    for i, e in enumerate(data["before"], 1):
        snip = (e["error_text"] or "")[:120].replace("\n", " ")
        sid = (e["session_id"] or "")[:8]
        out_lines.append(
            f"  {i:>2d}. [{e['error_type']}] {snip}... "
            f"({e['timestamp']}, session={sid})"
        )
    out_lines.append("")
    out_lines.append(
        f"AFTER the rule landed ({len(data['after'])} samples — "
        "the rule did NOT prevent these):"
    )
    for i, e in enumerate(data["after"], 1):
        snip = (e["error_text"] or "")[:120].replace("\n", " ")
        sid = (e["session_id"] or "")[:8]
        out_lines.append(
            f"  {i:>2d}. [{e['error_type']}] {snip}... "
            f"({e['timestamp']}, session={sid})"
        )

    judge_result: dict | None = None
    if judge:
        # Cost callout per ~/.claude/rules/domains/cost-control.md
        from sio.core.cost import estimate_call  # noqa: PLC0415

        n = len(data["after"])
        # Conservative per-call estimate: ~1500 in tokens (rule body +
        # error text), ~150 out tokens (small JSON verdict).
        model = "gemini/gemini-flash-latest"
        per_call = estimate_call(model, in_tokens=1500, out_tokens=150)
        total = per_call * n
        click.echo("")
        click.echo(
            f"--judge mode: PAID LLM call."
        )
        click.echo(
            f"  Model:    {model}"
        )
        click.echo(
            f"  Samples:  {n}"
        )
        click.echo(
            f"  Estimated: ${total:.4f} (≈ ${per_call:.5f}/call × {n})"
        )
        if not yes:
            if not click.confirm("Proceed with paid --judge run?", default=False):
                click.echo("Aborted — no LLM call fired.")
                judge = False

    if judge:
        try:
            import time  # noqa: PLC0415
            from sio.core.cost import record_call  # noqa: PLC0415
            from sio.core.dspy.lm_factory import get_task_lm  # noqa: PLC0415

            # Read rule body for prevention_instructions context.
            path_part = rule_id.split("#", 1)[0]
            rule_path = Path.home() / ".claude" / "rules" / path_part
            rule_body = ""
            try:
                rule_body = rule_path.read_text()[:4000]
            except Exception:
                rule_body = "(rule file not readable)"

            lm = get_task_lm()
            matches = 0
            verdicts: list[dict] = []
            for e in data["after"]:
                prompt = (
                    "You are a strict judge. Given the rule below and an "
                    "error that occurred AFTER the rule was applied, answer "
                    "ONLY 'YES' or 'NO' on whether the rule directly applies "
                    "to this error.\n\n"
                    f"=== RULE ({rule_id}) ===\n{rule_body}\n\n"
                    f"=== ERROR ===\n"
                    f"type: {e['error_type']}\n"
                    f"text: {(e['error_text'] or '')[:600]}\n"
                    "\nAnswer YES or NO:"
                )
                t0 = time.monotonic()
                try:
                    resp = lm(prompt)
                    if isinstance(resp, list):
                        txt = (resp[0] if resp else "").strip().upper()
                    else:
                        txt = str(resp).strip().upper()
                except Exception as je:  # noqa: BLE001
                    txt = f"ERROR:{je}"
                latency = int((time.monotonic() - t0) * 1000)
                in_tok = max(1, len(prompt) // 4)
                out_tok = max(1, len(txt) // 4)
                cost = estimate_call(model, in_tok, out_tok)
                try:
                    record_call(
                        model=model, role="judge",
                        in_tokens=in_tok, out_tokens=out_tok,
                        cost_usd=cost, cmd="rule-audit",
                        latency_ms=latency,
                    )
                except Exception:
                    pass
                applies = txt.startswith("YES")
                if applies:
                    matches += 1
                verdicts.append({
                    "id": e["id"], "applies": applies, "raw": txt[:40]
                })

            pct = (matches / len(data["after"]) * 100) if data["after"] else 0.0
            judge_result = {
                "model": model,
                "samples": len(data["after"]),
                "matches": matches,
                "applicability_pct": pct,
                "verdicts": verdicts,
            }
            out_lines.append("")
            out_lines.append(
                f"--judge verdict: {matches}/{len(data['after'])} "
                f"({pct:.0f}%) of AFTER errors are still in-scope of this rule."
            )
            if pct >= 70:
                out_lines.append(
                    "  Hint: rule still relevant — errors are in-scope but "
                    "persisting. Audit *content* of the rule next."
                )
            elif pct <= 30:
                out_lines.append(
                    "  Hint: rule may be miscategorized — most AFTER errors "
                    "are out-of-scope. Candidate for deprecation. You decide."
                )
            else:
                out_lines.append("  Hint: mixed — investigate manually.")
        except Exception as e:  # noqa: BLE001
            out_lines.append("")
            out_lines.append(f"--judge failed: {e}")

    body = "\n".join(out_lines)

    if fmt == "json":
        click.echo(_json.dumps({
            "rule_id": rule_id, "title": title,
            "first_seen": data["first_seen"],
            "before": data["before"], "after": data["after"],
            "judge": judge_result,
        }, indent=2, default=str))
    else:
        click.echo(body)

    if write_report:
        try:
            import hashlib  # noqa: PLC0415
            from datetime import datetime as _dt  # noqa: PLC0415
            audits_dir = Path.home() / ".sio" / "audits"
            audits_dir.mkdir(parents=True, exist_ok=True)
            rh = hashlib.sha1(rule_id.encode()).hexdigest()[:10]
            ts = _dt.now().strftime("%Y%m%dT%H%M%S")
            out_path = audits_dir / f"{rh}_{ts}.md"
            out_path.write_text(body + "\n")
            click.echo(f"\n[report written: {out_path}]")
        except Exception as e:  # noqa: BLE001
            click.echo(f"\n[report write failed: {e}]")


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
@runlogged("violations")
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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


def _pick_violation_samples(
    matching: list[dict],
    n: int = 10,
) -> list[dict]:
    """Pick a varied subset of violations for downstream pattern extraction.

    Greedy session-diversity selection: walk newest first, take the first
    occurrence per session_id until we have ``n``. If the cap isn't
    reached we fill with additional examples from already-seen sessions
    (gives Phase 3 enough mass even when violations are concentrated in
    a few sessions).

    Inputs are dicts as produced by ``get_violation_report["violations"]``.
    """
    by_session: dict[str, dict] = {}
    overflow: list[dict] = []

    # Newest first
    sorted_violations = sorted(
        matching, key=lambda v: v.get("timestamp", ""), reverse=True
    )

    for v in sorted_violations:
        sid = v.get("session_id", "")
        if sid not in by_session:
            by_session[sid] = v
        else:
            overflow.append(v)

    samples = list(by_session.values())[:n]
    if len(samples) < n:
        samples.extend(overflow[: n - len(samples)])
    return samples


def _discover_rule_files() -> list[str]:
    """Discover CLAUDE.md + rule files to scan for imperative rules.

    Used by both ``sio violations`` and ``sio promote-rule`` so the
    same set of files surfaces in the violation report and is then
    pickable from by index.
    """
    from pathlib import Path  # noqa: PLC0415

    rule_file_paths: list[str] = []

    # CLAUDE.md candidates (user + cwd, dedup later)
    for candidate in (Path.home() / ".claude" / "CLAUDE.md", Path.cwd() / "CLAUDE.md"):
        if candidate.exists() and str(candidate) not in rule_file_paths:
            rule_file_paths.append(str(candidate))

    # ~/.claude/rules/ — markdown files
    user_rules = Path.home() / ".claude" / "rules"
    if user_rules.exists():
        for md in sorted(user_rules.rglob("*.md")):
            rule_file_paths.append(str(md))

    # Project-level rules/
    project_rules = Path.cwd() / "rules"
    if project_rules.exists():
        for md in sorted(project_rules.rglob("*.md")):
            if str(md) not in rule_file_paths:
                rule_file_paths.append(str(md))

    return rule_file_paths


# ---------------------------------------------------------------------------
# Promote a violated rule into a runtime PreToolUse hook
#   Phase 1 scaffold: resolves the rule by violation-report index and
#   prints what would be promoted. Subsequent phases (2-5) will collect
#   violation samples, extract the detection pattern via DSPy, generate
#   the hook script, register it in settings.json, and verify against
#   historical violations. See prds/prd-violated-rule-to-pretooluse-hook.md.
# ---------------------------------------------------------------------------


@cli.command(name="promote-rule")
@click.argument("rule_index", type=int)
@click.option(
    "--mode",
    type=click.Choice(["warn", "block"]),
    default="warn",
    help="warn: hook prints the rule + continues. block: hook prevents the call.",
)
@click.option(
    "--since",
    default=None,
    help="Only count violations after this ISO-8601 date.",
)
@click.option(
    "--write",
    is_flag=True,
    help=(
        "Actually write the hook script + register it in "
        "~/.claude/settings.json. Without this flag the command is a "
        "preview — extracts the detection pattern and shows what "
        "would be promoted, but writes nothing."
    ),
)
@runlogged("promote-rule")
def promote_rule(rule_index: int, mode: str, since: str | None, write: bool) -> None:
    """Promote a violated CLAUDE.md rule into a runtime PreToolUse hook.

    \b
    Takes the 1-based index from the `sio violations` report:

        sio violations          # prints the indexed report
        sio promote-rule 1      # promotes row #1 to a hook

    \b
    Modes:
      warn  (default) — hook prints the rule text + a soft warning, lets
                        the call proceed. Use until the violation count
                        is decisively shrinking.
      block           — hook prevents the violating tool call entirely.
                        Use only after a warn-mode soak.

    Phase 1 scaffold: looks up the rule + prints what would be promoted.
    Hook generation + registration land in subsequent phases. See
    prds/prd-violated-rule-to-pretooluse-hook.md.
    """
    from sio.mining.violation_detector import get_violation_report  # noqa: PLC0415

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        raise SystemExit(2)

    rule_file_paths = _discover_rule_files()
    if not rule_file_paths:
        click.echo("No instruction files found to scan.")
        click.echo(
            "  Checked: ~/.claude/CLAUDE.md, ./CLAUDE.md, ~/.claude/rules/, ./rules/"
        )
        raise SystemExit(2)

    with _db_conn(db_path) as conn:
        report = get_violation_report(conn, rule_file_paths, since=since)

    summary = report["violation_summary"]
    if not summary:
        click.echo(
            "All rules are being followed — nothing to promote. "
            "Run `sio violations` to confirm."
        )
        return

    if rule_index < 1 or rule_index > len(summary):
        click.echo(
            f"error: rule_index {rule_index} out of range (1-{len(summary)}). "
            f"Run `sio violations` to see available rules."
        )
        raise SystemExit(2)

    chosen = summary[rule_index - 1]
    # Find the matching Rule (text + source location) from the parse step.
    # The summary only carries text + counts, not the source file/line, so
    # re-parse to locate the canonical (file, line) for audit.
    from sio.mining.violation_detector import parse_rules  # noqa: PLC0415

    matched_rule = None
    for fp in rule_file_paths:
        for rule in parse_rules(fp):
            if rule.text == chosen["rule_text"]:
                matched_rule = rule
                break
        if matched_rule:
            break

    # Phase 2: collect representative violation samples for this rule.
    # Pull all violations matching the chosen rule_text, pick a varied
    # subset (different sessions, varied tool_inputs) — this is what
    # Phase 3's DSPy detection-pattern extractor will consume.
    matching = [
        v for v in report.get("violations", []) if v.get("rule_text") == chosen["rule_text"]
    ]
    samples = _pick_violation_samples(matching, n=10)

    # Phase 3: feed (rule_text, samples) to the DSPy extractor → structured
    # detection pattern. Lazy import so users without an LM configured can
    # still run promote-rule and see the samples (they'll just hit a clean
    # error on the extractor call).
    pattern = None
    pattern_error: str | None = None
    if samples:
        try:
            from sio.promote_rule import extract_detection  # noqa: PLC0415

            pattern = extract_detection(chosen["rule_text"], samples)
        except RuntimeError as exc:
            pattern_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            # LM call can fail for many reasons (rate-limit, auth, network).
            # Surface a short message and continue — the samples are already
            # rendered above so the user has something to act on.
            pattern_error = f"{type(exc).__name__}: {exc}"

    try:
        from rich.console import Console  # noqa: PLC0415
        from rich.panel import Panel  # noqa: PLC0415

        console = Console()
        body = (
            f"[bold]Rule #{rule_index}[/bold]: "
            f"\"{chosen['rule_text']}\"\n\n"
            f"[dim]Source:[/dim]   "
            + (
                f"{matched_rule.file_path}:{matched_rule.line_number}"
                if matched_rule
                else "(could not re-locate source — rule text may have changed)"
            )
            + f"\n[dim]Violations:[/dim]   {chosen['count']} across "
            f"{chosen['sessions']} session(s)\n"
            f"[dim]Last seen:[/dim]    {chosen.get('last_seen', '?')}\n"
            f"[dim]Mode:[/dim]         [yellow]{mode}[/yellow]"
        )
        console.print(Panel(body, title="Promotion target", expand=False))

        # Phase 2: render the collected samples that will feed Phase 3
        if samples:
            from rich.table import Table  # noqa: PLC0415

            samples_tbl = Table(
                title=f"Representative violations (sampled {len(samples)} of {len(matching)})",
                show_lines=False,
            )
            samples_tbl.add_column("#", style="bold", width=3)
            samples_tbl.add_column("session", max_width=12, style="dim")
            samples_tbl.add_column("tool", style="cyan")
            samples_tbl.add_column("input excerpt", overflow="fold", max_width=46)
            samples_tbl.add_column("error excerpt", overflow="fold", max_width=46)
            for i, s in enumerate(samples, 1):
                tin = (s.get("tool_input") or "").strip().replace("\n", " ")[:80]
                terr = (s.get("error_text") or "").strip().replace("\n", " ")[:80]
                sid = (s.get("session_id") or "")[:8]
                samples_tbl.add_row(
                    str(i), sid, s.get("tool_name") or "?", tin or "—", terr or "—"
                )
            console.print(samples_tbl)

        # Phase 3: render the extracted detection pattern (or the error)
        if pattern is not None:
            promotable_tag = (
                "[green]promotable[/green]"
                if pattern.promotable
                else "[red]not promotable[/red] (rule isn't structurally enforceable)"
            )
            detection_body = (
                f"[bold]Matcher tools:[/bold]      "
                f"{', '.join(pattern.matcher_tools) if pattern.matcher_tools else '(none)'}\n"
                f"[bold]Detection expr:[/bold]    [cyan]{pattern.detection_expr}[/cyan]\n"
                f"[bold]Rationale:[/bold]         {pattern.rationale}\n"
                f"[bold]Status:[/bold]            {promotable_tag}"
            )
            console.print(Panel(detection_body, title="Extracted detection pattern", expand=False))
        elif pattern_error:
            console.print(
                f"[red]extractor error:[/red] {pattern_error}"
            )

        # Phase 5: replay the detection against ALL historical violations
        # (not just the 10-sample subset Phase 3 saw) so the user gets a
        # real coverage signal before --write commits the install. Free —
        # no LM calls, just eval against the existing rows.
        verification = None
        if pattern is not None and pattern.promotable and matching:
            try:
                from sio.promote_rule import verify_against_history  # noqa: PLC0415

                verification = verify_against_history(pattern, matching)
                cov_pct = verification.coverage_rate * 100
                cov_color = (
                    "green" if cov_pct >= 60
                    else "yellow" if cov_pct >= 30
                    else "red"
                )
                cov_body = (
                    f"[bold]Catches:[/bold]         "
                    f"[{cov_color}]{verification.fires}[/{cov_color}] of "
                    f"{verification.total} historical violations "
                    f"([{cov_color}]{cov_pct:.0f}%[/{cov_color}])\n"
                    f"[bold]Sessions:[/bold]        {len(verification.by_session)} "
                    f"distinct\n"
                    f"[bold]Example fires:[/bold]   "
                    f"{len(verification.examples_fired)} sampled\n"
                    f"[bold]Example misses:[/bold]  "
                    f"{len(verification.examples_missed)} sampled"
                )
                console.print(
                    Panel(cov_body, title="Detection coverage on historical data", expand=False)
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]verifier skipped:[/yellow] {exc}")

        # Phase 4: actually write the hook + register it (only when --write)
        if write and pattern is not None and pattern.promotable and matched_rule is not None:
            # Coverage gate: warn loudly (but don't block) if the detection
            # catches less than 30% of the historical violations. Below that
            # threshold the LM probably extracted something off-target and
            # the user should review the detection_expr before committing.
            if verification is not None and verification.coverage_rate < 0.30:
                console.print(
                    f"[yellow]warning:[/yellow] coverage is only "
                    f"{verification.coverage_rate * 100:.0f}% — the detection "
                    f"caught {verification.fires} of {verification.total} historical "
                    f"violations. The LM's detection_expr may be off-target. "
                    f"Review the expression above and consider re-running with a "
                    f"different rule index, or hand-edit the hook script after "
                    f"writing."
                )
            try:
                from sio.promote_rule import generate_and_register  # noqa: PLC0415

                result = generate_and_register(
                    pattern,
                    rule_text=chosen["rule_text"],
                    rule_source_file=matched_rule.file_path,
                    rule_source_line=matched_rule.line_number,
                    mode=mode,
                )
                wrote_body = (
                    f"[bold]Hook script:[/bold]      "
                    f"[green]{result.hook_path}[/green]\n"
                    f"[bold]Settings.json:[/bold]    {result.settings_path}\n"
                    f"[bold]Slug:[/bold]             {result.slug}\n"
                    f"[bold]promoted_hooks id:[/bold] {result.promoted_hook_id}"
                )
                console.print(Panel(wrote_body, title="Promoted (mode=" + mode + ")", expand=False))
                console.print(
                    "[green]✓ Hook installed.[/green] Restart Claude Code so the "
                    "harness picks up the new PreToolUse registration. The hook "
                    "starts in [yellow]warn[/yellow] mode (logs to stderr, allows "
                    "the call); flip to block by re-running with [bold]--mode "
                    "block --write[/bold] after the violation count has decisively "
                    "shrunk."
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]hook generation failed:[/red] {exc}")
        elif write and pattern is None:
            console.print(
                "[red]cannot write:[/red] no detection pattern extracted "
                "(see extractor error above)."
            )
        elif write and pattern is not None and not pattern.promotable:
            console.print(
                "[red]cannot write:[/red] extractor flagged this rule as not "
                "structurally enforceable as a PreToolUse hook. Keep it as a "
                "text rule in CLAUDE.md."
            )
        elif not write:
            console.print(
                "[yellow]Preview only — pass [bold]--write[/bold] to install the "
                "hook + register it in ~/.claude/settings.json.[/yellow]"
            )
    except ImportError:
        click.echo(f"Rule #{rule_index}: {chosen['rule_text']}")
        if matched_rule:
            click.echo(
                f"  source:    {matched_rule.file_path}:{matched_rule.line_number}"
            )
        click.echo(
            f"  violations: {chosen['count']} across {chosen['sessions']} session(s)"
        )
        click.echo(f"  last seen:  {chosen.get('last_seen', '?')}")
        click.echo(f"  mode:       {mode}")
        click.echo(
            "Phase 1 scaffold — not yet writing. Phases 2-5 will land hook generation."
        )


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
@runlogged("budget")
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
@runlogged("dedupe")
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("report")
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
    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("promote-flow")
def promote_flow(flow_hash):
    """Promote a flow pattern to a Claude Code skill file.

    Takes a flow hash (from `sio flows` output) and generates a skill
    Markdown file in ~/.claude/skills/ based on the observed tool sequence.

    Examples:
        sio promote-flow abc123def456
    """
    from sio.clustering.grader import promote_flow_to_skill

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
@runlogged("discover")
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

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
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
            f"Database not found at {db_path}. Run 'sio init' first.",
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


# ---------------------------------------------------------------------------
# sio trend — pattern growth over time (PRD T6: sio_multi_hop_search_2026-04-24)
# ---------------------------------------------------------------------------
@cli.command("trend")
@click.option(
    "--weekly",
    "granularity",
    flag_value="weekly",
    default="weekly",
    help="Weekly buckets (default).",
)
@click.option(
    "--daily",
    "granularity",
    flag_value="daily",
    help="Daily buckets.",
)
@click.option(
    "--monthly",
    "granularity",
    flag_value="monthly",
    help="Monthly buckets.",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=10,
    help="Show top-N patterns by total error count over the window. Default: 10.",
)
@click.option(
    "--windows",
    "num_windows",
    type=int,
    default=6,
    help=(
        "How many time windows (weeks / days / months) to include. "
        "Default: 6. Counted backwards from now."
    ),
)
@click.option(
    "--pattern",
    "pattern_filter",
    default=None,
    help="Filter to a single pattern by id or slug (pattern_id). Optional.",
)
@click.option(
    "--grep",
    "grep_term",
    default=None,
    help="Filter patterns by substring match on description (comma-separated OR).",
)
@runlogged("trend")
def trend(granularity, top_n, num_windows, pattern_filter, grep_term):
    """Show growth / decline of pattern clusters over time.

    Uses the `error_records.timestamp` column joined via `pattern_errors` to
    bucket errors per pattern per time window. Produces a compact table with a
    trend arrow (↑ growing, ↓ shrinking, → stable) based on the last two windows.
    """
    from rich.console import Console
    from rich.table import Table

    db_path = os.environ.get("SIO_DB_PATH", os.path.expanduser("~/.sio/sio.db"))
    if not os.path.exists(db_path):
        click.echo("No database found. Run 'sio mine' first.")
        return

    # SQLite strftime tokens per granularity
    if granularity == "weekly":
        bucket_fmt = "%Y-W%W"
        interval_days = 7 * num_windows
        bucket_label = "Week"
    elif granularity == "daily":
        bucket_fmt = "%Y-%m-%d"
        interval_days = num_windows
        bucket_label = "Day"
    else:  # monthly
        bucket_fmt = "%Y-%m"
        interval_days = 31 * num_windows
        bucket_label = "Month"

    with _db_conn(db_path) as conn:
        cur = conn.cursor()

        # 1. Build the ordered list of buckets that currently fall in-window
        cur.execute(
            f"""
            SELECT DISTINCT strftime('{bucket_fmt}', e.timestamp) AS bucket
            FROM error_records e
            WHERE e.timestamp >= datetime('now', '-{interval_days} days')
            ORDER BY bucket
            """
        )
        buckets = [r[0] for r in cur.fetchall() if r[0]]
        # Keep only the last num_windows buckets (most recent)
        buckets = buckets[-num_windows:]

        if not buckets:
            click.echo(
                f"No error_records timestamps in the last {interval_days} days. "
                f"Run 'sio mine' or widen --windows."
            )
            return

        # 2. Per-pattern bucket counts
        where_clauses = []
        params: list = []
        if pattern_filter:
            if pattern_filter.isdigit():
                where_clauses.append("p.id = ?")
                params.append(int(pattern_filter))
            else:
                where_clauses.append("p.pattern_id = ?")
                params.append(pattern_filter)
        if grep_term:
            terms = [t.strip() for t in grep_term.split(",") if t.strip()]
            grep_sql = " OR ".join(["LOWER(p.description) LIKE ?"] * len(terms))
            where_clauses.append(f"({grep_sql})")
            for t in terms:
                params.append(f"%{t.lower()}%")
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cur.execute(
            f"""
            SELECT
                p.id AS pid,
                p.pattern_id AS slug,
                p.description AS description,
                strftime('{bucket_fmt}', e.timestamp) AS bucket,
                COUNT(*) AS err_count
            FROM patterns p
            JOIN pattern_errors pe ON pe.pattern_id = p.id AND pe.active = 1
            JOIN error_records e ON e.id = pe.error_id
            {where_sql}
            GROUP BY p.id, bucket
            HAVING bucket IS NOT NULL
            ORDER BY p.id, bucket
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        click.echo(
            "No pattern-error rows match. "
            "Run 'sio suggest' first to persist patterns, or widen filters."
        )
        return

    # 3. Pivot into {pattern_id: {bucket: count}}
    pivot: dict = {}
    totals: dict = {}
    meta: dict = {}
    for pid, slug, description, bucket, err_count in rows:
        if bucket not in buckets:
            continue  # drop old ones outside window
        pivot.setdefault(pid, {})[bucket] = err_count
        totals[pid] = totals.get(pid, 0) + err_count
        meta[pid] = (slug, description)

    if not pivot:
        click.echo("No rows in the requested window. Try --windows <larger>.")
        return

    # 4. Sort by total count desc, take top-N
    ranked = sorted(pivot.keys(), key=lambda k: totals[k], reverse=True)[:top_n]

    # 5. Render
    console = Console()
    table = Table(
        title=(
            f"SIO Trend — {granularity} × {len(buckets)} {bucket_label.lower()}s, "
            f"top {len(ranked)} patterns"
            + (f", pattern='{pattern_filter}'" if pattern_filter else "")
            + (f", grep='{grep_term}'" if grep_term else "")
        ),
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Pattern", max_width=48)
    for b in buckets:
        table.add_column(b, justify="right", no_wrap=True)
    table.add_column("Total", justify="right", style="bold")
    table.add_column("Δ", justify="center", style="bold")

    for i, pid in enumerate(ranked, 1):
        slug, description = meta[pid]
        row_cells = [str(i), (description or slug)[:48]]
        counts_this_row = [pivot[pid].get(b, 0) for b in buckets]
        for c in counts_this_row:
            row_cells.append(str(c) if c else "·")
        row_cells.append(str(totals[pid]))
        # Trend arrow: compare last two non-zero buckets
        if len(counts_this_row) >= 2:
            prev, curr = counts_this_row[-2], counts_this_row[-1]
            if curr > prev:
                arrow = f"[green]↑[/green] +{curr - prev}"
            elif curr < prev:
                arrow = f"[red]↓[/red] -{prev - curr}"
            else:
                arrow = "[dim]→[/dim]"
        else:
            arrow = "[dim]·[/dim]"
        row_cells.append(arrow)
        table.add_row(*row_cells)

    console.print(table)
    console.print(
        f"[dim]Data source: error_records.timestamp via pattern_errors (active=1). "
        f"Granularity={granularity}, window=last {interval_days} days.[/dim]"
    )


# Register Principle XIII run-log inspector (sio runs)
from sio.cli.runs import runs_cmd as _runs_cmd  # noqa: E402
cli.add_command(_runs_cmd)

# Register sio render (turn optimized module into deployable skill)
from sio.cli.render import render_cmd as _render_cmd  # noqa: E402
cli.add_command(_render_cmd)

# Register sio costs (XII transparency)
from sio.cli.costs import costs_cmd as _costs_cmd  # noqa: E402
cli.add_command(_costs_cmd)

# Register sio multi-train (parallel multi-surface optimizer driver)
from sio.cli.multi_train import multi_train_cmd as _multi_train_cmd  # noqa: E402
cli.add_command(_multi_train_cmd)

# Register sio reproduce (XV — reproducibility)
from sio.cli.reproduce import reproduce_cmd as _reproduce_cmd  # noqa: E402
cli.add_command(_reproduce_cmd)


@cli.command("gepa-status")
@click.option("--watch", is_flag=True, default=False,
              help="Re-print every 5s until process ends.")
def gepa_status_cmd(watch):
    """Show live GEPA progress for the most recent in-flight optimize run.

    Reads the latest runlog JSON, surfaces:
      - current iteration + best valset score
      - per-iteration score history (last 10)
      - iter idle time, parse_err / truncation counters
      - critical-tier warnings already fired

    Origin: 2026-05-18 paired-debate. Lets the agent answer "where are
    we?" mid-run without grepping stderr or guessing.
    """
    import glob as _glob  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import os as _os  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    def _render_once():
        runs_dir = _os.path.expanduser("~/.sio/runs")
        candidates = sorted(_glob.glob(f"{runs_dir}/*_optimize_*.json"),
                            key=_os.path.getmtime, reverse=True)
        if not candidates:
            click.echo("No optimize runs found.")
            return False
        latest = candidates[0]
        try:
            d = _json.load(open(latest))
        except Exception as exc:
            click.echo(f"Failed to read {latest}: {exc}")
            return False
        click.echo(f"\n=== {_os.path.basename(latest)} ===")
        click.echo(f"  cmd: {d.get('cmd')} argv: {d.get('argv',[])[:3]}...")
        click.echo(f"  pid: {d.get('pid')}  start: {d.get('start_ts')}")
        click.echo(f"  elapsed: {d.get('elapsed_sec', 0)/60:.1f} min")
        click.echo(f"  exit_code: {d.get('exit_code', 'running')}")
        # Pull the optimize stage's gepa_snapshot
        snap = None
        for s in d.get("stages", []):
            if s.get("gepa_snapshot"):
                snap = s["gepa_snapshot"]
                break
        if not snap:
            click.echo("  [no GEPA snapshot yet — either not a GEPA run or "
                       "first heartbeat hasn't fired]")
        else:
            click.echo(f"\n  GEPA iter: {snap['iter']}")
            if snap.get('iter_score') is not None:
                click.echo(f"  GEPA iter_score (this iter): {snap['iter_score']:.4f}")
            if snap.get('best') is not None:
                click.echo(f"  GEPA best_valset_so_far: {snap['best']:.4f}")
            if snap.get('trend'):
                arrow = {"up": "↑", "down": "↓", "flat": "→"}[snap['trend']]
                click.echo(f"  Trend (last 3 iters): {arrow} ({snap['trend']})")
            click.echo(f"  iter_idle: {snap.get('iter_idle_sec',0)}s")
            click.echo(f"  parse_err (5min): {snap.get('parse_errors_5min',0)}")
            click.echo(f"  truncations (5min): {snap.get('truncations_5min',0)}")
            hist = snap.get('history', [])
            if hist:
                click.echo("\n  Score history (iter, score):")
                for it, sc in hist[-10:]:
                    click.echo(f"    iter={it:>3}  score={sc:.4f}")
        # Show warnings emitted by abort tiers
        warns = d.get("warns", []) if isinstance(d.get("warns"), list) else []
        gepa_warns = [w for w in warns if isinstance(w, dict)
                      and any(k in (w.get('code') or '') for k in
                              ['GEPA_', 'REFLECTION_'])]
        if gepa_warns:
            click.echo("\n  ⚠ GEPA warnings emitted:")
            for w in gepa_warns:
                click.echo(f"    [{w.get('code')}] {w.get('msg','')[:200]}")
        return d.get("exit_code") is None  # True if still running

    if not watch:
        _render_once()
        return
    try:
        while True:
            still_running = _render_once()
            if not still_running:
                click.echo("\nRun finished or no in-flight optimize.")
                break
            _time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
