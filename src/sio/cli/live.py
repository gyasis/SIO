"""sio live — discover and read *in-progress* coding-agent sessions.

The gap this closes: ``sio search`` only sees INDEXED (finished) transcripts, so
a live, still-being-written session is invisible to it. ``sio live`` finds
sessions by real-time signal instead (recent file mtime) and reads their tail
directly, keyed by repo + branch so you can spot two sessions about to collide
on the same working tree.

Two commands:

    sio live ls              # list active sessions + flag collisions
    sio live show <id>       # print the tail of one session (locked to its id)
    sio live show <id> -f    # ...then follow it live (delegates to the tailer)

Claude Code is covered richly (its JSONL writes ``cwd`` + ``gitBranch`` on every
line, which nothing else in SIO reads); other harnesses are listed best-effort.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any

import click

HOME = Path.home()
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
GOOSE_SESSIONS = HOME / ".local" / "share" / "goose" / "sessions"
CODEX_SESSIONS = HOME / ".codex" / "sessions"
GEMINI_TMP = HOME / ".gemini" / "tmp"

# Env vars a harness might expose so a session can identify ITSELF as "you".
_SELF_ID_ENVS = ("SIO_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_SESSION")


# --------------------------- transcript reading --------------------------- #


def _tail_json_lines(path: Path, want: int = 8, chunk: int = 65536) -> list[dict]:
    """Parse up to ``want`` JSON objects from the END of a JSONL file.

    Reads only the trailing ``chunk`` bytes (grown if needed) instead of the
    whole file, so enumerating many live sessions stays cheap. Returns the
    parsed entries in file order (oldest first); unparseable lines are skipped.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    read = min(size, chunk)
    entries: list[dict] = []
    while True:
        with open(path, "rb") as fh:
            fh.seek(size - read)
            raw = fh.read(read)
        # Drop a leading partial line unless we're at the true start of file.
        text = raw.decode("utf-8", "replace")
        lines = text.split("\n")
        if read < size:
            lines = lines[1:]
        parsed = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if len(parsed) >= want or read >= size:
            entries = parsed[-want:]
            break
        read = min(size, read * 4)  # widen the window and retry
    return entries


def _count_lines(path: Path) -> int:
    """Fast newline count (best-effort message count)."""
    try:
        total = 0
        with open(path, "rb") as fh:
            while True:
                buf = fh.read(1 << 20)
                if not buf:
                    break
                total += buf.count(b"\n")
        return total
    except OSError:
        return 0


_SALIENT_ARGS = (
    "command", "file_path", "path", "pattern", "query", "description",
    "prompt", "url", "old_string",
)


