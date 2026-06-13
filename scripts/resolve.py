#!/usr/bin/env python3
"""Stage 2 — sio resolve: pair each recurring MISTAKE with its SOLUTION.

For every structural signature (project_tag + command_category + error_type) it:
  1. Orders all occurrences across the window by time, grouped by session.
  2. Deterministic signals (no LLM):
       - recurred_sessions: # distinct sessions the signature appears in. The debate's
         strongest disproof — a signature that recurs across MANY sessions was never
         really fixed (chronic), regardless of any single "fix".
       - suppression: a bypass shell idiom present (|| true, --no-verify, .skip …).
       - anchor: the LAST occurrence (success-anchored — the fix, if any, follows it).
  3. nano-judge over the anchor [summary + context_after] → {resolution_type, fix,
     confidence}, given the deterministic signals. classes: CLEAN_FIX | BYPASS |
     STALLED | RECURRING.
  4. Upserts mistake→solution pairs into error_resolutions (read by Stage 5 guardrail).

Read-only on error_records (writes only the new error_resolutions table). Labeling via
lm_factory (default nano; SIO_AUTOPSY_LM overrides).

Usage:
  python scripts/resolve.py --since 2026-06-06
  python scripts/resolve.py --since 2026-06-06 --no-judge   # deterministic only, no LLM
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import defaultdict

from sio.mining.forward_window import extract_forward_window
from sio.mining.tagging import command_category, project_tag

SIO_DB = os.path.expanduser("~/.sio/sio.db")
FREQ_THRESHOLD = 4
TOP_N_JUDGE = 25

SUPPRESSION_RE = re.compile(
    r"(\|\|\s*true|\|\|\s*exit\s+0|--no-verify|--force\b|--no-deps|skip-checks"
    r"|ignore-scripts|\.skip\(|@pytest\.mark\.skip|\bxfail\b|--strict=false)",
    re.IGNORECASE,
)

RESOLUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS error_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_tag TEXT,
    command_category TEXT,
    error_type TEXT,
    occurrences INTEGER,
    recurred_sessions INTEGER,
    resolution_type TEXT,
    fix TEXT,
    confidence REAL,
    evidence_session TEXT,
    anchor_error_id INTEGER,
    resolved_at TEXT,
    computed_at TEXT,
    UNIQUE(project_tag, command_category, error_type)
)
"""


def _parse_judge(txt: str) -> dict:
    """Robustly pull {resolution_type, fix, confidence} from a reply that may be fenced,
    prose-wrapped, or carry unescaped quotes in the fix (flash is messier than gpt-4o-mini)."""
    t = re.sub(r"```(?:json)?", "", txt or "").strip()
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(t[s:e + 1])
        except Exception:  # noqa: BLE001
            pass

    def grab(key: str) -> str | None:
        m = re.search(rf'"{key}"\s*:\s*"(.*?)"\s*[,}}]', t, re.DOTALL)
        return m.group(1).strip() if m else None

    cm = re.search(r'"confidence"\s*:\s*([0-9.]+)', t)
    return {
        "resolution_type": grab("resolution_type"),
        "fix": grab("fix"),
        "confidence": float(cm.group(1)) if cm else 0.3,
    }


