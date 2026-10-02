"""Copy every coding-agent session store into SIO's archive. Never deletes.

Why this exists: Claude Code deletes transcripts older than ``cleanupPeriodDays``
(default 30). Before April 2026 SpecStory kept the long history; that job died in
the disk wipe and nothing noticed, so five months of transcripts were deleted.
SIO now keeps the raw session files itself, independent of any harness default.

Per-file rules (session files are normally append-only):

* missing in the archive          -> copy                         ("new")
* same size, same tail            -> refresh mtime only           ("unchanged")
* grew, archived bytes still a prefix (tail check) -> copy        ("appended")
* shrank or rewritten             -> keep old copy as ``<name>.~<UTC>``, then copy ("rewritten")
* SQLite database                 -> SQLite backup API, never a byte copy ("sqlite")
* ``-wal`` / ``-shm`` / ``-journal`` sidecars -> skipped (the backup API reads through them)

Every copy goes to a temp file and is renamed into place, so a crash never leaves a
half-written archive file. A source file that disappears is marked ``source_gone_at``
in the manifest; its archived copy is kept.

Outputs (both under the archive root, for humans and agents):

* ``_archive.db``  -- manifest: one row per archived file
* ``_status.json`` -- the last run: per-source counts, errors, total bytes
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sio.core.paths import sio_home

#: (archive name, path relative to $HOME). A directory is mirrored; a file is copied.
SOURCES: list[tuple[str, str]] = [
    ("claude/projects", ".claude/projects"),
    ("claude/history.jsonl", ".claude/history.jsonl"),
    ("codex/sessions", ".codex/sessions"),
    ("codex/history.jsonl", ".codex/history.jsonl"),
    ("gemini/tmp", ".gemini/tmp"),
    ("pi/sessions", ".pi/agent/sessions"),
    ("kimi/sessions", ".kimi-code/sessions"),
    ("promptchain/sessions", ".promptchain/sessions"),
    ("goose/sessions", ".local/share/goose/sessions"),
    ("opencode", ".local/share/opencode"),
]

SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
TAIL_BYTES = 4096
SQLITE_MAGIC = b"SQLite format 3\x00"


def archive_root() -> Path:
    """``$SIO_ARCHIVE_DIR``, else ``<sio_home>/archive`` (read at call time)."""
    env = os.environ.get("SIO_ARCHIVE_DIR")
    return Path(env).expanduser() if env else sio_home() / "archive"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _same_tail(src: Path, dest: Path, upto: int) -> bool:
    """True when the last bytes of ``dest`` equal ``src`` at the same offset."""
    n = min(TAIL_BYTES, upto)
    if n == 0:
        return True
    with src.open("rb") as a, dest.open("rb") as b:
        a.seek(upto - n)
        b.seek(upto - n)
        return a.read(n) == b.read(n)


def _atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _sqlite_backup(src: Path, dest: Path) -> None:
    """Consistent snapshot of a live database (WAL included) via the backup API."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)
            # The copy inherits WAL mode; switch it off so readers never drop
            # -wal/-shm sidecars inside the archive.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
            source.close()
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _keep_version(dest: Path) -> None:
    """Preserve the current archived copy before it is replaced."""
    os.replace(dest, dest.with_name(f"{dest.name}.~{_stamp()}"))


def _src_mtime(src: Path) -> float:
    """For SQLite, changes may sit in the ``-wal`` file while the main file is untouched."""
    mtime = src.stat().st_mtime
    wal = src.with_name(src.name + "-wal")
    if wal.exists():
        mtime = max(mtime, wal.stat().st_mtime)
    return mtime


@dataclass
class SourceResult:
    name: str
    state: str = "ok"  # ok | absent | error
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def bump(self, action: str) -> None:
        self.counts[action] = self.counts.get(action, 0) + 1


