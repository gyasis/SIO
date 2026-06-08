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
    # Internal only — full untruncated turn text for --refine predicate.
    # NEVER serialised to JSONL/text output (popped in emit_jsonl).
    match_text: str = ""


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
                            match_text=text,
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
                    match_text=line,
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
                            match_text=text,
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
                match_text=text,
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
                                match_text=text,
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
                            match_text=text,
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
                _content_str = str(content)
                yield Record(
                    agent="opencode",
                    session_id=row["session_id"],
                    ts=_iso(row["time_created"]),
                    role=role,
                    content=_content_str[:2000],
                    source_path=str(db),
                    metadata={"message_id": row["id"], "table": "message"},
                    match_text=_content_str,
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
                    match_text=text,
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
                    match_text=block,
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

    files_raw: list[str] = []
    if args.specstory:
        files_raw += _find_specstory(args.recent)
    else:
        if args.search_jsonl:
            files_raw += _find_recent(CLAUDE_PROJECTS, args.recent, "*.jsonl")
        if args.backups or args.all:
            files_raw += _find_recent(CLAUDE_BACKUPS, args.recent, "*.jsonl")
        if args.all:
            files_raw += _find_specstory(args.recent)

    if not files_raw:
        print("# Total matches: 0  (no files in scope)", file=sys.stderr)
        return 2

    # B3 fix: ripgrep parallelizes across files and emits matches in COMPLETION
    # order, so pre-sorting the file list does NOT guarantee newest-first output.
    # rg --sortr=modified (rg 14+) sorts results by mtime descending AND forces
    # deterministic single-threaded ordered output — matching the python path's
    # newest-first contract.
    files: list[str] = files_raw

    # -H forces the path prefix even when exactly one file is in scope (rg omits
    # it otherwise), so the match-count regex below stays valid for single-file
    # scope (NEW-ISSUE #1: single-file --recent windows reported 0).
    rg_args = [rg, "--no-heading", "--with-filename", "--sortr=modified"]
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
        # B8 fix: count only real match lines (file:line:content pattern) rather
        # than all output lines (which include rg's "--" context separators and
        # blank context lines from -C).
        if args.files or args.count:
            # In --files mode rg emits one path per file; in --count, "path:N".
            n = out.count("\n") if out else 0
        else:
            # B8 fix: count only real match lines.
            # rg --no-heading -n emits:
            #   match line:   <path>:<lineno>:<content>  — colon separators
            #   context line: <path>-<lineno>-<content>  — dash separators
            #   separator:    --
            # We count lines where the path:lineno: prefix appears at the
            # START (anchored), using a colon immediately after the path and
            # a colon after the lineno digits.  We cannot simply search for
            # ":\d+:" anywhere in the line because JSON content (e.g. ISO
            # timestamps "12:00:00") also matches that pattern.
            # Heuristic: split on the first ":digit+:" occurrence that is
            # preceded by a non-dash character.  A match line's path never
            # contains a bare "-digit-" at the same position, so checking
            # that position 0 to the separator uses only ":" (not "-") works.
            _match_re = re.compile(r"^[^:\n]+:\d+:")
            n = sum(1 for line in out.splitlines() if _match_re.match(line))
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


# --------------------- context window API (FR-003, FR-004) --------------------- #


def turns_from_jsonl(
    path: Path,
    *,
    return_line_map: bool = False,
) -> list[dict] | tuple[list[dict], dict[int, int]]:
    """Parse all turns from a Claude JSONL session file.

    Returns a list of dicts, each with at minimum:
        role    (str) — "user" | "assistant" | "tool" | "system" | "unknown"
        content (str) — full text of the turn (newlines preserved)
        ts      (str) — ISO-8601 timestamp (empty string if absent)
        _src_line (int) — 1-based file line number of this turn (B2 fix)

    Turn boundaries follow the on-disk Claude JSONL format:
    each line is one JSON object representing one message/event.  Blank lines
    and JSON-decode failures are skipped silently (same policy as the main
    parser).  This is role-aware: a multi-line assistant response is ONE turn
    regardless of how many newline characters it contains, unlike rg -C which
    counts raw lines.

    When *return_line_map* is True, also returns a dict mapping 1-based file
    line numbers to 0-based turn indices (B2 fix: allows callers to convert
    ``rec.line`` → correct turn index even when blank/malformed lines exist).
    """
    turns: list[dict] = []
    # B2 fix: map file-line-number (1-based) → turn index (0-based)
    line_to_turn_idx: dict[int, int] = {}
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
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
                    text = "\n".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in content_blocks
                        if b
                    ).strip()
                else:
                    text = str(content_blocks or entry.get("text", "")).strip()
                turn_idx = len(turns)
                line_to_turn_idx[lineno] = turn_idx
                turns.append(
                    {
                        "role": role,
                        "content": text,
                        "ts": entry.get("timestamp", ""),
                        "uuid": entry.get("uuid", ""),
                        "session_id": entry.get("sessionId", ""),
                        "_src_line": lineno,
                    }
                )
    except OSError:
        pass
    if return_line_map:
        return turns, line_to_turn_idx
    return turns


