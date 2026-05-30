#!/usr/bin/env python3
"""
session-search — unified cross-harness coding-agent session search.

Searches the on-disk session history of every coding-agent harness installed
on this box (claude, codex, goose, opencode, gemini, aider) using ONE pattern
and ONE output schema. Defaults to Claude-native fast mode (ripgrep
short-circuit, ~189ms) to preserve hardwired callers.

Output schema (JSONL — one object per match):
    {
      "agent":        "claude|codex|goose|opencode|gemini|aider",
      "session_id":   "<agent-native id>",
      "ts":           "<ISO-8601 UTC, best-effort>",
      "role":         "user|assistant|tool|system|info|unknown",
      "content":      "<text, capped 2000 chars>",
      "source_path":  "<absolute path>",
      "metadata":     { ... agent-specific ... }
    }

Usage:
    session-search "pattern"                      # claude only, fast path
    session-search "pattern" --all                # claude JSONL + SpecStory + backups
    session-search "pattern" --specstory          # SpecStory MD only
    session-search "pattern" --backups            # claude backups only
    session-search "pattern" --agent goose        # single non-Claude harness
    session-search "pattern" --agent all          # fan out across all 6 harnesses
    session-search "pattern" --recent 7           # files modified within last N days
    session-search "pattern" --files              # emit unique source paths only
    session-search "pattern" --count              # per-file match counts
    session-search "pattern" --context 3          # 3 lines of context around match
    session-search "pattern" --clean              # un-escape JSON in text output
    session-search --list-agents                  # inventory of on-disk presence

Exit codes: 0 ok, 1 usage error, 2 no matches.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

HOME = Path.home()
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
CLAUDE_BACKUPS = HOME / ".claude" / "backups"
DEV_ROOT = HOME / "dev"


# ------------------------- record + helpers ------------------------- #


@dataclass
class Record:
    agent: str
    session_id: str
    ts: str
    role: str
    content: str
    source_path: str
    metadata: dict = field(default_factory=dict)
    line: int = 0  # 1-based line number in source_path; 0 when N/A


def _matches(text: str, pattern: str, case_sensitive: bool) -> bool:
    if not text:
        return False
    if case_sensitive:
        return pattern in text
    return pattern.lower() in text.lower()


def _file_within(path: Path, cutoff_epoch: float | None) -> bool:
    if cutoff_epoch is None:
        return True
    try:
        return path.stat().st_mtime >= cutoff_epoch
    except OSError:
        return False


def _iso(epoch: float | int | None, fallback: str = "") -> str:
    if epoch is None:
        return fallback
    try:
        if epoch > 1e12:
            epoch = epoch / 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return fallback


def _clean(text: str) -> str:
    """Un-escape common JSON escape sequences for readable text output."""
    return (
        text.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


# --------------------- claude parsers (3 sources) --------------------- #


def _iter_claude_jsonl(
    root: Path,
    agent_label: str,
    source_kind: str,
    pattern: str,
    cs: bool,
    cutoff: float | None,
) -> Iterator[Record]:
    """Shared parser for ~/.claude/projects and ~/.claude/backups JSONL files."""
    if not root.exists():
        return
    for jsonl in root.rglob("*.jsonl"):
        if not _file_within(jsonl, cutoff):
            continue
        session_id = jsonl.stem
        try:
            with jsonl.open(encoding="utf-8", errors="replace") as fh:
                for lineno, raw in enumerate(fh, start=1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    msg = entry.get("message") or {}
                    role = entry.get("type") or msg.get("role") or "unknown"
                    content_blocks = msg.get("content")
                    if isinstance(content_blocks, list):
                        text = " ".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in content_blocks
                        )
                    else:
                        text = str(content_blocks or entry.get("text", ""))
                    if _matches(text, pattern, cs):
                        yield Record(
                            agent=agent_label,
                            session_id=session_id,
                            ts=entry.get("timestamp", ""),
                            role=role,
                            content=text[:2000],
                            source_path=str(jsonl),
                            metadata={
                                "uuid": entry.get("uuid", ""),
                                "source_kind": source_kind,
                            },
                            line=lineno,
                        )
        except OSError:
            continue


def search_claude(pattern: str, cs: bool, cutoff: float | None) -> Iterator[Record]:
    yield from _iter_claude_jsonl(
        CLAUDE_PROJECTS, "claude", "jsonl", pattern, cs, cutoff
    )


def search_claude_backups(
    pattern: str, cs: bool, cutoff: float | None
) -> Iterator[Record]:
    yield from _iter_claude_jsonl(
        CLAUDE_BACKUPS, "claude", "backup", pattern, cs, cutoff
    )


def search_claude_specstory(
    pattern: str, cs: bool, cutoff: float | None
) -> Iterator[Record]:
    """SpecStory MD files under any ~/dev/<repo>/.specstory/ directory.

    Matches the bash legacy: `find $DEV -path '*/.specstory/*.md'` — any .md
    under any .specstory dir (including history/ subdirs).
    """
    if not DEV_ROOT.exists():
        return
    for md in DEV_ROOT.rglob("*.md"):
        if "/.specstory/" not in str(md):
            continue
        if not _file_within(md, cutoff):
            continue
        try:
            text_all = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text_all.splitlines()
        session_id = md.stem
        for lineno, line in enumerate(lines, start=1):
            if _matches(line, pattern, cs):
                yield Record(
                    agent="claude",
                    session_id=session_id,
                    ts=_iso(md.stat().st_mtime),
                    role="specstory",
                    content=line[:2000],
                    source_path=str(md),
                    metadata={"source_kind": "specstory"},
                    line=lineno,
                )


# --------------------- non-Claude parsers --------------------- #


def search_codex(pattern: str, cs: bool, cutoff: float | None) -> Iterator[Record]:
    hist = HOME / ".codex" / "history.jsonl"
    if hist.exists() and _file_within(hist, cutoff):
        try:
            with hist.open(encoding="utf-8", errors="replace") as fh:
                for lineno, raw in enumerate(fh, start=1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    text = entry.get("text", "")
                    if _matches(text, pattern, cs):
                        yield Record(
                            agent="codex",
                            session_id=entry.get("session_id", ""),
                            ts=_iso(entry.get("ts")),
                            role="user",
                            content=text[:2000],
                            source_path=str(hist),
                            metadata={"store": "history"},
                            line=lineno,
                        )
        except OSError:
            pass

    sessions_dir = HOME / ".codex" / "sessions"
    if not sessions_dir.exists():
        return
    for fp in sessions_dir.glob("rollout-*.json"):
        if not _file_within(fp, cutoff):
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        ts = (data.get("session") or {}).get("timestamp", "")
        session_id = fp.stem
        text = json.dumps(data, ensure_ascii=False)
        if _matches(text, pattern, cs):
            yield Record(
                agent="codex",
                session_id=session_id,
                ts=ts,
                role="session",
                content=text[:2000],
                source_path=str(fp),
                metadata={"store": "rollout"},
            )


def search_goose(pattern: str, cs: bool, cutoff: float | None) -> Iterator[Record]:
    root = HOME / ".local" / "share" / "goose" / "sessions"
    if not root.exists():
        return
    for fp in sorted(root.glob("*.jsonl")):
        if not _file_within(fp, cutoff):
            continue
        session_id = fp.stem
        try:
            with fp.open(encoding="utf-8", errors="replace") as fh:
                first = True
                meta = {}
                for lineno, raw in enumerate(fh, start=1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if first:
                        meta = {
                            "working_dir": entry.get("working_dir"),
                            "description": entry.get("description"),
                            "total_tokens": entry.get("total_tokens"),
                        }
                        first = False
                        text = entry.get("description", "") or ""
                        if _matches(text, pattern, cs):
                            yield Record(
                                agent="goose",
                                session_id=session_id,
                                ts="",
                                role="info",
                                content=text[:2000],
                                source_path=str(fp),
                                metadata=meta,
                                line=lineno,
                            )
                        continue
                    role = entry.get("role", "unknown")
                    blocks = entry.get("content") or []
                    text = " ".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in blocks
                    )
                    if _matches(text, pattern, cs):
                        yield Record(
                            agent="goose",
                            session_id=session_id,
                            ts=_iso(entry.get("created")),
                            role=role,
                            content=text[:2000],
                            source_path=str(fp),
                            metadata=meta,
                            line=lineno,
                        )
        except OSError:
            continue


def search_opencode(pattern: str, cs: bool, cutoff: float | None) -> Iterator[Record]:
    db = HOME / ".local" / "share" / "opencode" / "opencode.db"
    if not db.exists() or not _file_within(db, cutoff):
        return
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, session_id, time_created, data FROM message "
            "ORDER BY time_created DESC LIMIT 5000"
        )
        for row in cursor:
            data_str = row["data"] or ""
            if _matches(data_str, pattern, cs):
                role = "unknown"
                content = data_str
                try:
                    blob = json.loads(data_str)
                    if isinstance(blob, dict):
                        role = blob.get("role", "unknown")
                        content = blob.get("content", data_str)
                        if isinstance(content, list):
                            content = " ".join(
                                p.get("text", "") if isinstance(p, dict) else str(p)
                                for p in content
                            )
                        elif not isinstance(content, str):
                            content = json.dumps(content)
                except json.JSONDecodeError:
                    pass
                yield Record(
                    agent="opencode",
                    session_id=row["session_id"],
                    ts=_iso(row["time_created"]),
                    role=role,
                    content=str(content)[:2000],
                    source_path=str(db),
                    metadata={"message_id": row["id"], "table": "message"},
                )
        conn.close()
    except sqlite3.Error:
        pass


def search_gemini(pattern: str, cs: bool, cutoff: float | None) -> Iterator[Record]:
    root = HOME / ".gemini" / "tmp"
    if not root.exists():
        return
    for fp in root.rglob("session-*.json"):
        if not _file_within(fp, cutoff):
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        session_id = data.get("sessionId", fp.stem)
        proj = data.get("projectHash", "")
        for msg in data.get("messages", []) or []:
            text = msg.get("content", "")
            if not isinstance(text, str):
                text = json.dumps(text)
            if _matches(text, pattern, cs):
                msg_type = msg.get("type", "unknown")
                role = {"gemini": "assistant", "user": "user", "info": "info"}.get(
                    msg_type, msg_type
                )
                yield Record(
                    agent="gemini",
                    session_id=session_id,
                    ts=msg.get("timestamp", ""),
                    role=role,
                    content=text[:2000],
                    source_path=str(fp),
                    metadata={"project_hash": proj},
                )


def search_aider(pattern: str, cs: bool, cutoff: float | None) -> Iterator[Record]:
    if not DEV_ROOT.exists():
        return
    for fp in DEV_ROOT.rglob(".aider.chat.history.md"):
        if "/node_modules/" in str(fp) or "/.git/" in str(fp):
            continue
        if not _file_within(fp, cutoff):
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        blocks = re.split(r"^#### ", text, flags=re.MULTILINE)
        repo = str(fp.parent)
        session_id = repo
        for block in blocks:
            if not block.strip():
                continue
            if _matches(block, pattern, cs):
                lines = block.splitlines()
                head = lines[0] if lines else ""
                role = "user" if head and not head.startswith("---") else "mixed"
                yield Record(
                    agent="aider",
                    session_id=session_id,
                    ts=_iso(fp.stat().st_mtime, fallback=""),
                    role=role,
                    content=block[:2000],
                    source_path=str(fp),
                    metadata={"repo": repo},
                )


PARSERS = {
    "claude": search_claude,
    "codex": search_codex,
    "goose": search_goose,
    "opencode": search_opencode,
    "gemini": search_gemini,
    "aider": search_aider,
}


# --------------------- fast path (ripgrep short-circuit) --------------------- #


def _rg_path() -> str | None:
    return shutil.which("rg")


def _find_recent(dirpath: Path, days: int, glob: str) -> list[str]:
    """Find files matching glob under dirpath, optionally filtered by mtime."""
    if not dirpath.exists():
        return []
    cutoff = time.time() - days * 86400 if days > 0 else None
    out = []
    for fp in dirpath.rglob(glob):
        if cutoff is not None and fp.stat().st_mtime < cutoff:
            continue
        out.append(str(fp))
    return out


def _find_specstory(days: int) -> list[str]:
    if not DEV_ROOT.exists():
        return []
    cutoff = time.time() - days * 86400 if days > 0 else None
    out = []
    for md in DEV_ROOT.rglob("*.md"):
        if "/.specstory/" not in str(md):
            continue
        if cutoff is not None and md.stat().st_mtime < cutoff:
            continue
        out.append(str(md))
    return out


def fast_path(args: argparse.Namespace) -> int:
    """Ripgrep short-circuit for claude-native search. Returns exit code.

    Preserves the ~189ms hot path the legacy bash tool delivered. Activates
    when --fast is set (default when --agent claude). Output mirrors the
    legacy ripgrep-style text format byte-for-byte enough that existing
    grep-the-output callers keep working.
    """
    rg = _rg_path()
    if rg is None:
        print(
            "# WARN: ripgrep not found; falling back to python parser",
            file=sys.stderr,
        )
        return 1  # signal to caller: fall back

    files: list[str] = []
    if args.specstory:
        files += _find_specstory(args.recent)
    else:
        if args.search_jsonl:
            files += _find_recent(CLAUDE_PROJECTS, args.recent, "*.jsonl")
        if args.backups or args.all:
            files += _find_recent(CLAUDE_BACKUPS, args.recent, "*.jsonl")
        if args.all:
            files += _find_specstory(args.recent)

    if not files:
        print("# Total matches: 0  (no files in scope)", file=sys.stderr)
        return 2

    rg_args = [rg, "--no-heading"]
    if not args.case_sensitive:
        rg_args.append("-i")
    if args.files:
        rg_args.append("--files-with-matches")
    elif args.count:
        rg_args.append("--count")
    else:
        rg_args += ["-n", "-C", str(args.context)]

    rg_args += [args.pattern, "--"]
    rg_args += files

    try:
        proc = subprocess.run(
            rg_args,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        print(f"# WARN: ripgrep failed: {e}", file=sys.stderr)
        return 1

    out = proc.stdout
    if args.clean and not (args.files or args.count):
        out = _clean(out)

    if out:
        sys.stdout.write(out)
    rc = proc.returncode
    # rg: 0=match, 1=no match, 2=error
    if rc == 0:
        # count matches roughly for summary
        n = out.count("\n") if out else 0
        print(
            f"# Total matches: {n}  (claude-fast, {len(files)} files)",
            file=sys.stderr,
        )
        return 0
    if rc == 1:
        print(f"# Total matches: 0  (claude-fast, {len(files)} files)", file=sys.stderr)
        return 2
    print(f"# ripgrep exited {rc}: {proc.stderr.strip()}", file=sys.stderr)
    return rc


# --------------------- output emitters --------------------- #


def emit_jsonl(rec: Record) -> None:
    print(json.dumps(asdict(rec), ensure_ascii=False))


def emit_text(rec: Record, clean: bool) -> None:
    content = rec.content[:200].replace("\n", " ")
    if clean:
        content = _clean(content)
    line_marker = f":{rec.line}" if rec.line else ""
    print(
        f"[{rec.agent}] {rec.ts}  {rec.role:>9}  {rec.session_id[:30]}\n"
        f"   {content}\n"
        f"   ↳ {rec.source_path}{line_marker}\n"
    )


# --------------------- inventory --------------------- #


def inventory() -> list[tuple[str, str, bool]]:
    checks = [
        ("claude", str(CLAUDE_PROJECTS)),
        ("codex", str(HOME / ".codex/sessions")),
        ("goose", str(HOME / ".local/share/goose/sessions")),
        ("opencode", str(HOME / ".local/share/opencode/opencode.db")),
        ("gemini", str(HOME / ".gemini/tmp")),
        ("aider", str(DEV_ROOT) + " (per-repo .aider.chat.history.md)"),
    ]
    rows = []
    for agent, path in checks:
        p = Path(path.split(" ")[0])
        exists = p.exists()
        if agent == "aider" and exists:
            exists = any(True for _ in DEV_ROOT.rglob(".aider.chat.history.md"))
        rows.append((agent, path, exists))
    return rows


# --------------------- main --------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="session-search",
        description="Unified cross-harness coding-agent session search.",
    )
    p.add_argument("pattern", nargs="?", help="Pattern to search for.")
    p.add_argument(
        "--agent",
        choices=list(PARSERS.keys()) + ["all"],
        default="claude",
        help=(
            "Which agent's history to search (default: claude). "
            "'all' fans out to all 6 harnesses."
        ),
    )
    # Claude-specific source toggles (only meaningful for --agent claude)
    p.add_argument("--specstory", action="store_true", help="Search SpecStory MD only.")
    p.add_argument("--backups", action="store_true", help="Include ~/.claude/backups.")
    p.add_argument(
        "--all",
        action="store_true",
        help="Claude: JSONL + SpecStory + backups. Equivalent to bash legacy --all.",
    )
    # Recency + limit
    p.add_argument(
        "--recent",
        type=int,
        default=0,
        help="Only files whose mtime is within N days (0=all).",
    )
    p.add_argument(
        "--limit", type=int, default=0, help="Cap matches per agent (0=unlimited)."
    )
    # Output modes
    p.add_argument("--files", action="store_true", help="Emit unique source paths.")
    p.add_argument("--count", action="store_true", help="Emit per-file match counts.")
    p.add_argument(
        "--context", type=int, default=1, help="Lines of context (fast/legacy only)."
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Un-escape JSON escapes in content (text/legacy modes).",
    )
    p.add_argument(
        "--format",
        choices=["jsonl", "text"],
        default="jsonl",
        help="Output format for python parsers (default jsonl).",
    )
    # Case
    p.add_argument("--case-sensitive", action="store_true", help="Case-sensitive match.")
    # Speed knobs
    p.add_argument(
        "--fast",
        action="store_true",
        help="Force ripgrep fast path (claude only).",
    )
    p.add_argument(
        "--no-fast",
        action="store_true",
        help="Disable ripgrep fast path even when claude-only.",
    )
    p.add_argument(
        "--list-agents",
        action="store_true",
        help="Print inventory of agents with on-disk history, then exit.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    # Entry-point dispatch: when invoked via the `session-search-x` shim, that
    # shim exports SESSION_SEARCH_LEGACY_X=1 so we know to behave like the
    # legacy cross-agent tool (default --agent all) AND emit a deprecation
    # notice. When invoked as `session-search`, claude-first defaults (already
    # the build_parser default). One binary serves both entry points during
    # the 30-day deprecation window. Env var (not argv[0]) because Python
    # overrides sys.argv[0] to the script path, ignoring `exec -a`.
    legacy_x = os.environ.get("SESSION_SEARCH_LEGACY_X") == "1"

    p = build_parser()
    if legacy_x:
        # Override the default before parsing so user flags still take precedence
        for action in p._actions:
            if action.dest == "agent":
                action.default = "all"
        print(
            "# DEPRECATION: 'session-search-x' is now an alias for 'session-search "
            "--agent all'. The -x name will be removed ~30 days after the unified "
            "tool ships (target: 2026-06-27). Update callers.",
            file=sys.stderr,
        )

    args = p.parse_args(argv)

    if args.list_agents:
        rows = inventory()
        width = max(len(a) for a, _, _ in rows)
        for agent, path, has in rows:
            mark = "✅" if has else "❌"
            print(f"  {mark}  {agent.ljust(width)}  {path}")
        return 0

    if not args.pattern:
        p.print_help(sys.stderr)
        return 1

    # Determine claude source mix (needed for both fast and python paths)
    args.search_jsonl = not args.specstory  # default unless --specstory alone

    # Decide: fast path or python parsers
    claude_only = args.agent == "claude"
    fast_eligible = (
        claude_only
        and not args.no_fast
        and args.format != "jsonl"  # fast path emits ripgrep text, not JSONL
        # ↑ if user wants JSONL records, use the python parser even for claude
    )
    # Implicit-fast triggers when output mode is text-like
    want_fast = args.fast or (
        fast_eligible and (args.files or args.count or args.format == "text")
    )

    if want_fast:
        rc = fast_path(args)
        if rc != 1:  # 1 = fallback signal from fast_path
            return rc
        # else fall through to python path

    # Python parser path
    cs = args.case_sensitive
    cutoff = time.time() - args.recent * 86400 if args.recent > 0 else None

    # Build the parser list. For claude, may include multiple sources.
    if args.agent == "all":
        parsers = [(name, PARSERS[name]) for name in PARSERS]
    elif args.agent == "claude":
        parsers = []
        if args.specstory and not args.all:
            parsers.append(("claude-specstory", search_claude_specstory))
        else:
            if args.search_jsonl:
                parsers.append(("claude", search_claude))
            if args.backups or args.all:
                parsers.append(("claude-backups", search_claude_backups))
            if args.all:
                parsers.append(("claude-specstory", search_claude_specstory))
    else:
        parsers = [(args.agent, PARSERS[args.agent])]

    total = 0
    per_label_counts: dict[str, int] = defaultdict(int)
    files_seen: set[str] = set()
    per_file_counts: dict[str, int] = defaultdict(int)

    for label, parser in parsers:
        try:
            for rec in parser(args.pattern, cs, cutoff):
                if args.limit and per_label_counts[label] >= args.limit:
                    break
                per_label_counts[label] += 1
                total += 1

                if args.files:
                    files_seen.add(rec.source_path)
                    continue
                if args.count:
                    per_file_counts[rec.source_path] += 1
                    continue
                if args.format == "jsonl":
                    emit_jsonl(rec)
                else:
                    emit_text(rec, args.clean)
        except Exception as e:  # noqa: BLE001
            print(
                f"# WARN: {label} parser failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )

    # Aggregate emitters
    if args.files:
        for fp in sorted(files_seen):
            print(fp)
    elif args.count:
        for fp, n in sorted(per_file_counts.items(), key=lambda kv: -kv[1]):
            print(f"{n}\t{fp}")

    summary = ", ".join(f"{a}={c}" for a, c in per_label_counts.items()) or "none"
    print(f"# Total matches: {total}  ({summary})", file=sys.stderr)
    return 0 if total > 0 else 2


def main_session_search_shim(argv: list[str] | None = None) -> int:
    """Deprecation entry point for the legacy ``session-search`` command.

    ``session-search`` has been absorbed into SIO as ``sio search`` (Phase 0 of
    the session-search -> SIO merge). This shim keeps the old command working —
    a single packaged entry point that survives ``pip install`` on any machine —
    while emitting a one-line deprecation notice. Invocations via ``sio search``
    go straight to ``main()`` and never see this notice. Remove this entry point
    after the deprecation window.
    """
    print(
        "# DEPRECATION: `session-search` is now `sio search` (identical flags). "
        "The standalone command will be removed after the merge deprecation "
        "window — update callers to `sio search`.",
        file=sys.stderr,
    )
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
