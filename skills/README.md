# SIO Skills — Bundled Slash Commands

This folder ships **29 portable, SIO-only skills** that `sio init` stages into your
AI coding harness (`~/.claude/skills/<name>/SKILL.md`). They are the slash-command
surface for SIO's pipeline — observe → suggest → recall → optimize → measure.

> Generated/maintained as a set. Because skills reference each other (see the graph
> below), **install the whole folder**, not individual skills.

## Install

```bash
pip install -e .      # or your install path
sio init              # stages these skills + registers SIO's hooks
# restart Claude Code so newly-staged skills appear as slash commands
```

## Dependency model

Every skill declares a `requires:` block in its frontmatter:

```yaml
requires:
  cli: "sio>=0.3.0"     # the sio CLI must be installed
  skills: [sio-scan]    # other SIO skills this one references (ship together)
  hooks: []             # SIO's telemetry hooks (global; see below) — rarely per-skill
  optional: [prd]       # enhances if present, safe to skip if absent
```

**Ship gate:** every `requires.skills` entry resolves within this folder — there are
no unsatisfiable intra-SIO references. `requires.optional` entries are *external* and
are NOT shipped by SIO (see the table further down).

## The 29 skills

| Skill | What it does | Requires (skills) |
|-------|--------------|-------------------|
| `/sio` | "SIO Suite — Session Intelligence Observer. Master skill that routes to the right SIO sub-command | `sio-apply`, `sio-codify-workflow`, `sio-discover`, `sio-distill`, `sio-export`, `sio-flows`, `sio-recall`, `sio-review`, `sio-scan`, `sio-status`, `sio-suggest`, `sio-violations` |
| `/sio-apply` | Apply an approved suggestion to CLAUDE.md or other config files | `sio-review`, `sio-suggest` |
| `/sio-briefing` | Session-start intelligence check. Shows violations, budget warnings, declining rules, and pending suggestions | `sio-budget`, `sio-review`, `sio-velocity`, `sio-violations` |
| `/sio-budget` | Check instruction file budget usage | `sio-apply`, `sio-velocity`, `sio-violations` |
| `/sio-codify-workflow` | One-shot pipeline to codify a recent successful workflow into a reusable skill — runs distill → promote → optimize with confirmation between steps. Use when the user says "codify this", "save this workflow as a skill", "turn what I just did into a skill", "make a skill from this session" | `sio`, `sio-discover`, `sio-distill`, `sio-flows`, `sio-optimize`, `sio-promote-flow`, `sio-velocity` |
| `/sio-discover` | Find repo-specific skill candidates from mined patterns | `sio-flows`, `sio-promote-flow`, `sio-suggest` |
| `/sio-distill` | Distill a long exploratory session into a clean playbook of winning steps. Removes failures, retries, dead ends | `sio-export` |
| `/sio-export` | Export structured training datasets from mined sessions for DSPy/ML optimization. Generates routing, recovery, and flow prediction training pairs | — |
| `/sio-feedback` | Label the last AI action with satisfaction feedback (++ or --) | — |
| `/sio-flows` | Discover recurring positive tool sequence patterns from sessions. Shows what workflows work well, not just errors | `sio-distill`, `sio-export`, `sio-scan` |
| `/sio-health` | Show per-skill health metrics | — |
| `/sio-optimize` | Run DSPy prompt optimization for a skill | — |
| `/sio-promote-flow` | Promote a successful workflow to a reusable skill file | `sio-discover`, `sio-flows`, `sio-velocity` |
| `/sio-recall` | Recall how a task was solved in a previous session. Topic-filters distilled sessions, detects struggle→fix transitions, polishes via Gemini. Ask "how did we do X?" or "recall the setup workflow" | `sio-scan` |
| `/sio-recall-flow` | "SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_description + example_errors + project_context), produces a concise, testable rule. Optimized by SIO BOOTSTRAP 2026-03-25, score=1.0000." | — |
| `/sio-recall-recovery` | "SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_description + example_errors + project_context), produces a concise, testable rule. Optimized by SIO BOOTSTRAP 2026-03-25, score=1.0000." | — |
| `/sio-recall-router` | "SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_description + example_errors + project_context), produces a concise, testable rule. Optimized by SIO BOOTSTRAP 2026-03-25, score=1.0000." | — |
| `/sio-report` | Generate a visual HTML report of SIO analysis | `sio-scan`, `sio-suggest`, `sio-velocity` |
| `/sio-review` | Interactively review pending improvement suggestions | `sio-apply` |
| `/sio-router` | "SIO meta-router for prompt-engineering targets. Detects the target_surface from agent intent (claude_md_rule / skill_update / hook_config / mcp_config / settings_config / agent_profile / project_config) and dispatches to the appropriate trained SIO skill, falling back to /sio-rule-generator for un-trained surfaces." | `sio-rule-generator` |
| `/sio-rule-audit` | Audit which rules in CLAUDE.md / rules/domains/ / rules/tools/ exist as TEXT only versus which have actual ENFORCEMENT (hooks/skills/recipes/memory). Cross-references SIO violation counts to rank "rules most-violated AND least-enforced" as 6-channel-wiring candidates. Triggers on "audit rules", "which rules aren't enforced", "rule coverage", "what rules are unenforceable", "find rule gaps", "which rules need hooks", "rule-to-enforcement audit". Use to find the next high-violation cluster before it costs another long debugging session | `sio`, `sio-status`, `sio-suggest`, `sio-violations` |
| `/sio-rule-generator` | "SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_description + example_errors + project_context), produces a concise, testable rule. Optimized by SIO GEPA 2026-05-16, score=0.8653." | — |
| `/sio-scan` | Mine and analyze recent Claude Code session errors | `sio-suggest` |
| `/sio-status` | Show the current state of the SIO pipeline — errors mined, patterns found, suggestions pending | `sio-review`, `sio-scan`, `sio-suggest` |
| `/sio-suggest` | Generate targeted CLAUDE.md rules from mined error patterns | `sio-review` |
| `/sio-suggestion-generator` | "SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_description + example_errors + project_context), produces a concise, testable rule. Optimized by SIO BOOTSTRAP 2026-05-15, score=1.0000." | — |
| `/sio-validate` | Generate tool argument validation rules from SIO error patterns. Mines the SIO database for recurring tool_failure errors caused by bad arguments and proposes deny/auto-fix rules for the validate-args.js hook | `sio` |
| `/sio-velocity` | Check if applied rules are actually reducing errors | `sio-budget`, `sio-scan`, `sio-suggest` |
| `/sio-violations` | Detect when rules in CLAUDE.md are being violated | `sio-budget`, `sio-scan`, `sio-suggest` |