def turns_around(turns: list[dict], offset: int, n: int) -> list[dict]:
    """Return the ±N turns around *offset* in *turns*, clamped at boundaries.

    Args:
        turns:  Ordered list of turn dicts as returned by ``turns_from_jsonl``.
        offset: Zero-based index of the hit turn within *turns*.
        n:      Number of turns to include on each side of the hit.

    Returns:
        A new list (subset of *turns* by value) spanning
        ``[max(0, offset-n) .. min(len(turns)-1, offset+n)]`` inclusive.
        When n=0, returns exactly ``[turns[offset]]``.
        Raises ``IndexError`` if *offset* is out of range.

    This is DISTINCT from:
    - rg ``-C N`` — which counts raw *lines*, not role-aware turns.
    - ``--session <uuid>`` — which dumps the FULL transcript with no offset.
    """
    if not turns:
        return []
    if offset < 0 or offset >= len(turns):
        raise IndexError(
            f"offset {offset} out of range for transcript of length {len(turns)}"
        )
    start = max(0, offset - n)
    end = min(len(turns) - 1, offset + n)
    return turns[start : end + 1]


def window_for_session(session_path: Path, hit_offset: int, n: int) -> list[dict]:
    """Convenience wrapper: parse *session_path* then return ``turns_around``.

    Args:
        session_path: Path to a Claude JSONL session file.
        hit_offset:   Zero-based turn index of the search hit.
        n:            Context window half-width in turns.

    Returns:
        List of turn dicts (role, content, ts) spanning ±N turns around the hit,
        clamped at transcript boundaries.
    """
    turns_result = turns_from_jsonl(session_path)
    # turns_from_jsonl returns list[dict] by default (return_line_map=False)
    turns: list[dict] = turns_result  # type: ignore[assignment]
    return turns_around(turns, hit_offset, n)


def _emit_window_as_jsonl(
    turns: list[dict],
    session_id: str,
    source_path: str,
    hit_offset: int,
) -> None:
    """Emit the context window as JSONL Records on stdout (FR-003)."""
    for i, turn in enumerate(turns):
        rec = Record(
            agent="claude",
            session_id=session_id,
            ts=turn.get("ts", ""),
            role=turn.get("role", "unknown"),
            content=turn.get("content", ""),
            source_path=source_path,
            metadata={"context_window": True, "hit_offset": hit_offset},
            line=0,
        )
        print(json.dumps(asdict(rec), ensure_ascii=False))


# --------------------- output emitters --------------------- #


def emit_jsonl(rec: Record) -> None:
    d = asdict(rec)
    d.pop("match_text", None)  # internal field — never in on-wire schema
    print(json.dumps(d, ensure_ascii=False))


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


# --------------------- session skeleton (discovery view) --------------------- #

