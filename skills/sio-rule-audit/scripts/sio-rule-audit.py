#!/usr/bin/env python3
"""
SIO Rule Audit — find rules that exist as text but aren't enforced.

Scans:
  ~/.claude/CLAUDE.md
  ~/.claude/rules/domains/*.md
  ~/.claude/rules/tools/*.md

For each detected rule (lines containing MUST/NEVER/ALWAYS/BLOCKING/MANDATORY/CRITICAL),
counts enforcement coverage across 4 channels:
  1. Hook    — does any ~/.claude/hooks/**/*.sh reference this rule's key terms?
  2. Skill   — does any ~/.claude/skills/*/SKILL.md reference it?
  3. Recipe  — does ~/.claude/recipes/INDEX.md keywords overlap?
  4. Memory  — does any file in ~/.claude/projects/**/memory/*.md reference it?

Cross-references SIO violation counts (last 14d default).

Ranks by ratio: (violations + 1) / (enforcement_channels + 1) — high = high-pain low-wiring.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

HOME = Path.home()
CLAUDE_MD = HOME / ".claude/CLAUDE.md"
RULES_DOMAINS = HOME / ".claude/rules/domains"
RULES_TOOLS = HOME / ".claude/rules/tools"
HOOKS_DIR = HOME / ".claude/hooks"
SKILLS_DIR = HOME / ".claude/skills"
RECIPES_INDEX = HOME / ".claude/recipes/INDEX.md"
# Memory: scan all project memory dirs under ~/.claude/projects/
MEMORY_ROOT = HOME / ".claude/projects"
SIO_DB = HOME / ".sio/sio.db"

RULE_MARKERS = re.compile(r"\b(MUST|NEVER|ALWAYS|BLOCKING|MANDATORY|CRITICAL)\b")
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "must", "never", "always",
    "blocking", "mandatory", "critical", "you", "your", "agent", "rule",
    "rules", "use", "used", "using", "from", "into", "before", "after",
    "when", "what", "which", "where", "should", "would", "could", "have",
    "has", "had", "are", "was", "were", "been", "being", "any", "all",
    "not", "but", "can", "than", "then", "them", "they", "their", "there",
    "these", "those", "via", "per", "each", "one", "two", "three",
}


def extract_rules(filepath: Path):
    """Extract rule statements from a markdown file."""
    if not filepath.exists():
        return []
    rules = []
    try:
        lines = filepath.read_text(encoding="utf-8", errors="replace").split("\n")
    except Exception:
        return []

    current_section = ""
    for i, line in enumerate(lines, 1):
        # Track section headings for context
        if line.startswith("#"):
            current_section = line.lstrip("# ").strip()[:80]
            continue
        if RULE_MARKERS.search(line) and len(line.strip()) > 20:
            # Heuristic: skip code/quote blocks, table headers, and prose
            stripped = line.strip()
            if stripped.startswith(("|", "```", "<", "*", "-", ">")) and len(stripped) < 120:
                # Bulleted rule lines are fine; only filter unhelpful ones
                if stripped.startswith("|"):
                    continue
            rules.append({
                "file": str(filepath.relative_to(HOME)),
                "line": i,
                "section": current_section,
                "text": stripped[:200],
                "key_terms": extract_key_terms(stripped + " " + current_section),
            })
    return rules


def extract_key_terms(text: str):
    """Extract distinctive lowercase terms from a rule line."""
    # Split on non-word chars, lowercase, drop stopwords + short words
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text.lower())
    distinctive = [w for w in words if w not in STOPWORDS and not w.isdigit()]
    # Dedupe, preserve order
    seen = set()
    out = []
    for w in distinctive:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:8]  # cap at 8 terms


def scan_directory_for_terms(directory: Path, key_terms: list, glob_pattern: str = "**/*"):
    """Return list of files (relative paths) that contain any key term."""
    if not directory.exists():
        return []
    matches = []
    if not key_terms:
        return matches
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in key_terms) + r")\b", re.IGNORECASE)
    for f in directory.glob(glob_pattern):
        if not f.is_file():
            continue
        # Skip backups, state, irrelevant files
        if any(part in (".git", "node_modules", "state", "backups", "__pycache__") for part in f.parts):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            # Need at least 2 distinct key-term hits for a meaningful match
            hits = set(m.group(0).lower() for m in pattern.finditer(content))
            if len(hits) >= 2 or (len(hits) >= 1 and len(key_terms) <= 3):
                matches.append(str(f.relative_to(HOME)))
        except Exception:
            continue
    return matches


def scan_memory_for_terms(key_terms: list):
    """Scan all project memory dirs under ~/.claude/projects/ for key terms."""
    if not MEMORY_ROOT.exists():
        return []
    results = []
    # Find memory/ subdirs under any project dir
    for memory_dir in MEMORY_ROOT.glob("*/memory"):
        if memory_dir.is_dir():
            results.extend(scan_directory_for_terms(memory_dir, key_terms, "*.md"))
    return results


def count_sio_violations(key_terms: list, since_days: int):
    """Count SIO error events in the last N days matching any key term."""
    if not SIO_DB.exists() or not key_terms:
        return 0
    try:
        conn = sqlite3.connect(f"file:{SIO_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Discover the actual schema — SIO uses `error_records` (not `errors`)
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r["name"] for r in cur.fetchall()}
        # Try error_records first (SIO actual), fall back to errors
        table = None
        for t in ("error_records", "errors"):
            if t in tables:
                table = t
                break
        if not table:
            conn.close()
            return 0
        cur.execute(f"PRAGMA table_info({table})")
        cols = {r["name"] for r in cur.fetchall()}
        # Find a content/error column
        content_col = None
        for c in ("error_text", "error", "content", "error_message", "message", "text"):
            if c in cols:
                content_col = c
                break
        if not content_col:
            conn.close()
            return 0
        time_col = None
        for c in ("timestamp", "created_at", "mined_at", "ts", "time"):
            if c in cols:
                time_col = c
                break
        # Build LIKE filter
        like_clauses = " OR ".join([f"LOWER({content_col}) LIKE ?" for _ in key_terms])
        params = [f"%{t.lower()}%" for t in key_terms]
        sql = f"SELECT COUNT(*) AS n FROM {table} WHERE ({like_clauses})"
        if time_col:
            cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()
            sql += f" AND {time_col} >= ?"
            params.append(cutoff)
        cur.execute(sql, params)
        n = cur.fetchone()["n"]
        conn.close()
        return n
    except Exception:
        return 0


def audit():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--since", type=int, default=14, help="SIO violation lookback in days")
    parser.add_argument("--min-violations", type=int, default=0,
                        help="Only show rules with >= this many violations")
    args = parser.parse_args()

    # 1. Extract rules from all source files
    rules = []
    rules.extend(extract_rules(CLAUDE_MD))
    if RULES_DOMAINS.exists():
        for f in sorted(RULES_DOMAINS.glob("*.md")):
            rules.extend(extract_rules(f))
    if RULES_TOOLS.exists():
        for f in sorted(RULES_TOOLS.glob("*.md")):
            rules.extend(extract_rules(f))

    # 2. For each rule, score enforcement and violations
    results = []
    for r in rules:
        kt = r["key_terms"]
        if not kt:
            continue
        hooks = scan_directory_for_terms(HOOKS_DIR, kt, "**/*.sh")
        # Also scan .py / .js hooks
        hooks += scan_directory_for_terms(HOOKS_DIR, kt, "**/*.py")
        hooks += scan_directory_for_terms(HOOKS_DIR, kt, "**/*.js")
        skills = scan_directory_for_terms(SKILLS_DIR, kt, "**/SKILL.md")
        memory = scan_memory_for_terms(kt)
        recipes = []
        if RECIPES_INDEX.exists():
            try:
                content = RECIPES_INDEX.read_text(encoding="utf-8", errors="replace").lower()
                if sum(1 for t in kt if t in content) >= 2:
                    recipes = ["recipes/INDEX.md"]
            except Exception:
                pass

        enforcement_count = (
            (1 if hooks else 0)
            + (1 if skills else 0)
            + (1 if recipes else 0)
            + (1 if memory else 0)
        )

        violations = count_sio_violations(kt, args.since)

        if violations < args.min_violations:
            continue

        # Score: high violations + low enforcement = high priority
        score = (violations + 1) / (enforcement_count + 1)

        results.append({
            "rule": r["text"],
            "file": r["file"],
            "line": r["line"],
            "section": r["section"],
            "key_terms": kt,
            "violations": violations,
            "enforcement": {
                "hook": len(set(hooks)),
                "skill": len(set(skills)),
                "recipe": len(set(recipes)),
                "memory": len(set(memory)),
            },
            "enforcement_count": enforcement_count,
            "score": round(score, 2),
        })

    # 3. Sort and emit
    results.sort(key=lambda x: -x["score"])
    top = results[: args.top]

    if args.json:
        print(json.dumps({
            "since_days": args.since,
            "total_rules": len(rules),
            "scored_rules": len(results),
            "results": top,
        }, indent=2))
        return

    print(f"\n=== SIO Rule Audit (last {args.since}d violations, top {args.top}) ===\n")
    print(f"Total rules detected: {len(rules)} | scored: {len(results)}\n")
    print(f"{'#':>3} | {'Score':>5} | {'Vio':>4} | {'Enf':>3} | {'H S R M':<7} | Rule")
    print("-" * 130)
    for i, r in enumerate(top, 1):
        e = r["enforcement"]
        marks = (
            ("h" if e["hook"] else "·")
            + " " + ("s" if e["skill"] else "·")
            + " " + ("r" if e["recipe"] else "·")
            + " " + ("m" if e["memory"] else "·")
        )
        text = r["rule"][:75]
        print(f"{i:>3} | {r['score']:>5} | {r['violations']:>4} | {r['enforcement_count']:>3} | {marks:<7} | {text}")
        print(f"     | {r['file']}:{r['line']} ({r['section'][:60]})")

    print("\nLegend: h=hook s=skill r=recipe m=memory  ·=not bound")
    print("High score = high violation count + low enforcement coverage = next 6-channel-wiring candidate")
    print("\nSee `/sio-rule-audit` skill for the 6-channel-wiring pattern.")


if __name__ == "__main__":
    audit()
