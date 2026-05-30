---
name: sio-rule-audit
description: Audit which rules in CLAUDE.md / rules/domains/ / rules/tools/ exist as TEXT only versus which have actual ENFORCEMENT (hooks/skills/recipes/memory). Cross-references SIO violation counts to rank "rules most-violated AND least-enforced" as 6-channel-wiring candidates. Triggers on "audit rules", "which rules aren't enforced", "rule coverage", "what rules are unenforceable", "find rule gaps", "which rules need hooks", "rule-to-enforcement audit". Use to find the next high-violation cluster before it costs another long debugging session.
requires:
  cli: "sio>=0.3.0"
  skills: [sio, sio-status, sio-suggest, sio-violations]
  hooks: []
  optional: []
---

# /sio-rule-audit

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio` — master router; `/sio-status` — check pipeline health before auditing; `/sio-suggest` — generates new rules after audit surfaces gaps; `/sio-violations` — sister skill (violation detection without enforcement-coverage analysis)
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)
- **Bundled script:** `scripts/sio-rule-audit.py` (in this skill directory) — the audit runner

> **Portability note:** Originally written with hh-dev / hhdev / zeno examples in sample output; genericized for portability. The script itself scans your own `~/.claude/` tree — examples in this skill are illustrative only.

The meta-tool for the "rule exists but agent doesn't follow" pattern. Scans every rule statement across the user's instruction files and scores each by:
- **Violation count** (from SIO database, last 14 days)
- **Enforcement coverage** (which of 4 channels actually bind it: hook / skill / recipe / memory)
- **Discoverability** (which of 2 channels surface it: CLAUDE.md / rules-injector domain rule)

Outputs a ranked list of rules that are **high-violation AND low-enforcement** — these are the text-only hazards waiting to bleed time.

## When to invoke

- User asks: "what rules are being ignored?", "which rules aren't enforced?", "audit my rules"
- After SIO scan reveals a recurring violation pattern (per `/sio-violations`)
- Periodically — same way you do `/sio-status` for health check
- Before adding a new rule — ensure existing high-violation rules are addressed first

## Run it

```bash
# Bundled script (preferred — self-contained, no install needed):
python3 ~/.claude/skills/sio-rule-audit/scripts/sio-rule-audit.py                # human-readable table
python3 ~/.claude/skills/sio-rule-audit/scripts/sio-rule-audit.py --json         # machine-readable
python3 ~/.claude/skills/sio-rule-audit/scripts/sio-rule-audit.py --top 10       # top N candidates only
python3 ~/.claude/skills/sio-rule-audit/scripts/sio-rule-audit.py --since 7      # 7-day SIO window (default 14)
```

## What it scans

| Source | Pattern matched as a "rule" |
|---|---|
| `~/.claude/CLAUDE.md` | Lines containing **MUST**, **NEVER**, **ALWAYS**, **BLOCKING**, **MANDATORY**, **CRITICAL** |
| `~/.claude/rules/domains/*.md` | Same |
| `~/.claude/rules/tools/*.md` | Same |

For each detected rule, it extracts:
- The rule statement (one-line summary)
- Source file + line number
- Key terms (for matching against violation patterns)

## How it scores enforcement

For each rule, scans these channels:

| Channel | What counts as "binding" |
|---|---|
| **Hook** | A script in `~/.claude/hooks/**/*.sh` whose content references the rule's key terms |
| **Skill** | A skill in `~/.claude/skills/*/SKILL.md` whose description or body references the rule |
| **Recipe** | An entry in `~/.claude/recipes/INDEX.md` whose keywords overlap with the rule |
| **Memory** | A file in the project memory dir referencing the rule |

A rule with 0 channels = **TEXT-ONLY** (the highest-hazard state). 4 channels = fully wired.

## How it scores violations

Queries `~/.sio/sio.db` for error patterns matching the rule's key terms in the last N days (default 14). Higher count = more user time bled.

## Output

A table ranked by `(violation_count + 1) / (enforcement_count + 1)` — pure ratio of pain to wiring:

```
Rank | Rule (file:line)                                   | Violations | Enforcement | Recommendation
   1 | "NEVER run destructive op without confirmation"    |        88  | TEXT-ONLY   | 🚨 build hook
   2 | "DUAL CONFIRMATION on MCP create/edit/transition"  |       312  | TEXT-ONLY   | 🚨 build hook
   3 | "Clarify-then-execute on typo-heavy input"         |       284  | TEXT-ONLY   | 🚨 build hook
   4 | "Always check file exists before overwriting"      |         0  | hook+recipe | adequate
```

## What to do with output

1. **Top of list (TEXT-ONLY + high-violation)** = the next 6-channel-wiring candidates. Apply the same pattern: hook + skill + recipe + memory + domain rule + CLAUDE.md nudge.
2. **TEXT-ONLY + low-violation** = either the rule is well-followed already, OR the violation isn't being detected (consider adding SIO error patterns).
3. **Wired + high-violation** = the wiring isn't catching the violations. Probably has a bug or the matcher is too narrow.

## Reference for the 6-channel pattern

When wiring a rule, hit all 6 channels:

| Channel | Where | Auto-loaded? |
|---|---|---|
| 1. Hook | `~/.claude/hooks/<name>/*.sh` + `settings.json` registration | ✅ on tool call |
| 2. Skill | `~/.claude/skills/<name>/SKILL.md` | ✅ session-start listing |
| 3. Recipe | `~/.claude/recipes/<name>.md` + INDEX.md | ✅ retry-guard / proactive lookup |
| 4. Memory | project memory dir + MEMORY.md index | ✅ always-loaded |
| 5. Domain rule | `~/.claude/rules/domains/<domain>.md` | ✅ rules-injector on path match |
| 6. CLAUDE.md core | the 200-line constitution | ✅ always-loaded |

## Cross-references

- `/sio-violations` — sister skill: which CLAUDE.md rules are being violated (no enforcement-coverage analysis)
- `/sio-suggest` — generates new CLAUDE.md rules; this skill audits existing ones