# Match-location categories, in priority order (most → least meaningful).
CAT_DISCUSSED = "discussed"  # in a human prompt or assistant prose block
CAT_EDITED = "edited"        # pattern in a tool_use file_path (Write/Edit/Read target)
CAT_COMMAND = "command"      # pattern in a Bash command (non-search)
CAT_OUTPUT = "output"        # pattern in tool_result / toolUseResult text
# Sessions whose ONLY match is the agent searching FOR the pattern are noise,
# not real hits — e.g. a stored `session-search "redpen"` Bash invocation.
_SEARCH_NOISE = "__search_noise__"
_SEARCH_TOOLS_RE = re.compile(r"\b(session-search|sio\s+search|rg|ripgrep|grep|ag)\b")


@dataclass
class SessionHit:
    session_id: str  # = the session UUID (jsonl stem)
    project: str
    ts: str
    label: str
    n_hits: int
    snippet: str
    source_path: str


def _project_name(jsonl: Path) -> str:
    """Friendly project from the encoded project dir, e.g. PromptChain, dev-kid."""
    m = re.search(r"-code-(.+)$", jsonl.parent.name)
    return m.group(1) if m else jsonl.parent.name


def _claude_line_categories(
    entry: dict, pattern: str, cs: bool
) -> tuple[set[str], str]:
    """Return (categories, snippet) for where `pattern` appears in one JSONL entry.

    Unlike the per-match parser (which reads only ``message.content[].text``),
    this also inspects tool_use inputs, tool_result content, and the top-level
    ``toolUseResult`` — the fields that hold real work the agent did. A Bash
    command that is itself a search for the pattern is tagged search-noise.
    """
    cats: set[str] = set()
    snippet = ""

    def note(text: str, cat: str) -> None:
        nonlocal snippet
        if text and _matches(text, pattern, cs):
            cats.add(cat)
            if not snippet or cat == CAT_DISCUSSED:
                snippet = " ".join(text.split())[:160]

    msg = entry.get("message") or {}
    blocks = msg.get("content")
    if isinstance(blocks, list):
        for b in blocks:
            if not isinstance(b, dict):
                note(str(b), CAT_DISCUSSED)
                continue
            btype = b.get("type")
            if btype == "text":
                note(b.get("text", ""), CAT_DISCUSSED)
            elif btype == "tool_use":
                inp = b.get("input") or {}
                fp = (
                    inp.get("file_path")
                    or inp.get("path")
                    or inp.get("notebook_path")
                    or ""
                )
                note(str(fp), CAT_EDITED)
                cmd = str(inp.get("command", ""))
                if cmd and _matches(cmd, pattern, cs):
                    if _SEARCH_TOOLS_RE.search(cmd):
                        cats.add(_SEARCH_NOISE)
                    else:
                        note(cmd, CAT_COMMAND)
            elif btype == "tool_result":
                rc = b.get("content")
                if isinstance(rc, list):
                    rc = " ".join(
                        x.get("text", "") if isinstance(x, dict) else str(x)
                        for x in rc
                    )
                note(str(rc or ""), CAT_OUTPUT)
    elif blocks is not None:
        note(str(blocks), CAT_DISCUSSED)

    tur = entry.get("toolUseResult")  # stdout/stderr outside message.content
    if tur is not None:
        note(
            tur if isinstance(tur, str) else json.dumps(tur, ensure_ascii=False),
            CAT_OUTPUT,
        )

    return cats, snippet


def _label_for(cats: set[str]) -> str | None:
    """Strongest real category, or None if the session only has search-noise."""
    for c in (CAT_DISCUSSED, CAT_EDITED, CAT_COMMAND, CAT_OUTPUT):
        if c in cats:
            return c
    return None