def fetch(con, since):
    q = """SELECT id, session_id, timestamp, tool_name, tool_input, source_file, summary,
                  context_after, project_tag, command_category, error_type
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
    ap.add_argument("--db", default=SIO_DB)
    ap.add_argument("--no-judge", action="store_true", help="deterministic only, no LLM")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute(RESOLUTIONS_DDL)
    con.commit()
    rows = fetch(con, args.since)
    print(f"[resolve] {len(rows)} rows since {args.since}")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for (rid, sess, ts, tool, tinput, src, summary, ctx_after,
         ptag, ccat, etype) in rows:
        sig = (ptag or project_tag(src), ccat or command_category(tool, tinput), etype)
        groups[sig].append({
            "id": rid, "sess": sess, "ts": ts, "tinput": tinput or "",
            "summary": summary or "", "ctx_after": ctx_after or "",
            "src": src, "tool": tool,
        })

    # deterministic pass
    candidates = []
    for sig, members in groups.items():
        if len(members) < FREQ_THRESHOLD:
            continue
        members.sort(key=lambda m: m["ts"] or "")
        anchor = members[-1]  # success-anchored: the fix (if any) follows the last occurrence
        sessions = {m["sess"] for m in members}
        suppression = any(SUPPRESSION_RE.search(m["tinput"]) for m in members)
        proj, cat, etype = sig
        candidates.append({
            "proj": proj, "cat": cat, "etype": etype,
            "occ": len(members), "recurred_sessions": len(sessions),
            "suppression": suppression, "anchor": anchor,
        })
    # judge the worst offenders first (most occurrences)
    candidates.sort(key=lambda c: c["occ"], reverse=True)
    print(f"[resolve] {len(candidates)} signatures >= freq {FREQ_THRESHOLD}")

    lm = None
    if not args.no_judge and candidates:
        from sio.core.dspy.lm_factory import make_lm
        # The JUDGE classifies resolution + extracts the actual fix from the forward window —
        # harder reasoning than the autopsy labeler. Default gpt-4o-mini (sanctioned; the cost
        # rule bans gpt-4o, not -mini): empirically it pulled 23 real fixes vs flash's 1 (flash
        # too conservative for extraction). Override with SIO_RESOLVE_LM=gemini/gemini-flash-latest
        # (cheaper, conservative) or any litellm id.
        judge_model = os.environ.get("SIO_RESOLVE_LM", "openai/gpt-4o-mini")
        print(f"[resolve] judge model: {judge_model}")
        lm = make_lm(judge_model, temperature=0.2, max_tokens=300)

    upserts = 0
    for i, c in enumerate(candidates):
        # deterministic prior: chronic across many sessions = never really fixed. These
        # signals are OBJECTIVE and the judge must NOT override them (the debate's weight-1.0
        # disproof) — when set, the judge only extracts the fix text + confidence.
        if c["recurred_sessions"] >= 3:
            det_type = "RECURRING"
        elif c["suppression"]:
            det_type = "BYPASS"
        else:
            det_type = None  # ambiguous → judge decides CLEAN_FIX vs STALLED

        res_type, fix, conf = det_type or "STALLED", "", 0.3
        if lm is not None and i < TOP_N_JUDGE:
            a = c["anchor"]
            # the FIX is the successful turn(s) after the anchor — re-parse the transcript
            window = extract_forward_window(a["src"], a["ts"], a["tool"], n=12)
            prompt = (
                "A recurring AI-coding-agent failure. Decide if it was RESOLVED and extract the "
                "ACTUAL fix from the turns that FOLLOWED the last occurrence.\n"
                'Output STRICT JSON {"resolution_type":...,"fix":...,"confidence":0-1}:\n'
                "- resolution_type: CLEAN_FIX (a real fix followed) | BYPASS (suppressed/skipped, "
                "not fixed) | STALLED (no resolution visible) | RECURRING (keeps coming back).\n"
                "- fix: the CONCRETE remedy (exact command/flag/path/edit/env/container) shown in "
                "the OK turns below. If none visible: 'unknown'. No generic advice.\n"
                "- confidence: 0-1 that resolution_type is right.\n"
                "Output ONE line of minified JSON only. Keep 'fix' a SHORT plain-text phrase "
                "(<20 words) — NO code blocks, NO raw newlines or double-quotes inside it.\n\n"
                f"signature: {c['cat']} / {c['etype']} in {c['proj']}\n"
                f"occurred {c['occ']}x across {c['recurred_sessions']} session(s); "
                f"suppression_present={c['suppression']}\n"
                f"anchor summary:\n{a['summary'][:400]}\n\n"
                "turns AFTER the anchor (tools flagged OK=succeeded / ERR=failed — the OK ones "
                f"are the candidate fix):\n{window[:1600] or '(no transcript window available)'}"
            )
            try:
                raw = lm(messages=[{"role": "user", "content": prompt}])
                txt = raw[0] if isinstance(raw, list) else str(raw)
                j = _parse_judge(txt)
                fix = (j.get("fix") or "").strip()
                conf = float(j.get("confidence") or conf)
                # judge classifies ONLY ambiguous signatures; RECURRING/BYPASS are
                # deterministic-final (cross-session recurrence / suppression are objective).
                if det_type is None and j.get("resolution_type"):
                    res_type = j["resolution_type"]
            except Exception as e:  # noqa: BLE001
                fix = f"(judge error: {e})"

        con.execute(
            """INSERT INTO error_resolutions
               (project_tag, command_category, error_type, occurrences, recurred_sessions,
                resolution_type, fix, confidence, evidence_session, anchor_error_id,
                resolved_at, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(project_tag, command_category, error_type) DO UPDATE SET
                 occurrences=excluded.occurrences, recurred_sessions=excluded.recurred_sessions,
                 resolution_type=excluded.resolution_type, fix=excluded.fix,
                 confidence=excluded.confidence, evidence_session=excluded.evidence_session,
                 anchor_error_id=excluded.anchor_error_id, resolved_at=excluded.resolved_at,
                 computed_at=excluded.computed_at""",
            (c["proj"], c["cat"], c["etype"], c["occ"], c["recurred_sessions"],
             res_type, fix, conf, c["anchor"]["sess"], c["anchor"]["id"], c["anchor"]["ts"]),
        )
        upserts += 1
        if lm is not None and i < TOP_N_JUDGE:
            print(f"  {res_type:9} ({conf:.1f})  [{c['proj']}] {c['cat']}/{c['etype']}")
    con.commit()

    print(f"\n[resolve] upserted {upserts} mistake→solution rows into error_resolutions")
    for rt, n in con.execute(
        "SELECT resolution_type, COUNT(*) FROM error_resolutions GROUP BY resolution_type "
        "ORDER BY COUNT(*) DESC"
    ).fetchall():
        print(f"    {n:>4}  {rt}")
    con.close()


if __name__ == "__main__":
    main()
