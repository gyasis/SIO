#!/usr/bin/env python3
"""SIO Anti-Pattern Autopsy (MVP) — turn a week of mined errors into ANTIPATTERNS.md.

Project-agnostic by construction: project + command-category are DERIVED from the
session, never hardcoded. Clusters on a STRUCTURAL signature (tool + error_type +
project + command_category) — NOT the free-text nano summary (which drifts). The
summary is used only as label input.

Pipeline (per PRD prd-corpus-problem-miner.md):
  query window (excluded=0) -> derive project/command_category -> structural signature
  -> cluster (freq>=THRESH) -> lite-bypass (zombie% + suppression%) + dual denominator
  (cycles + active-wall-time, 12-min cap) -> nano-label top-N -> emit ANTIPATTERNS.md

Read-only on error_records (never deletes). Labeling routes through SIO's lm_factory
(default openai/gpt-4.1-nano, ~cents; override with SIO_AUTOPSY_LM) — never a raw SDK client.

Usage:
  python scripts/autopsy.py --since 2026-06-06 --out ANTIPATTERNS.md
  python scripts/autopsy.py --since 2026-06-06 --no-label   # clustering only, no LLM/cost
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime

from sio.mining.tagging import command_category, project_tag  # single source of derivation

SIO_DB = os.path.expanduser("~/.sio/sio.db")
FREQ_THRESHOLD = 4          # <4 in a week is a fluke
GAP_CAP_MIN = 12            # gap >12 min = context switch (active-wall-time cap)
ZOMBIE_WINDOW_MIN = 15      # same sig within 15 min of predecessor = trial-and-error
TOP_N_LABEL = 15            # clusters sent to nano

# TRUE evasion signals only. Deliberately EXCLUDES 2>/dev/null and bare -f: in this
# corpus those are benign idioms (`source .env 2>/dev/null`, `rm -f`), not bypasses —
# including them floods Bypass detection with false positives (the nag-spam failure mode).
SUPPRESSION_RE = re.compile(
    r"(\|\|\s*true|\|\|\s*exit\s+0|--no-verify|--force\b|--no-deps|skip-checks"
    r"|ignore-scripts|\.skip\(|@pytest\.mark\.skip|\bxfail\b|--strict=false)",
    re.IGNORECASE,
)

def parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00").split("+")[0])
    except Exception:
        try:
            return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def fetch_rows(con, since):
    # prefer the persisted Stage-1 tags (project_tag, command_category); fall back to
    # deriving on the fly for any row not yet tagged.
    q = """SELECT id, error_type, timestamp, tool_name, tool_input, source_file, summary,
                  error_text, project_tag, command_category
           FROM error_records WHERE excluded=0"""
    params = []
    if since:
        q += " AND timestamp >= ?"
        params.append(since)
    q += " ORDER BY timestamp ASC"
    return con.execute(q, params).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-06")
    ap.add_argument("--out", default="ANTIPATTERNS.md")
    ap.add_argument("--no-label", action="store_true", help="skip the nano labeling (no cost)")
    ap.add_argument("--db", default=SIO_DB)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    rows = fetch_rows(con, args.since)
    con.close()
    print(f"[autopsy] {len(rows)} rows (excluded=0, since {args.since})")

    # --- bucket into structural-signature clusters ---
    clusters: dict[tuple, list[dict]] = defaultdict(list)
    for rid, etype, ts, tool, tinput, src, summary, etext, ptag, ccat in rows:
        proj = ptag or project_tag(src)            # persisted Stage-1 tag, else derive
        cat = ccat or command_category(tool, tinput)
        sig = (proj, cat, etype)
        clusters[sig].append({
            "id": rid, "ts": parse_ts(ts), "tinput": tinput or "",
            "summary": summary or "", "etext": etext or "",
        })

    # --- metrics per cluster ---
    results = []
    for sig, members in clusters.items():
        if len(members) < FREQ_THRESHOLD:
            continue
        members.sort(key=lambda m: m["ts"] or datetime.min)
        cycles = len(members)
        # active wall-time: sum inter-member gaps, capped at GAP_CAP_MIN
        active_sec, zombie_hits = 0, 0
        prev = None
        for m in members:
            if prev and m["ts"] and prev["ts"]:
                gap = (m["ts"] - prev["ts"]).total_seconds()
                if 0 < gap <= GAP_CAP_MIN * 60:
                    active_sec += gap
                if 0 < gap <= ZOMBIE_WINDOW_MIN * 60:
                    zombie_hits += 1
            prev = m
        suppression = sum(1 for m in members if SUPPRESSION_RE.search(m["tinput"]))
        proj, cat, etype = sig
        results.append({
            "proj": proj, "cat": cat, "etype": etype,
            "cycles": cycles,
            "active_min": round(active_sec / 60, 1),
            "zombie_pct": round(100 * zombie_hits / max(cycles - 1, 1)),
            "suppression_pct": round(100 * suppression / cycles),
            "samples": [m["summary"][:400] for m in members[:3] if m["summary"]],
            "sample_cmd": next((m["tinput"][:200] for m in members if m["tinput"]), ""),
        })

    results.sort(key=lambda r: (r["active_min"], r["cycles"]), reverse=True)
    print(f"[autopsy] {len(results)} clusters >= freq {FREQ_THRESHOLD}")

    # --- nano labeling (top-N) ---
    labels = {}
    if not args.no_label and results:
        # Route through SIO's mandated LM hub (lm_factory) — NOT a raw openai client.
        # Inherits the gpt-4o ban-check + ollama-heartbeat fallback, and stays consistent
        # with the rest of SIO's DSPy LLM path. Model is overridable via SIO_AUTOPSY_LM
        # (default nano per cost preference; set to a gemini/* or ollama/* id to switch).
        from sio.core.dspy.lm_factory import make_lm
        lm = make_lm(os.environ.get("SIO_AUTOPSY_LM", "openai/gpt-4.1-nano"),
                     temperature=0.3, max_tokens=250)
        for r in results[:TOP_N_LABEL]:
            key = (r["proj"], r["cat"], r["etype"])
            prompt = (
                "Write ONE entry for a developer's ANTIPATTERNS.md from a cluster of REAL "
                "AI-coding-agent failures. Be SPECIFIC to the command + error shown. The sample "
                "summaries below each contain a Context/Failure/Lesson — MINE THE LESSON LINES "
                "for the actual fix that was found.\n"
                'Output STRICT JSON {"name":...,"why":...,"protip":...}:\n'
                "- name: snappy, <8 words, names the SPECIFIC trap (e.g. 'Wrong Lakebase "
                "container for docker exec'), NOT a generic 'retry loop'.\n"
                "- why: one sentence citing the concrete command/tool/error.\n"
                "- protip: the CONCRETE fix from the Lesson lines — name the exact command, flag, "
                "path, env var, or container. BANNED generic filler: 'add a retry limit', "
                "'implement idempotency', 'add error handling'. If no concrete fix is "
                "visible in the samples, write 'needs investigation: <the specific "
                "open question>'.\n\n"
                f"command: `{r['cat']}`  |  where: {r['proj']}  |  failure-mode: {r['etype']}\n"
                f"happened {r['cycles']}x · {r['active_min']} active-min · "
                f"zombie {r['zombie_pct']}%\n"
                f"sample command: {r['sample_cmd']}\n\n"
                "sample summaries (mine the Lesson lines):\n---\n" + "\n---\n".join(r["samples"])
            )
            try:
                raw = lm(messages=[{"role": "user", "content": prompt}])
                txt = (raw[0] if isinstance(raw, list) else str(raw)).strip()
                txt = re.sub(r"^```(json)?|```$", "", txt, flags=re.MULTILINE).strip()
                labels[key] = json.loads(txt)
            except Exception as e:
                labels[key] = {"name": f"{r['cat']} failures",
                               "why": f"(label error: {e})", "protip": ""}
            print(f"  labeled: [{r['proj']}] {r['cat']}/{r['etype']}")

    # --- emit ANTIPATTERNS.md ---
    def resolution(r):
        if r["suppression_pct"] >= 25:
            return "BYPASS"
        if r["zombie_pct"] >= 50:
            return "TRIAL-AND-ERROR"
        return "FRICTION"

    bypasses = [r for r in results if resolution(r) == "BYPASS"]
    sinks = [r for r in results if resolution(r) != "BYPASS"]
    proj_tot = defaultdict(int)
    for r in results:
        proj_tot[r["proj"]] += r["cycles"]
    total_cycles = sum(proj_tot.values()) or 1

    L = [f"# Anti-Pattern Autopsy — since {args.since}",
         f"\n_{len(rows)} error rows · {len(results)} clusters (freq≥{FREQ_THRESHOLD}) · "
         f"structural-signature clustering · nano-labeled_\n",
         "\n## 🏆 Hall of Shame — top time-sinks\n"]
    for r in sinks[:TOP_N_LABEL]:
        lab = labels.get((r["proj"], r["cat"], r["etype"]), {})
        name = lab.get("name") or f"{r['cat']} / {r['etype']}"
        L.append(f"\n### {name}")
        L.append(f"- **Where:** `{r['proj']}` · **Command:** `{r['cat']}` · "
                 f"**Type:** {r['etype']}")
        L.append(f"- **Cost:** {r['active_min']} active-min · {r['cycles']} cycles · "
                 f"zombie {r['zombie_pct']}% · suppression {r['suppression_pct']}%")
        if lab.get("why"):
            L.append(f"- **Why:** {lab['why']}")
        if lab.get("protip"):
            L.append(f"- **Pro-tip:** {lab['protip']}")
    L.append("\n## 🚩 Bypass Gallery — Technical-Debt Generators\n")
    if bypasses:
        for r in bypasses:
            lab = labels.get((r["proj"], r["cat"], r["etype"]), {})
            L.append(f"- **`{r['cat']}`** in `{r['proj']}` — suppression {r['suppression_pct']}% "
                     f"({r['cycles']} cycles). {lab.get('protip','')}")
    else:
        L.append("_(none crossed the 25% suppression threshold this window)_")
    L.append("\n## 📊 Project Friction Heatmap\n")
    for proj, n in sorted(proj_tot.items(), key=lambda x: -x[1]):
        L.append(f"- **{proj}** — {n} cycles ({round(100*n/total_cycles)}% of clustered failures)")

    with open(args.out, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"[autopsy] wrote {args.out}  ({len(sinks)} sinks, {len(bypasses)} bypasses)")


if __name__ == "__main__":
    main()