def iter_claude_session_hits(
    pattern: str, cs: bool, cutoff: float | None
) -> Iterator[SessionHit]:
    """Skeleton discovery: one row per SESSION that genuinely touched `pattern`.

    Dedups by session UUID, classifies the strongest match, and drops sessions
    whose only match is the agent searching for the pattern. This is the
    human-facing default — a map of relevant sessions, not a raw match count.
    """
    if not CLAUDE_PROJECTS.exists():
        return
    for jsonl in CLAUDE_PROJECTS.rglob("*.jsonl"):
        if not _file_within(jsonl, cutoff):
            continue
        cats_all: set[str] = set()
        n_hits = 0
        snippet = ""
        latest_ts = ""
        try:
            with jsonl.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    cats, snip = _claude_line_categories(entry, pattern, cs)
                    real = cats - {_SEARCH_NOISE}
                    if not real:
                        continue
                    cats_all |= real
                    n_hits += 1
                    if snip and (not snippet or CAT_DISCUSSED in real):
                        snippet = snip
                    ts = entry.get("timestamp", "") or ""
                    if ts > latest_ts:
                        latest_ts = ts
        except OSError:
            continue
        label = _label_for(cats_all)
        if label is None:
            continue
        yield SessionHit(
            session_id=jsonl.stem,
            project=_project_name(jsonl),
            ts=latest_ts,
            label=label,
            n_hits=n_hits,
            snippet=snippet,
            source_path=str(jsonl),
        )


def emit_skeleton(hits: list[SessionHit]) -> int:
    """Print the session-level discovery table. Returns exit code."""
    if not hits:
        print("# no sessions matched", file=sys.stderr)
        return 2
    hits = sorted(hits, key=lambda h: h.ts, reverse=True)
    proj_w = min(max((len(h.project) for h in hits), default=7), 24)
    for h in hits:
        date = h.ts[:10] if h.ts else "—"
        print(
            f"{h.session_id}  {h.project[:proj_w].ljust(proj_w)}  {date}  "
            f"{h.label:<9} ×{h.n_hits}"
        )
        if h.snippet:
            print(f"    {h.snippet}")
    print(
        f"\n# {len(hits)} session(s). Full history: sio search --session <uuid>",
        file=sys.stderr,
    )
    return 0


def _print_transcript_line(entry: dict, clean: bool) -> None:
    msg = entry.get("message") or {}
    role = entry.get("type") or msg.get("role") or "?"
    ts = (entry.get("timestamp", "") or "")[:19]
    blocks = msg.get("content")
    parts: list[str] = []
    if isinstance(blocks, list):
        for b in blocks:
            if not isinstance(b, dict):
                parts.append(str(b))
                continue
            bt = b.get("type")
            if bt == "text":
                parts.append(b.get("text", ""))
            elif bt == "tool_use":
                inp = json.dumps(b.get("input") or {}, ensure_ascii=False)
                parts.append(f"[tool_use {b.get('name', '')}] {inp[:1000]}")
            elif bt == "tool_result":
                rc = b.get("content")
                if isinstance(rc, list):
                    rc = " ".join(
                        x.get("text", "") if isinstance(x, dict) else str(x)
                        for x in rc
                    )
                parts.append(f"[tool_result] {str(rc)[:2000]}")
    elif blocks is not None:
        parts.append(str(blocks))
    text = " ".join(p for p in parts if p).strip()
    if clean:
        text = _clean(text)
    if not text:
        return
    print(f"[{role} {ts}] {text}")