## Dependency graph (who depends on whom)

**Hub skills** (most depended-on — install these no matter what):

- **`/sio-suggest`** ← 9 dependents: `sio`, `sio-apply`, `sio-discover`, `sio-report`, `sio-rule-audit`, `sio-scan`, `sio-status`, `sio-velocity`, `sio-violations`
- **`/sio-scan`** ← 7 dependents: `sio`, `sio-flows`, `sio-recall`, `sio-report`, `sio-status`, `sio-velocity`, `sio-violations`
- **`/sio-review`** ← 5 dependents: `sio`, `sio-apply`, `sio-briefing`, `sio-status`, `sio-suggest`
- **`/sio-velocity`** ← 5 dependents: `sio-briefing`, `sio-budget`, `sio-codify-workflow`, `sio-promote-flow`, `sio-report`
- **`/sio-flows`** ← 4 dependents: `sio`, `sio-codify-workflow`, `sio-discover`, `sio-promote-flow`
- **`/sio-violations`** ← 4 dependents: `sio`, `sio-briefing`, `sio-budget`, `sio-rule-audit`

**Leaf skills** (no intra-SIO dependencies — self-contained): `/sio-export`, `/sio-feedback`, `/sio-health`, `/sio-optimize`, `/sio-recall-flow`, `/sio-recall-recovery`, `/sio-recall-router`, `/sio-rule-generator`, `/sio-suggestion-generator`

## Hooks SIO ships (surfaced)

SIO's skills read data captured by **5 lifecycle hooks** that `sio init` registers
into Claude Code's `settings.json`. These are a **global install prerequisite** — the
whole pipeline depends on them capturing session telemetry — not a per-skill dependency:

| Hook event | SIO hook | Purpose |
|------------|----------|---------|
| `SessionStart` | `session_start.py` | Recall briefing / context priming |
| `PostToolUse` | `post_tool_use.py` | **Core telemetry** → writes `~/.sio/<platform>/behavior_invocations.db` |
| `PreCompact` | `pre_compact.py` | Capture discoveries before context compaction |
| `Stop` | `stop.py` | Session-boundary bookkeeping |
| `UserPromptSubmit` | `user_prompt_submit.py` | Prompt-level signal capture |

Verify with `sio doctor` / `sio status` (Hooks section). Source: `src/sio/adapters/claude_code/hooks/`.

## External / optional references (NOT shipped by SIO)

These are declared `requires.optional` — the skill works without them, or clearly flags the gap:

| Skill | External ref | Notes |
|-------|--------------|-------|
| `/sio` | `prd` | Incidental mention of the PRD workflow; not required |
| `/sio-router` | `prd` | Same — demoted to optional |
| `/sio-recall` | `memory-search` | Enhances recall if you also run the memory-search skill |
| `/sio-validate` | `cascade-shield` | **Bridge skill** — drives an external tool-arg-validation hook that SIO does *not* ship. SIO supplies the error data; install cascade-shield separately to use the generator. |

## Special skills — locally regenerated

`/sio-recall-flow`, `/sio-recall-recovery`, `/sio-recall-router` are **DSPy-rendered
stubs**: their learned prompt lives in your local `~/.sio/sio.db` (optimized-module
rows), not in the SKILL.md. On a fresh machine they are inert until regenerated with
`sio render --module-id <N>` (or the relevant optimize/recall flow). They ship as
placeholders so the slash command exists; the content is machine-local by design.

## Portability

All domain-specific examples (originally referencing the author's other projects) were
genericized for portability; affected skills carry a `> **Portability note:**` line in
their Dependencies section. No machine-specific absolute paths or secrets are shipped.
