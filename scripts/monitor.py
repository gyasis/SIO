#!/usr/bin/env python3
"""Stage 4 — sio monitor: temporal fix-detection over the FULL history.

For each structural signature it asks: did this failure DECAY because a fix landed, or
because the work stopped? The debate's rule (denominator = activity, not calendar time):
  - a signature that went COLD while its project KEPT WORKING  -> FIXED (a fix landed)
  - a signature that went cold around when the project went quiet -> DORMANT (abandoned)
  - a signature still failing recently -> ACTIVE
  - hot throughout, never decays -> CHRONIC

"Activity after" (count of the project's error-events after the signature's last
occurrence) is the command-cycle-style denominator — far more than calendar days.

Writes a signature_lifecycle table and joins error_resolutions to show, for FIXED
signatures, WHAT fix landed and WHEN. Read-only on error_records.

Usage:
  python scripts/monitor.py                 # all history
  python scripts/monitor.py --since 2026-04-01
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

SIO_DB = os.path.expanduser("~/.sio/sio.db")
FREQ_THRESHOLD = 5          # all-time floor
HOT_PEAK = 3                # >= this in a single day = was a real hotspot
RECENT_DAYS = 3             # last occurrence within this of global-latest = still ACTIVE
MIN_EVENTS_AFTER = 15       # project must have kept working this much after = real fix

LIFECYCLE_DDL = """
CREATE TABLE IF NOT EXISTS signature_lifecycle (
    project_tag TEXT, command_category TEXT, error_type TEXT,
    total INTEGER, first_seen TEXT, last_seen TEXT, peak_day INTEGER,
    events_after INTEGER, days_idle INTEGER, lifecycle TEXT, fix_landed_at TEXT,
    computed_at TEXT,
    UNIQUE(project_tag, command_category, error_type)
)
"""


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00").split("+")[0])
    except Exception:
        try:
            return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--db", default=SIO_DB)
    ap.add_argument("--out", default="SIGNATURE_LIFECYCLE.md")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute(LIFECYCLE_DDL)
    con.commit()

    q = """SELECT project_tag, command_category, error_type, timestamp
           FROM error_records WHERE excluded=0 AND project_tag IS NOT NULL"""
    params = []
    if args.since:
        q += " AND timestamp >= ?"
        params.append(args.since)
    rows = con.execute(q, params).fetchall()
    print(f"[monitor] {len(rows)} tagged rows")

    sig_times: dict[tuple, list[datetime]] = defaultdict(list)
    proj_times: dict[str, list[datetime]] = defaultdict(list)
    global_last = None
    for proj, cat, etype, ts in rows:
        t = parse_ts(ts)
        if not t:
            continue
        sig_times[(proj, cat, etype)].append(t)
        proj_times[proj].append(t)
        global_last = t if global_last is None else max(global_last, t)

    for p in proj_times:
        proj_times[p].sort()

    def events_after(proj, t):
        arr = proj_times[proj]
        lo, hi = 0, len(arr)
        while lo < hi:  # bisect: count events strictly after t
            mid = (lo + hi) // 2
            if arr[mid] <= t:
                lo = mid + 1
            else:
                hi = mid
        return len(arr) - lo

    results = []
    for (proj, cat, etype), times in sig_times.items():
        if len(times) < FREQ_THRESHOLD:
            continue
        times.sort()
        first, last = times[0], times[-1]
        peak_day = max(Counter(t.date() for t in times).values())
        after = events_after(proj, last)
        days_idle = (global_last - last).days

        if days_idle <= RECENT_DAYS:
            life = "ACTIVE"
        elif peak_day >= HOT_PEAK and after >= MIN_EVENTS_AFTER:
            life = "FIXED"            # decayed while the project kept working
        elif peak_day >= HOT_PEAK:
            life = "DORMANT"          # went cold, but so did the project
        else:
            life = "MINOR"
        results.append({
            "proj": proj, "cat": cat, "etype": etype, "total": len(times),
            "first": first, "last": last, "peak_day": peak_day,
            "after": after, "days_idle": days_idle, "life": life,
        })

    for r in results:
        con.execute(
            """INSERT INTO signature_lifecycle
               (project_tag, command_category, error_type, total, first_seen, last_seen,
                peak_day, events_after, days_idle, lifecycle, fix_landed_at, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(project_tag, command_category, error_type) DO UPDATE SET
                 total=excluded.total, first_seen=excluded.first_seen,
                 last_seen=excluded.last_seen,
                 peak_day=excluded.peak_day, events_after=excluded.events_after,
                 days_idle=excluded.days_idle, lifecycle=excluded.lifecycle,
                 fix_landed_at=excluded.fix_landed_at, computed_at=excluded.computed_at""",
            (r["proj"], r["cat"], r["etype"], r["total"], r["first"].isoformat(),
             r["last"].isoformat(), r["peak_day"], r["after"], r["days_idle"], r["life"],
             r["last"].isoformat() if r["life"] == "FIXED" else None),
        )
    con.commit()

    dist = Counter(r["life"] for r in results)
    print(f"[monitor] {len(results)} signatures: " +
          " ".join(f"{k}={v}" for k, v in dist.most_common()))

    # report: FIXED (with the fix from Stage 2) + ACTIVE/CHRONIC (still hurting)
    def fix_for(r):
        row = con.execute(
            "SELECT fix FROM error_resolutions WHERE project_tag=? AND command_category=? "
            "AND error_type=? AND fix NOT IN ('unknown','')",
            (r["proj"], r["cat"], r["etype"])).fetchone()
        return row[0] if row else ""

    fixed = sorted([r for r in results if r["life"] == "FIXED"],
                   key=lambda r: r["total"], reverse=True)
    active = sorted([r for r in results if r["life"] == "ACTIVE"],
                    key=lambda r: r["total"], reverse=True)
    L = [f"# Signature Lifecycle — temporal fix-detection ({len(rows)} rows)\n",
         f"\n_{dict(dist)}_\n",
         "\n## ✅ FIXED — decayed while the project kept working (a fix landed)\n"]
    for r in fixed[:20]:
        fx = fix_for(r)
        L.append(f"- **`{r['cat']}/{r['etype']}`** in `{r['proj']}` — {r['total']}x, "
                 f"last {r['last'].date()} (then {r['after']} more project events). "
                 f"fix landed ≈ {r['last'].date()}" + (f" → {fx[:80]}" if fx else ""))
    L.append("\n## 🔴 ACTIVE — still failing in the last "
             f"{RECENT_DAYS} days (needs attention)\n")
    for r in active[:20]:
        L.append(f"- **`{r['cat']}/{r['etype']}`** in `{r['proj']}` — "
                 f"{r['total']}x, peak {r['peak_day']}/day")
    with open(args.out, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"[monitor] wrote {args.out}")
    con.close()


if __name__ == "__main__":
    main()
