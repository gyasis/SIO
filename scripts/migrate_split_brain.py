"""scripts/migrate_split_brain.py — One-time backfill from per-platform DBs.

Calls sync_behavior_invocations(since_timestamp=None) for a full backfill
of all per-platform behavior_invocations into the canonical sio.db.

Usage:
    python -m scripts.migrate_split_brain
    python scripts/migrate_split_brain.py

Exits 0 in all cases (no per-platform DB is normal for fresh installs).
Safe under concurrent hook writes — relies on sync.py's WAL + INSERT OR IGNORE.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run full backfill sync. Returns exit code."""
    from sio.core.db.sync import sync_behavior_invocations  # noqa: PLC0415

    try:
        results = sync_behavior_invocations(since_timestamp=None)
    except Exception as exc:
        # Per-platform DB may not exist yet — treat as zero rows.
        if "no such file" in str(exc).lower() or "unable to open" in str(exc).lower():
            print("No per-platform DB found; nothing to migrate.")
            return 0
        print(f"migrate_split_brain: unexpected error: {exc}", file=sys.stderr)
        return 0  # Non-fatal — installer must not fail on sync errors

    if not results:
        print("No per-platform DB found; nothing to migrate.")
        return 0

    for platform, n in results.items():
        print(f"{platform}: {n} rows copied")

    return 0


if __name__ == "__main__":
    sys.exit(main())
