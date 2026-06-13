#!/usr/bin/env python3
"""Stage 1 — backfill structural tags onto error_records (project_tag, command_category,
time_bucket). Idempotent + incremental: adds the columns if missing, fills only rows whose
tags are still NULL. Run after `sio mine` so every mined error is tagged.

Usage:
  python scripts/tag_errors.py                 # tag all untagged rows
  python scripts/tag_errors.py --retag         # recompute for ALL rows (after logic change)
  python scripts/tag_errors.py --since 2026-06-06
"""
from __future__ import annotations

import argparse
import os
import sqlite3

from sio.mining.tagging import TAG_COLUMNS, derive_all

SIO_DB = os.path.expanduser("~/.sio/sio.db")


def ensure_columns(con: sqlite3.Connection):
    existing = {r[1] for r in con.execute("PRAGMA table_info(error_records)").fetchall()}
    for col in TAG_COLUMNS:
        if col not in existing:
            con.execute(f"ALTER TABLE error_records ADD COLUMN {col} TEXT")
            print(f"[tag] added column error_records.{col}")
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=SIO_DB)
    ap.add_argument("--since", default=None)
    ap.add_argument("--retag", action="store_true", help="recompute for ALL rows, not just NULL")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    ensure_columns(con)

    q = ("SELECT id, source_file, tool_name, tool_input, timestamp "
         "FROM error_records WHERE 1=1")
    params: list = []
    if not args.retag:
        q += " AND project_tag IS NULL"
    if args.since:
        q += " AND timestamp >= ?"
        params.append(args.since)
    rows = con.execute(q, params).fetchall()
    print(f"[tag] {len(rows)} rows to tag ({'retag-all' if args.retag else 'untagged only'})")

    n = 0
    for rid, src, tool, tinput, ts in rows:
        tags = derive_all(src, tool, tinput, ts)
        con.execute(
            "UPDATE error_records SET project_tag=?, command_category=?, time_bucket=? WHERE id=?",
            (tags["project_tag"], tags["command_category"], tags["time_bucket"], rid),
        )
        n += 1
        if n % 500 == 0:
            con.commit()
            print(f"  …{n}")
    con.commit()

    total = con.execute(
        "SELECT COUNT(*) FROM error_records WHERE project_tag IS NOT NULL"
    ).fetchone()[0]
    print(f"[tag] tagged {n} rows; {total} rows now carry tags")
    # quick sanity: top command categories
    print("[tag] top command categories:")
    for cat, c in con.execute(
        "SELECT command_category, COUNT(*) n FROM error_records "
        "WHERE command_category IS NOT NULL GROUP BY command_category ORDER BY n DESC LIMIT 8"
    ).fetchall():
        print(f"    {c:>4}  {cat}")
    con.close()


if __name__ == "__main__":
    main()