class Manifest:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS files (
                   agent TEXT NOT NULL,
                   rel TEXT NOT NULL,
                   size INTEGER,
                   src_mtime REAL,
                   first_seen TEXT,
                   last_synced TEXT,
                   source_gone_at TEXT,
                   versions INTEGER NOT NULL DEFAULT 0,
                   PRIMARY KEY (agent, rel))"""
        )

    def get(self, agent: str, rel: str) -> tuple | None:
        return self.conn.execute(
            "SELECT size, src_mtime FROM files WHERE agent=? AND rel=?", (agent, rel)
        ).fetchone()

    def record(self, agent: str, rel: str, size: int, mtime: float, now: str,
               versioned: bool) -> None:
        self.conn.execute(
            """INSERT INTO files (agent, rel, size, src_mtime, first_seen, last_synced, versions)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent, rel) DO UPDATE SET
                   size=excluded.size, src_mtime=excluded.src_mtime,
                   last_synced=excluded.last_synced, source_gone_at=NULL,
                   versions=files.versions + ?""",
            (agent, rel, size, mtime, now, now, int(versioned), int(versioned)),
        )

    def mark_gone(self, agent: str, seen: set[str], now: str) -> int:
        rows = self.conn.execute(
            "SELECT rel FROM files WHERE agent=? AND source_gone_at IS NULL", (agent,)
        ).fetchall()
        gone = [r[0] for r in rows if r[0] not in seen]
        self.conn.executemany(
            "UPDATE files SET source_gone_at=? WHERE agent=? AND rel=?",
            [(now, agent, rel) for rel in gone],
        )
        return len(gone)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def _sync_one(src: Path, dest: Path, agent: str, rel: str, manifest: Manifest,
              res: SourceResult, now: str) -> None:
    if src.name.endswith(SIDECAR_SUFFIXES):
        return
    st = src.stat()
    if _is_sqlite(src):
        mtime = _src_mtime(src)
        prev = manifest.get(agent, rel)
        if dest.exists() and prev is not None and prev[1] == mtime:
            res.bump("unchanged")
            manifest.record(agent, rel, st.st_size, mtime, now, False)
            return
        shrank = dest.exists() and st.st_size < dest.stat().st_size
        if shrank:
            _keep_version(dest)
        _sqlite_backup(src, dest)
        res.bump("sqlite")
        manifest.record(agent, rel, st.st_size, mtime, now, shrank)
        return

    versioned = False
    if not dest.exists():
        _atomic_copy(src, dest)
        res.bump("new")
    else:
        dst = dest.stat()
        if dst.st_size == st.st_size and _same_tail(src, dest, st.st_size):
            if int(dst.st_mtime) != int(st.st_mtime):
                shutil.copystat(src, dest)
            res.bump("unchanged")
        elif st.st_size > dst.st_size and _same_tail(src, dest, dst.st_size):
            _atomic_copy(src, dest)
            res.bump("appended")
        else:
            _keep_version(dest)
            _atomic_copy(src, dest)
            versioned = True
            res.bump("rewritten")
    manifest.record(agent, rel, st.st_size, st.st_mtime, now, versioned)


def sync(home: Path | None = None, root: Path | None = None,
         sources: list[tuple[str, str]] | None = None) -> dict:
    """Run one archive pass. Returns the status dict also written to ``_status.json``."""
    home = home or Path.home()
    root = root or archive_root()
    root.mkdir(parents=True, exist_ok=True)
    started, t0 = _now(), time.monotonic()
    manifest = Manifest(root / "_archive.db")
    results: list[SourceResult] = []
    try:
        for name, relpath in sources or SOURCES:
            res = SourceResult(name)
            results.append(res)
            src_root = home / relpath
            if not src_root.exists():
                res.state = "absent"  # harness not installed; never mark its files gone
                continue
            seen: set[str] = set()
            files = [src_root] if src_root.is_file() else sorted(
                p for p in src_root.rglob("*") if p.is_file() and not p.is_symlink()
            )
            for src in files:
                rel = src.name if src_root.is_file() else str(src.relative_to(src_root))
                dest = root / name if src_root.is_file() else root / name / rel
                seen.add(rel)
                try:
                    _sync_one(src, dest, name, rel, manifest, res, started)
                except (OSError, sqlite3.Error) as exc:
                    res.errors.append(f"{rel}: {exc}")
            gone = manifest.mark_gone(name, seen, started)
            if gone:
                res.counts["source_gone"] = gone
            if res.errors:
                res.state = "error"
    finally:
        manifest.close()

    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    status = {
        "started": started,
        "finished": _now(),
        "seconds": round(time.monotonic() - t0, 2),
        "archive": str(root),
        "bytes": total,
        "ok": all(r.state != "error" for r in results),
        "sources": [
            {"name": r.name, "state": r.state, "counts": r.counts,
             "errors": r.errors[:20], "error_count": len(r.errors)}
            for r in results
        ],
    }
    tmp = root / "_status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2) + "\n")
    os.replace(tmp, root / "_status.json")
    return status


def status(root: Path | None = None) -> dict:
    """Last run plus manifest totals. Raises FileNotFoundError if never synced."""
    root = root or archive_root()
    last = json.loads((root / "_status.json").read_text())
    db = root / "_archive.db"
    per_agent = []
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            per_agent = [
                {"agent": a, "files": n, "source_gone": g, "versions": v}
                for a, n, g, v in conn.execute(
                    """SELECT agent, count(*), count(source_gone_at), sum(versions)
                       FROM files GROUP BY agent ORDER BY agent"""
                )
            ]
        finally:
            conn.close()
    return {"last_run": last, "manifest": per_agent}
