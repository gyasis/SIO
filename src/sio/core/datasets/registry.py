"""trainsets table — content-hash + permanent storage for training JSONLs."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

DATASETS_DIR = Path.home() / ".sio" / "datasets"


def _db_path() -> str:
    return os.environ.get("SIO_DB_PATH", str(Path.home() / ".sio" / "sio.db"))


def ensure_schema(db_path: str | None = None) -> None:
    """Idempotent migration — creates trainsets table + adds
    optimized_modules.dataset_id column if missing."""
    p = db_path or _db_path()
    conn = sqlite3.connect(p)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trainsets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                row_count INTEGER,
                stored_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                description TEXT,
                source TEXT,
                parent_dataset_id INTEGER,
                FOREIGN KEY (parent_dataset_id) REFERENCES trainsets(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trainsets_hash ON trainsets(content_sha256)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trainsets_slug ON trainsets(slug)")
        # Add optimized_modules.trainset_id column (idempotent)
        try:
            conn.execute("ALTER TABLE optimized_modules ADD COLUMN trainset_id INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Add optimized_modules.seed column (idempotent — for XV reproducibility)
        try:
            conn.execute("ALTER TABLE optimized_modules ADD COLUMN seed INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def hash_file(path: Path) -> str:
    """SHA256 of a file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_by_hash(content_sha256: str, db_path: str | None = None) -> Optional[dict]:
    p = db_path or _db_path()
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM trainsets WHERE content_sha256 = ?", (content_sha256,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_by_path(stored_path: str, db_path: str | None = None) -> Optional[dict]:
    p = db_path or _db_path()
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM trainsets WHERE stored_path = ?", (stored_path,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def register_dataset(
    source_path: Path,
    slug: str,
    description: str = "",
    source: str = "curate",
    parent_dataset_id: Optional[int] = None,
    db_path: str | None = None,
) -> int:
    """Hash, copy to ~/.sio/trainsets/, register in DB. Returns dataset id.

    Idempotent: if a dataset with the same content_sha256 exists, returns
    that id without copying or re-registering.
    """
    ensure_schema(db_path)
    sha = hash_file(source_path)
    existing = find_by_hash(sha, db_path)
    if existing:
        return existing["id"]

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    short = sha[:12]
    target = DATASETS_DIR / f"{slug}_{short}.jsonl"
    if not target.exists():
        shutil.copy2(source_path, target)
    row_count = sum(1 for _ in open(target))

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    p = db_path or _db_path()
    conn = sqlite3.connect(p)
    try:
        cur = conn.execute(
            "INSERT INTO trainsets "
            "(slug, content_sha256, row_count, stored_path, created_at, "
            "description, source, parent_dataset_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, sha, row_count, str(target), now, description, source, parent_dataset_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def link_optimized_module(module_id: int, dataset_id: int,
                          db_path: str | None = None) -> None:
    """Set optimized_modules.trainset_id for an existing run."""
    ensure_schema(db_path)
    p = db_path or _db_path()
    conn = sqlite3.connect(p)
    try:
        conn.execute(
            "UPDATE optimized_modules SET trainset_id = ? WHERE id = ?",
            (dataset_id, module_id),
        )
        conn.commit()
    finally:
        conn.close()

# NOTE 2026-05-18: `backfill_known_trainsets()` was removed. It was a
# one-shot helper that hardcoded three specific trainset files + linked
# them to modules #8/#13/#14/#15/#10. Its work has already been applied
# to ~/.sio/sio.db (rows 1, 2, 3 in the trainsets table, plus the
# trainset_id linkages on the corresponding optimized_modules rows).
# Now that curate_cmd and amplify_cmd auto-register every output via
# register_dataset(), and optimize_cmd auto-links by sha via
# link_optimized_module(), the one-shot path is obsolete. Generic
# recovery is the right replacement — e.g. `sio datasets scan
# --dir ~/.sio/{amplified,curated}/` that content-addresses every JSONL
# (queued as a separate task; not implemented yet). See PRD
# sio_dataset_versioning_2026-05-16 for the design intent.
