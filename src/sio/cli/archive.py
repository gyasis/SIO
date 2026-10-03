"""sio archive — SIO's own durable copy of every coding-agent session store.

Harnesses delete their own history (Claude Code keeps 30 days by default), so SIO
keeps the raw session files itself and never deletes them.

Examples:
    sio archive sync            # one pass; exit 1 if any file failed
    sio archive sync --json     # machine-readable status
    sio archive status          # last run + per-agent manifest totals
    sio archive install         # run sync hourly (systemd --user / launchd)
"""
from __future__ import annotations

import json
import subprocess

import click

from sio.archive import sync as archive_sync


@click.group("archive")
def archive_cmd() -> None:
    """Keep a never-deleted copy of every agent's raw session files (~/.sio/archive)."""


@archive_cmd.command("sync")
@click.option("--json", "as_json", is_flag=True, help="Print the status as JSON.")
def sync_cmd(as_json: bool) -> None:
    """Copy new and grown session files into the archive. Never deletes."""
    status = archive_sync.sync()
    if as_json:
        click.echo(json.dumps(status, indent=2))
    else:
        click.echo(f"archive: {status['archive']}  ({status['bytes'] / 1e9:.2f} GB, "
                   f"{status['seconds']}s)")
        for src in status["sources"]:
            counts = ", ".join(f"{k}={v}" for k, v in sorted(src["counts"].items())) or "-"
            click.echo(f"  {src['state']:6} {src['name']:22} {counts}")
            for err in src["errors"]:
                click.echo(f"         ERROR {err}", err=True)
    if not status["ok"]:
        raise SystemExit(1)


@archive_cmd.command("install")
@click.option("--interval", default=60, show_default=True, type=click.IntRange(min=5),
              help="Minutes between runs.")
@click.option("--dry-run", is_flag=True, help="Show what would be written and run.")
def install_cmd(interval: int, dry_run: bool) -> None:
    """Schedule `sio archive sync` (systemd --user on Linux, launchd on macOS)."""
    from sio.archive import schedule  # noqa: PLC0415

    try:
        actions = schedule.install(interval_minutes=interval, dry_run=dry_run)
    except (FileNotFoundError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from None
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"{' '.join(exc.cmd)} failed: {(exc.stderr or '').strip()}"
        ) from None
    for line in actions:
        click.echo(line)


@archive_cmd.command("status")
@click.option("--json", "as_json", is_flag=True, help="Print the status as JSON.")
def status_cmd(as_json: bool) -> None:
    """Show the last archive run and how many files each agent has archived."""
    try:
        info = archive_sync.status()
    except FileNotFoundError:
        raise click.ClickException(
            f"no archive run recorded under {archive_sync.archive_root()}"
            " -- run `sio archive sync`"
        ) from None
    if as_json:
        click.echo(json.dumps(info, indent=2))
        return
    last = info["last_run"]
    click.echo(f"last run: {last['finished']}  ok={last['ok']}  "
               f"{last['bytes'] / 1e9:.2f} GB at {last['archive']}")
    for row in info["manifest"]:
        click.echo(f"  {row['agent']:22} files={row['files']:<6} "
                   f"gone_at_source={row['source_gone']:<5} versions={row['versions']}")
