"""`sio runs` — inspect per-invocation run logs (Principle XIII clause 5).

Subcommands:
    sio runs                      list last 20
    sio runs --failed             only class=error
    sio runs --partial            only class=partial
    sio runs --cmd optimize       filter by command
    sio runs --since "7 days"     time-range filter
    sio runs <run_id>             full JSON of one record
    sio runs --tail               follow latest in real time
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import click

_RUNS_DIR = Path.home() / ".sio" / "runs"


def _parse_since(s: str) -> Optional[datetime]:
    """Parse '7 days', '24h', '90 min', 'today'."""
    s = s.strip().lower()
    now = datetime.now(timezone.utc)
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    parts = s.split()
    try:
        n = int(parts[0])
    except (ValueError, IndexError):
        return None
    unit = parts[1] if len(parts) > 1 else "min"
    if unit.startswith("day"):
        return now - timedelta(days=n)
    if unit.startswith(("hour", "hr", "h")):
        return now - timedelta(hours=n)
    if unit.startswith(("min", "m")):
        return now - timedelta(minutes=n)
    return None


def _load_runs(since: Optional[datetime] = None) -> list[dict]:
    if not _RUNS_DIR.exists():
        return []
    rows = []
    for p in sorted(_RUNS_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if since:
            try:
                start = datetime.fromisoformat(d.get("start_ts", "").replace("Z", "+00:00"))
                if start < since:
                    continue
            except (ValueError, TypeError):
                pass
        d["_file"] = str(p)
        rows.append(d)
    return rows


@click.command("runs")
@click.argument("run_id", required=False)
@click.option("--failed", is_flag=True, help="Only exit_class == error")
@click.option("--partial", is_flag=True, help="Only exit_class == partial")
@click.option("--cmd", "cmd_filter", default=None, help="Filter by command name")
@click.option("--since", default=None, help="e.g. '7 days', '24h', '90 min', 'today'")
@click.option("--limit", default=20, show_default=True, type=int,
              help="Max rows in list view")
@click.option("--tail", is_flag=True, help="Follow newest record in real time")
@click.option("--dspy", is_flag=True, help="When showing one run, also dump dspy capture")
def runs_cmd(run_id, failed, partial, cmd_filter, since, limit, tail, dspy):
    """Inspect SIO per-invocation run logs.

    With no RUN_ID, lists the most recent runs in a compact table.
    With RUN_ID (8-char hex prefix), shows the full JSON record.
    """
    if tail:
        return _tail_latest()

    if run_id:
        return _show_one(run_id, dspy=dspy)

    since_dt = _parse_since(since) if since else None
    rows = _load_runs(since=since_dt)
    if cmd_filter:
        rows = [r for r in rows if r.get("cmd") == cmd_filter]
    if failed:
        rows = [r for r in rows if r.get("exit_class") == "error"]
    if partial:
        rows = [r for r in rows if r.get("exit_class") == "partial"]
    rows = rows[:limit]

    if not rows:
        click.echo("No runs found.")
        return

    # Compact table — now with progress + ETA when available
    click.echo(
        f"{'run_id':<10}{'cmd':<22}{'class':<9}{'exit':<6}{'elapsed':<10}"
        f"{'progress':<14}{'eta':<8}{'warns':<7}{'errs':<6}start_ts"
    )
    click.echo("-" * 120)
    for r in rows:
        cls = r.get("exit_class") or "?"
        color = {"ok": "green", "partial": "yellow", "error": "red"}.get(cls, "white")
        # Extract progress from latest stage if present
        progress_str = "-"
        eta_str = "-"
        for s in r.get("stages", []):
            p = s.get("progress")
            if p:
                cur, tot = p.get("current"), p.get("total")
                if cur is not None and tot:
                    pct = (cur / tot) * 100
                    progress_str = f"{cur}/{tot} {pct:.0f}%"
                eta = p.get("eta_sec")
                if eta is not None:
                    eta_str = (
                        f"{eta//60}m{eta%60:02d}s" if eta < 3600
                        else f"{eta//3600}h{(eta%3600)//60}m"
                    )
        line = (
            f"{r.get('run_id','?'):<10}"
            f"{(r.get('cmd') or '?')[:21]:<22}"
            f"{cls:<9}"
            f"{(str(r.get('exit_code')) if r.get('exit_code') is not None else '?'):<6}"
            f"{(str(r.get('elapsed_sec')) if r.get('elapsed_sec') is not None else '?')+'s':<10}"
            f"{progress_str[:13]:<14}"
            f"{eta_str[:7]:<8}"
            f"{len(r.get('warnings', [])):<7}"
            f"{len(r.get('errors', [])):<6}"
            f"{r.get('start_ts','?')}"
        )
        click.secho(line, fg=color)


def _show_one(run_id_prefix: str, dspy: bool = False) -> None:
    # P1 fix 2026-05-16: validate prefix is hex (run_ids are hex digits) before
    # using in glob pattern. Prevents user-supplied wildcards (*, ?, [abc])
    # from matching unintended files.
    import re
    if not re.match(r"^[0-9a-f]{1,8}$", run_id_prefix):
        click.echo(
            f"Invalid run_id prefix '{run_id_prefix}'. "
            "Expected 1-8 hex characters (e.g. 'a592c6e2' or 'a5').",
            err=True,
        )
        sys.exit(1)
    matches = [p for p in _RUNS_DIR.glob(f"*_{run_id_prefix}*.json")]
    if not matches:
        # Fall back to scanning all and matching by run_id field
        for p in _RUNS_DIR.glob("*.json"):
            try:
                d = json.loads(p.read_text())
                if d.get("run_id", "").startswith(run_id_prefix):
                    matches.append(p)
            except (OSError, json.JSONDecodeError):
                continue
    if not matches:
        click.echo(f"No run found matching '{run_id_prefix}'", err=True)
        sys.exit(1)
    if len(matches) > 1:
        click.echo(f"Ambiguous prefix — matches: {[m.name for m in matches]}", err=True)
        sys.exit(1)

    path = matches[0]
    data = json.loads(path.read_text())
    click.echo(json.dumps(data, indent=2))

    if dspy:
        dspy_path = path.with_name(path.stem + "_dspy.jsonl")
        click.echo(f"\n--- DSPy capture: {dspy_path} ---")
        if dspy_path.exists():
            click.echo(dspy_path.read_text())
        else:
            click.echo("(no dspy capture file for this run yet)")


def _tail_latest() -> None:
    """Show new lines from the most recently modified run-log file."""
    click.echo("Tailing latest run log... Ctrl-C to stop.")
    seen = {}
    while True:
        files = sorted(_RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            time.sleep(1)
            continue
        newest = files[0]
        mtime = newest.stat().st_mtime
        if seen.get(str(newest)) != mtime:
            try:
                d = json.loads(newest.read_text())
            except json.JSONDecodeError:
                time.sleep(0.5)
                continue
            click.clear()
            click.echo(f"--- {newest.name} (refreshing) ---")
            click.echo(json.dumps(d, indent=2))
            seen[str(newest)] = mtime
        time.sleep(1)