def _content_snippet(entry: dict[str, Any]) -> str:
    """Human-readable one-liner for a JSONL entry — text, tool call, OR result.

    The shared adapter only joins ``text`` blocks, so tool_use / tool_result /
    user-tool-return turns render blank. For a "catch me up" tail we dig a bit:
    show tool names + their salient arg, and tool-result text.
    """
    msg = entry.get("message") or {}
    blocks = msg.get("content")
    parts: list[str] = []
    if isinstance(blocks, str):
        parts.append(blocks)
    elif isinstance(blocks, list):
        for b in blocks:
            if not isinstance(b, dict):
                parts.append(str(b))
                continue
            kind = b.get("type")
            if kind == "text":
                parts.append(b.get("text", ""))
            elif kind == "tool_use":
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                key = next((k for k in _SALIENT_ARGS if k in inp), None)
                arg = str(inp.get(key, "")).replace("\n", " ")[:90] if key else ""
                parts.append(f"{b.get('name', 'tool')}({arg})")
            elif kind == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    parts.append(
                        " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                    )
                elif isinstance(c, str):
                    parts.append(c)
    text = " ".join(p for p in parts if p).strip()
    if not text:  # fall back to the top-level tool result payload
        tur = entry.get("toolUseResult")
        if isinstance(tur, str):
            text = tur
        elif isinstance(tur, dict):
            text = str(tur.get("stdout") or tur.get("content") or "")
    return " ".join(text.split())


def _last_event_label(entry: dict[str, Any]) -> str:
    """Short 'what happened last' label from a parsed JSONL entry."""
    msg = entry.get("message") or {}
    role = entry.get("type") or msg.get("role") or "?"
    blocks = msg.get("content")
    if isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                return f"[{b.get('name', 'tool')}]"
    return str(role)


# ------------------------------ git awareness ------------------------------ #


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


_REPO_CACHE: dict[str, dict[str, str | None]] = {}


def _repo_info(cwd: str | None) -> dict[str, str | None]:
    """Resolve a working directory to (toplevel, common_dir, branch).

    ``toplevel`` is the working tree root (same-toplevel sessions edit the same
    files → collision). ``common_dir`` identifies the underlying repository
    (shared even across worktrees). Cached per cwd to bound git calls.
    """
    if not cwd:
        return {"toplevel": None, "common_dir": None, "branch": None}
    if cwd in _REPO_CACHE:
        return _REPO_CACHE[cwd]
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    common = _git(cwd, "rev-parse", "--git-common-dir")
    if common and not os.path.isabs(common):
        common = os.path.normpath(os.path.join(toplevel or cwd, common))
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    info = {"toplevel": toplevel, "common_dir": common, "branch": branch}
    _REPO_CACHE[cwd] = info
    return info


# ----------------------------- session probes ----------------------------- #


def _claude_session(path: Path) -> dict[str, Any]:
    """Rich probe of one Claude JSONL session (reads the unused cwd/gitBranch)."""
    entries = _tail_json_lines(path, want=8)
    cwd = branch = sid = None
    for e in reversed(entries):  # newest-first: take the first that carries it
        cwd = cwd or e.get("cwd")
        branch = branch or e.get("gitBranch")
        sid = sid or e.get("sessionId")
    last = _last_event_label(entries[-1]) if entries else "?"
    st = path.stat()
    return {
        "agent": "claude",
        "native_id": sid or path.stem,
        "path": str(path),
        "cwd": cwd,
        "jsonl_branch": branch,
        "mtime": st.st_mtime,
        "msgs": _count_lines(path),
        "last": last,
    }


def _goose_cwd(path: Path) -> str | None:
    """Goose stores working_dir on its first metadata line — best-effort."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
        return (json.loads(first) or {}).get("working_dir")
    except (OSError, json.JSONDecodeError):
        return None


def _simple_session(path: Path, agent: str, cwd: str | None) -> dict[str, Any]:
    st = path.stat()
    return {
        "agent": agent,
        "native_id": path.stem,
        "path": str(path),
        "cwd": cwd,
        "jsonl_branch": None,
        "mtime": st.st_mtime,
        "msgs": 0,
        "last": "—",
    }


def _recent(dirpath: Path, glob: str, cutoff: float) -> list[Path]:
    if not dirpath.exists():
        return []
    out = []
    for fp in dirpath.rglob(glob):
        try:
            if fp.stat().st_mtime >= cutoff:
                out.append(fp)
        except OSError:
            continue
    return out


def discover_sessions(minutes: int) -> list[dict[str, Any]]:
    """Enumerate sessions whose transcript was written in the last ``minutes``.

    Claude sessions are probed richly; other harnesses are listed best-effort
    (cwd only where trivially available, no git/collision resolution).
    """
    cutoff = time.time() - minutes * 60
    rows: list[dict[str, Any]] = []
    for fp in _recent(CLAUDE_PROJECTS, "*.jsonl", cutoff):
        # Sub-agent transcripts (<sid>/subagents/agent-*.jsonl) carry the PARENT
        # session id — they are the same session, not a concurrent one. Skip.
        if f"{os.sep}subagents{os.sep}" in str(fp):
            continue
        rows.append(_claude_session(fp))
    for fp in _recent(GOOSE_SESSIONS, "*.jsonl", cutoff):
        rows.append(_simple_session(fp, "goose", _goose_cwd(fp)))
    for fp in _recent(CODEX_SESSIONS, "rollout-*.json", cutoff):
        rows.append(_simple_session(fp, "codex", None))
    for fp in _recent(GEMINI_TMP, "session-*.json", cutoff):
        rows.append(_simple_session(fp, "gemini", None))

    # Collapse resume/compact continuations that share a session id (newest wins),
    # so one logical session is one row and never collides with itself.
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["agent"], r["native_id"])
        cur = deduped.get(key)
        if cur is None or r["mtime"] > cur["mtime"]:
            deduped[key] = r
    rows = list(deduped.values())

    # Resolve repo/branch and attach collision keys.
    for r in rows:
        info = _repo_info(r["cwd"])
        r["toplevel"] = info["toplevel"]
        r["common_dir"] = info["common_dir"]
        # Live working-tree HEAD is authoritative for collision reasoning (all
        # sessions in one tree share its branch); the transcript value can be
        # stale, so it is only the fallback when the dir isn't a live git repo.
        r["branch"] = info["branch"] or r["jsonl_branch"]
    # A toplevel shared by >1 live session = collision.
    counts: dict[str, int] = {}
    for r in rows:
        if r["toplevel"]:
            counts[r["toplevel"]] = counts.get(r["toplevel"], 0) + 1
    for r in rows:
        r["collision"] = bool(r["toplevel"] and counts[r["toplevel"]] > 1)
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


# -------------------------------- rendering -------------------------------- #


def _self_id() -> str | None:
    for env in _SELF_ID_ENVS:
        val = os.environ.get(env)
        if val:
            return val.split(":", 1)[-1]  # tolerate agent:native form
    return None


def _fmt_age(mtime: float) -> str:
    s = max(0, int(time.time() - mtime))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _short_cwd(cwd: str | None) -> str:
    if not cwd:
        return "—"
    disp = cwd.replace(str(HOME), "~")
    if len(disp) > 34:
        disp = "…" + disp[-33:]
    return disp


def _render_table(rows: list[dict[str, Any]]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    self_id = _self_id()
    self_top = _repo_info(os.getcwd())["toplevel"]

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col in ("", "agent", "id", "repo", "branch", "cwd", "age", "msgs", "last"):
        table.add_column(col)
    for r in rows:
        top = r["toplevel"]
        if self_id and (r["native_id"].endswith(self_id) or self_id in r["native_id"]):
            mark, style = "← you", "cyan"
        elif r["collision"]:
            mark, style = "⚠", "bold red"
        elif top and top == self_top:
            mark, style = "◆", "yellow"
        else:
            mark, style = "", None
        repo = Path(r["common_dir"]).parent.name if r["common_dir"] else "—"
        table.add_row(
            mark,
            r["agent"],
            r["native_id"][:7],
            repo,
            r["branch"] or "—",
            _short_cwd(r["cwd"]),
            _fmt_age(r["mtime"]),
            str(r["msgs"] or "—"),
            r["last"],
            style=style,
        )
    console.print(table)

    # Collision summary — group live sessions sharing a working tree.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["collision"]:
            groups.setdefault(r["toplevel"], []).append(r)
    if groups:
        console.print()
        console.print("[bold red]⚠ Collisions — same working tree, live:[/bold red]")
        for top, members in groups.items():
            branches = sorted({m["branch"] or "?" for m in members})
            ids = ", ".join(m["native_id"][:7] for m in members)
            console.print(
                f"  {_short_cwd(top)}  branch={'/'.join(branches)}  →  {ids}"
            )
        console.print(
            "  [dim]Two sessions in one checkout on the same branch WILL "
            "collide — split to a worktree or coordinate.[/dim]"
        )


# --------------------------------- commands -------------------------------- #


@click.group("live")
def live_cmd() -> None:
    """Discover and read in-progress (live) coding-agent sessions."""


@live_cmd.command("ls")
@click.option(
    "--minutes",
    "-m",
    default=60,
    show_default=True,
    help="Consider a session live if its transcript changed within N minutes.",
)
@click.option("--repo", help="Filter to sessions whose working tree is under PATH.")
@click.option("--as-json", "as_json", is_flag=True, help="Machine-readable output.")
def live_ls(minutes: int, repo: str | None, as_json: bool) -> None:
    """List currently-active sessions across harnesses and flag collisions."""
    rows = discover_sessions(minutes)
    if repo:
        want = os.path.realpath(repo)
        rows = [
            r
            for r in rows
            if r["toplevel"] and os.path.realpath(r["toplevel"]).startswith(want)
        ]
    if as_json:
        keep = (
            "agent", "native_id", "cwd", "branch", "toplevel",
            "common_dir", "collision", "mtime", "msgs", "last",
        )
        click.echo(json.dumps([{k: r[k] for k in keep} for r in rows], indent=2))
        return
    if not rows:
        click.echo(f"No sessions active in the last {minutes} min.")
        return
    _render_table(rows)


def _resolve_claude_partial(native: str) -> Path | None:
    """Locate a Claude session by exact or partial id (from `ls` short ids)."""
    exact = sorted(CLAUDE_PROJECTS.rglob(f"{native}.jsonl"))
    if exact:
        return exact[0]
    fuzzy = sorted(CLAUDE_PROJECTS.rglob(f"*{native}*.jsonl"))
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        raise click.ClickException(
            f"'{native}' matches {len(fuzzy)} sessions — use a longer id."
        )
    return None


def _locate(session: str):
    """Resolve a session arg (bare/partial/handle/path) → (agent, adapter, manifest)."""
    from sio.adapters.factory import adapter_for, manifest_from_handle
    from sio.core.session_handle import coerce_session_input, parse_handle

    handle = coerce_session_input(session)
    agent, native = parse_handle(handle)
    if agent == "claude":
        path = _resolve_claude_partial(native)
        if path is None:
            raise click.ClickException(f"Session not found: {session}")
        handle = f"claude:{path.stem}"
    try:
        manifest = manifest_from_handle(handle)
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc
    if manifest is None:
        raise click.ClickException(f"Session not found: {session}")
    return agent, adapter_for(agent), manifest


def _stream(adapter, manifest, tail: int, follow: bool, tools_only: bool) -> None:
    """Emit the last ``tail`` events, then optionally follow new ones."""
    def _emit(ev) -> None:
        if tools_only and not ev.tool:
            return
        tag = f"[{ev.tool}]" if ev.tool else ev.role
        body = _content_snippet(ev.raw) or ev.content
        click.echo(f"{ev.ts[:19]} {tag}: {body[:180]}")

    for ev in deque(adapter.get_events(manifest), maxlen=tail):
        _emit(ev)
    if follow:
        try:
            for ev in adapter.get_live_stream(manifest, from_start=False):
                _emit(ev)
        except KeyboardInterrupt:
            click.echo("\nStopped.")
        except NotImplementedError as exc:
            raise click.ClickException(str(exc)) from exc


@live_cmd.command("show")
@click.argument("session")
@click.option("--tail", "-n", default=40, show_default=True, help="Show last N events.")
@click.option("--follow", "-f", is_flag=True, help="Keep streaming new events.")
@click.option("--tools-only", is_flag=True, help="Only surface tool_use events.")
def live_show(session: str, tail: int, follow: bool, tools_only: bool) -> None:
    """Print the TAIL of one session, locked to its id (bare/partial/handle/path).

    Reuses the same locator + live tailer as ``sio watch``; the default is a
    one-shot snapshot of the last N events (the "catch me up on this session"
    read), with ``--follow`` to keep streaming.
    """
    _agent, adapter, manifest = _locate(session)
    click.echo(f"── {manifest.handle}  (last {tail}){'  · following' if follow else ''}")
    _stream(adapter, manifest, tail, follow, tools_only)


@live_cmd.command("attach")
@click.argument("session")
@click.option(
    "--context",
    "-c",
    "tail",
    default=15,
    show_default=True,
    help="Lines of prior context to print before following.",
)
@click.option("--tools-only", is_flag=True, help="Only surface tool_use events.")
def live_attach(session: str, tail: int, tools_only: bool) -> None:
    """ATTACH to a live session from another session and follow it read-only.

    The companion to ``sio live ls``: find the peer session, then attach to keep
    watching it as it works — a short context tail, then a live stream. This is
    a read-only observer (it never writes to the attached session); Ctrl-C to
    detach. Live follow currently supports Claude sessions.
    """
    _agent, adapter, manifest = _locate(session)
    click.echo(f"⇢ attached to {manifest.handle}  ({tail} lines context · Ctrl-C to detach)")
    _stream(adapter, manifest, tail, follow=True, tools_only=tools_only)