def expand_sessions(uuids: list[str], clean: bool) -> int:
    """Dump the full transcript of each session, looked up by UUID."""
    wanted = [u.strip() for part in uuids for u in part.split(",") if u.strip()]
    found = 0
    for uuid in wanted:
        matches = sorted(CLAUDE_PROJECTS.rglob(f"{uuid}.jsonl"))
        if not matches:
            print(f"# session not found: {uuid}", file=sys.stderr)
            continue
        for jsonl in matches:
            found += 1
            print(f"# ===== session {uuid} =====")
            print(f"# {jsonl}\n")
            try:
                with jsonl.open(encoding="utf-8", errors="replace") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            entry = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        _print_transcript_line(entry, clean)
            except OSError as e:
                print(f"# read error: {e}", file=sys.stderr)
            print()
    return 0 if found else 2


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
        default=None,
        help=(
            "Only files whose mtime is within N days (default: 7). "
            "Use 0 to search full history (overrides the default window). "
            "With --all and no explicit --recent, defaults to full history; "
            "an explicit --recent N is still honored alongside --all. "
            "Aligns with the Cascade Memory Protocol recency-first gate."
        ),
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
        "--around",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Context window: when a search hit is found in a session, return the "
            "±N TURNS around that hit (role-aware: user/assistant/tool), clamped at "
            "transcript boundaries.  Distinct from --context (raw rg lines) and from "
            "--session (full transcript dump).  When --around is set the output is the "
            "windowed turns around each hit in JSONL Record format. (FR-003 / FR-004)"
        ),
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Un-escape JSON escapes in content (text/legacy modes).",
    )
    p.add_argument(
        "--format",
        choices=["jsonl", "text"],
        default=None,
        help=(
            "Output format for python parsers. When omitted, interactive "
            "(TTY) claude search shows the session skeleton; piped output "
            "defaults to jsonl."
        ),
    )
    # Session skeleton (discovery) + expand-by-UUID
    p.add_argument(
        "--skeleton",
        "--sessions",
        dest="skeleton",
        action="store_true",
        help=(
            "Session-level discovery view: one deduped row per session UUID "
            "(claude), classified discussed/edited/command/output, search-noise "
            "dropped. This is the default for interactive claude search."
        ),
    )
    p.add_argument(
        "--session",
        metavar="UUID",
        action="append",
        default=None,
        help=(
            "Expand: print the FULL transcript of one or more sessions by UUID "
            "(comma-separated, or repeat the flag). Pairs with --skeleton."
        ),
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
    # ---------------------------------------------------------------
    # US3 / FR-005: Two-hop cascade flags (T033).
    # Delegating to sio.clustering.hop2 (shared with sio suggest).
    # ---------------------------------------------------------------
    p.add_argument(
        "--refine",
        dest="refine",
        default=None,
        metavar="TERM",
        help=(
            "Hop-2 refinement: AND-narrow the search result set by a second "
            "filter term (comma-separated for OR within Hop-2). Applied after "
            "records are collected and sorted: only records whose content "
            "contains the refine term(s) are emitted. "
            "(FR-005 / US3)"
        ),
    )
    p.add_argument(
        "--strategy",
        dest="hop2_strategy",
        choices=["filter", "recluster", "hybrid"],
        default="filter",
        metavar="STRATEGY",
        help=(
            "Hop-2 narrowing strategy (used with --refine). "
            "'filter' (default): keep only records containing the refine term. "
            "Fast, no embeddings. "
            "'recluster' and 'hybrid' are not supported for sio search (session "
            "records do not map to the error-DB cluster schema); passing either "
            "raises an error. "
            "(FR-005 / US3)"
        ),
    )
    p.add_argument(
        "--noise-threshold",
        dest="noise_threshold",
        type=int,
        default=20,
        metavar="N",
        help=(
            "When the first-hop result count exceeds N, emit a Hop-2 refine "
            "suggestion to stderr (non-blocking). Default: 20. "
            "(FR-006 / US3)"
        ),
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

    # Expand: full transcript of one or more sessions by UUID (no pattern needed).
    if args.session:
        return expand_sessions(args.session, args.clean)

    if not args.pattern:
        p.print_help(sys.stderr)
        return 1

    # B7 fix: reject negative --around values (silently-empty window).
    if getattr(args, "around", None) is not None and args.around < 0:
        print(
            f"# error: --around N must be ≥ 0, got {args.around}",
            file=sys.stderr,
        )
        return 1

    # B1 fix: reject recluster/hybrid for sio search (session records don't map
    # to the error-DB cluster schema; the hop2 module operates on error dicts).
    hop2_strategy = getattr(args, "hop2_strategy", "filter")
    if getattr(args, "refine", None) and hop2_strategy in ("recluster", "hybrid"):
        print(
            f"# error: --strategy {hop2_strategy} is not supported for sio search. "
            "Session transcript records do not have the error-DB schema required by "
            "cluster_errors(). Use --strategy filter (the default) for sio search, "
            "or use 'sio suggest --refine' for the full recluster/hybrid cascade.",
            file=sys.stderr,
        )
        return 1

    # FR-001: resolve the --recent sentinel. --all and --recent stay ORTHOGONAL
    # (--all = source expansion: JSONL + SpecStory + backups; --recent = time window).
    # When --recent is unset (None): default to 7 days, OR full history (0) if --all
    # is set (since --all alone means "find every time" historical research). An
    # EXPLICIT --recent N is always honored, including alongside --all
    # (e.g. `--all --recent 7` = all sources, last 7 days).
    if args.recent is None:
        args.recent = 0 if args.all else 7

    # Resolve output format. Explicit --format wins; otherwise interactive claude
    # search shows the skeleton, and piped output stays jsonl for machine callers.
    explicit_format = args.format is not None
    claude_only = args.agent == "claude"
    want_skeleton = args.skeleton or (
        not explicit_format
        and claude_only
        and not args.files
        and not args.count
        and not args.fast
        and not args.specstory
        and sys.stdout.isatty()
    )
    args.format = args.format or "jsonl"

    if want_skeleton:
        cs = args.case_sensitive
        cutoff = time.time() - args.recent * 86400 if args.recent > 0 else None
        hits = list(iter_claude_session_hits(args.pattern, cs, cutoff))
        return emit_skeleton(hits)

    # Determine claude source mix (needed for both fast and python paths)
    args.search_jsonl = not args.specstory  # default unless --specstory alone

    # B4 fix: --around requires the python parser (windowing only runs there).
    # Force the python path when --around is set so windowing always applies.
    around_set = getattr(args, "around", None) is not None

    # Decide: fast path or python parsers
    fast_eligible = (
        claude_only
        and not args.no_fast
        and not around_set  # B4: --around forces python path
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

    # Collect records so we can sort newest-first before emitting (FR-001).
    # --files and --count are aggregation modes that don't emit individual
    # records, so they bypass the sort buffer and stay O(1) memory.
    inline_records: list[Record] = []

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
                # Buffer for newest-first sort.
                inline_records.append(rec)
        except Exception as e:  # noqa: BLE001
            print(
                f"# WARN: {label} parser failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )

    # Emit buffered records newest-first (FR-001).
    # --skeleton already sorts by ts; skip double-sort there.
    if inline_records:
        inline_records.sort(key=lambda r: r.ts or "", reverse=True)

        # B1 fix: apply Hop-2 --refine narrowing on the search result records.
        # Map each Record to the minimal dict shape _hop2_matches expects:
        # it AND-matches refine terms against the error's text fields; for
        # search records we expose ``content`` as ``error_text`` and populate
        # the remaining fields so the predicate has something to scan.
        refine_term = getattr(args, "refine", None)
        if refine_term:
            from sio.clustering.hop2 import _hop2_matches  # noqa: PLC0415

            refine_terms = [t.strip().lower() for t in refine_term.split(",") if t.strip()]

            def _rec_as_error_dict(r: Record) -> dict:
                return {
                    # Use full untruncated text so --refine sees the whole turn,
                    # not just the 2000-char preview stored in r.content.
                    "error_text": r.match_text or r.content,
                    "user_message": "",
                    "context_before": "",
                    "context_after": "",
                    "source_file": r.source_path,
                }

            inline_records = [
                r for r in inline_records
                if _hop2_matches(_rec_as_error_dict(r), refine_terms)
            ]
            # Recount total after narrowing so the summary is accurate.
            total = len(inline_records)

        # FR-003 / FR-004: --around N replaces the normal per-record emitter with a
        # role-aware ±N turn context window around each hit.  This is DISTINCT from:
        #   rg -C  (raw lines, not role-aware)
        #   --session (full transcript, no offset)
        #
        # B2 fix: use the line→turn-index map so the window is correct even when
        # blank or malformed lines exist in the JSONL (previously hit_offset used
        # rec.line - 1 which miscounted because turns_from_jsonl skips those lines).
        #
        # B5 fix: window ALL hits per session (up to MAX_WINDOWS_PER_SESSION) rather
        # than only the first/newest. Emit a note to stderr if hits are capped.
        MAX_WINDOWS_PER_SESSION = 5
        if around_set:
            # Cache parsed turns + line maps per session path to avoid re-parsing
            # on each hit.
            _session_cache: dict[str, tuple[list[dict], dict[int, int]]] = {}
            # Track how many windows have been emitted per session.
            _session_window_counts: dict[str, int] = defaultdict(int)
            _session_hit_counts: dict[str, int] = defaultdict(int)

            for rec in inline_records:
                _session_hit_counts[rec.source_path] += 1

            for rec in inline_records:
                sp = rec.source_path
                cap = _session_window_counts[sp]
                if cap >= MAX_WINDOWS_PER_SESSION:
                    continue
                _session_window_counts[sp] += 1

                # Load + cache turns and line map for this session.
                if sp not in _session_cache:
                    result = turns_from_jsonl(Path(sp), return_line_map=True)
                    # turns_from_jsonl with return_line_map=True returns a tuple
                    _session_cache[sp] = result  # type: ignore[assignment]
                turns, line_map = _session_cache[sp]

                # B2 fix: resolve file line number → turn index via the line map
                # instead of using (rec.line - 1) which is wrong when blank/
                # malformed lines precede the hit.
                if rec.line and rec.line in line_map:
                    hit_offset = line_map[rec.line]
                else:
                    # Fallback for records without a line number (non-JSONL sources).
                    hit_offset = max(0, rec.line - 1) if rec.line else 0

                try:
                    window = turns_around(turns, hit_offset, args.around)
                    _emit_window_as_jsonl(window, rec.session_id, sp, hit_offset)
                except (IndexError, OSError) as exc:
                    print(
                        f"# WARN: --around failed for {sp}: {exc}",
                        file=sys.stderr,
                    )

            # B5: note any sessions where hits were capped.
            for sp, total_hits in _session_hit_counts.items():
                emitted = _session_window_counts.get(sp, 0)
                if total_hits > emitted:
                    print(
                        f"# NOTE: {sp}: {total_hits} hits, showed windows for "
                        f"{emitted} (cap={MAX_WINDOWS_PER_SESSION}). "
                        "Re-run with a narrower pattern to see all hits.",
                        file=sys.stderr,
                    )
        else:
            for rec in inline_records:
                if args.format == "jsonl":
                    emit_jsonl(rec)
                else:
                    emit_text(rec, args.clean)

    # Aggregate emitters
    if args.files:
        for fp in sorted(files_seen):
            print(fp)
    elif args.count:
        for fp, n in sorted(per_file_counts.items(), key=lambda kv: -kv[1]):
            print(f"{n}\t{fp}")

    summary = ", ".join(f"{a}={c}" for a, c in per_label_counts.items()) or "none"
    print(f"# Total matches: {total}  ({summary})", file=sys.stderr)

    # FR-002: On zero results within the default window, emit a widen hint.
    # The hint fires only when a non-zero default window was active (i.e. the
    # caller did NOT explicitly pass --recent 0 / --all).  We detect the
    # default by checking that args.recent > 0 and args.all is False.
    if total == 0 and args.recent > 0 and not args.all:
        print(
            f"# 0 results in last {args.recent} days — widen with `--recent 0` "
            "to search full history.",
            file=sys.stderr,
        )

    # FR-006 / T034: When Hop-1 is noisy, suggest a concrete Hop-2 refine
    # command (non-blocking: emitted to stderr, never an error or hard stop).
    # Only fires when no --refine was already active (the operator already knows
    # about multi-hop if they typed --refine).
    if (
        total > 0
        and not getattr(args, "refine", None)
        and args.pattern
    ):
        noise_threshold = getattr(args, "noise_threshold", 20)
        from sio.clustering.hop2 import build_noise_hint  # noqa: PLC0415

        hint = build_noise_hint(
            hop1_count=total,
            noise_threshold=noise_threshold,
            pattern=args.pattern,
        )
        if hint:
            print(hint, file=sys.stderr)

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
