"""T100 [US7] — Ground-truth slug remap script (R-5, Jaccard FK migration).

Remaps ``ground_truth.pattern_id`` FK references from old slugs to new slugs
when the Jaccard overlap between their member error sets is >= 0.5.

On match:
    UPDATE ground_truth
    SET pattern_id = <new_slug>, remapped_from_pattern_id = <old_slug>
    WHERE pattern_id = <old_slug>

On no match (Jaccard < 0.5): leave orphaned.

Idempotent: rows that have already been remapped (remapped_from_pattern_id IS NOT NULL)
are skipped on subsequent runs.

Usage::

    python scripts/remap_ground_truth_slugs.py [--dry-run] [--db-path PATH]

Exit codes:
    0  All done (may have remapped 0 rows).
    1  Fatal error (DB not found, schema missing, etc.).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Public API (used by tests)
# ---------------------------------------------------------------------------


def _jaccard(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _get_member_set(conn: sqlite3.Connection, pattern_id: str) -> set[int]:
    """Return the set of error_ids for a pattern from pattern_members table.

    Falls back to scanning error_records.pattern_id if pattern_members is absent.
    """
    # Try pattern_members first (preferred)
    try:
        rows = conn.execute(
            "SELECT error_id FROM pattern_members WHERE pattern_id = ?",
            (pattern_id,),
        ).fetchall()
        if rows:
            return {r[0] for r in rows}
    except sqlite3.OperationalError:
        pass  # table may not exist

    # Fallback: error_records.pattern_id column
    try:
        rows = conn.execute(
            "SELECT id FROM error_records WHERE pattern_id = ?",
            (pattern_id,),
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def remap_slugs(
    conn: sqlite3.Connection,
    *,
    threshold: float = 0.5,
    dry_run: bool = False,
) -> dict[str, int]:
    """Remap ground_truth FK references from old slugs to new slugs.

    Parameters
    ----------
    conn:
        Open SQLite connection to sio.db.
    threshold:
        Jaccard overlap required to trigger a remap. Default 0.5.
    dry_run:
        If True, compute the remap plan but do NOT write any changes.

    Returns
    -------
    dict with keys:
        ``remapped`` — number of ground_truth rows updated.
        ``skipped``  — number of ground_truth rows left unchanged.
    """
    # Collect ground_truth rows that still have the OLD slug
    # (not yet remapped: remapped_from_pattern_id IS NULL)
    gt_rows = conn.execute(
        "SELECT id, pattern_id FROM ground_truth WHERE remapped_from_pattern_id IS NULL"
    ).fetchall()

    if not gt_rows:
        return {"remapped": 0, "skipped": 0}

    # Collect all current pattern_ids from patterns table
    current_patterns = {
        r[0]
        for r in conn.execute("SELECT pattern_id FROM patterns").fetchall()
    }

    # Build member sets lazily (cached per pattern_id)
    _member_cache: dict[str, set[int]] = {}

    def _members(pid: str) -> set[int]:
        if pid not in _member_cache:
            _member_cache[pid] = _get_member_set(conn, pid)
        return _member_cache[pid]

    remapped = 0
    skipped = 0

    for gt_id, old_slug in gt_rows:
        old_members = _members(old_slug)

        # Find best-matching candidate slug by Jaccard (exclude self)
        best_new_slug: str | None = None
        best_score: float = 0.0

        for candidate_slug in current_patterns:
            if candidate_slug == old_slug:
                continue
            score = _jaccard(old_members, _members(candidate_slug))
            if score >= threshold and score > best_score:
                best_score = score
                best_new_slug = candidate_slug

        if best_new_slug is None:
            skipped += 1
            continue

        # Remap this row
        if not dry_run:
            conn.execute(
                "UPDATE ground_truth "
                "SET pattern_id = ?, remapped_from_pattern_id = ? "
                "WHERE id = ?",
                (best_new_slug, old_slug, gt_id),
            )
        remapped += 1

    if not dry_run:
        conn.commit()

    return {"remapped": remapped, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="Remap ground_truth slug FKs from old→new pattern_ids."
    )
    parser.add_argument(
        "--db-path",
        default=os.environ.get("SIO_DB_PATH", str(Path.home() / ".sio" / "sio.db")),
        help="Path to sio.db (default: ~/.sio/sio.db or SIO_DB_PATH env).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Minimum Jaccard overlap to trigger remap (default: 0.5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute plan without writing changes.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"ERROR: sio.db not found at {db_path}")
        return 1

    try:
        conn = _open_db(db_path)
        summary = remap_slugs(conn, threshold=args.threshold, dry_run=args.dry_run)
        conn.close()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}remapped: {summary['remapped']}, skipped: {summary['skipped']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
