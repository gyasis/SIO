# SIO Skills — Bundled Slash Commands

This folder ships **32 portable, SIO-only skills** that `sio init` stages into your
AI coding harness (`~/.claude/skills/<name>/SKILL.md`). They are the slash-command
surface for SIO's pipeline — observe → suggest → recall → optimize → measure.

> Maintained as a set. Because skills reference each other (see the graph below),
> **install the whole folder**, not individual skills.

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

**Ship gate:** every `requires.skills` entry resolves within this folder — no
unsatisfiable intra-SIO references. `requires.optional` entries are *external* and
are NOT shipped by SIO (see table below).

## The 32 skills

| Skill | What it does | Requires (skills) |
|-------|--------------|-------------------|
| `/sio` | SIO Suite — Session Intelligence Observer. Master skill that routes to the right SIO sub… | — |
| `/sio-apply` | Apply an approved suggestion to CLAUDE.md or other config files | `sio-review`, `sio-suggest` |
| `/sio-briefing` | Session-start intelligence check. Shows violations, budget warnings, declining rules, an… | `sio-budget`, `sio-review`, `sio-velocity`, `sio-violations` |
| `/sio-budget` | Check instruction file budget usage | `sio-apply`, `sio-velocity`, `sio-violations` |
| `/sio-codify-workflow` | One-shot pipeline to codify a recent successful workflow into a reusable skill — runs di… | `sio`, `sio-discover`, `sio-distill`, `sio-flows`, `sio-optimize`, `sio-promote-flow`, `sio-velocity` |
| `/sio-discover` | Find repo-specific skill candidates from mined patterns | `sio-flows`, `sio-promote-flow`, `sio-suggest` |
| `/sio-distill` | Distill a long exploratory session into a clean playbook of winning steps. Removes failu… | `sio-export` |
| `/sio-export` | Export structured training datasets from mined sessions for DSPy/ML optimization. Genera… | — |
| `/sio-feedback` | Label the last AI action with satisfaction feedback (++ or --) | — |
| `/sio-flows` | Discover recurring positive tool sequence patterns from sessions. Shows what workflows w… | `sio-distill`, `sio-export`, `sio-scan` |
| `/sio-health` | Show per-skill health metrics | — |
| `/sio-optimize` | Run DSPy prompt optimization for a skill | — |
| `/sio-promote-flow` | Promote a successful workflow to a reusable skill file | `sio-discover`, `sio-flows`, `sio-velocity` |
| `/sio-promote-rule` | Promote a violated CLAUDE.md rule (from `sio violations`) into a runtime PreToolUse hook… | `sio-violations` |
| `/sio-recall` | Recall how a task was solved in a previous session. Topic-filters distilled sessions, de… | `sio-scan` |
| `/sio-recall-flow` | SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_descr… | — |
| `/sio-recall-recovery` | SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_descr… | — |
| `/sio-recall-router` | SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_descr… | — |
| `/sio-report` | Generate a visual HTML report of SIO analysis | `sio-scan`, `sio-suggest`, `sio-velocity` |
| `/sio-review` | Interactively review pending improvement suggestions | `sio-apply` |
| `/sio-router` | SIO meta-router for prompt-engineering targets. Detects the target_surface from agent in… | `sio-rule-generator` |
| `/sio-rule-audit` | Audit which rules in CLAUDE.md / rules/domains/ / rules/tools/ exist as TEXT only versus… | `sio`, `sio-status`, `sio-suggest`, `sio-violations` |
| `/sio-rule-generator` | SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_descr… | — |
| `/sio-scan` | Mine and analyze recent Claude Code session errors | `sio-suggest` |
| `/sio-search` | Search session history across all six coding-agent harnesses, or scope any SIO analysis … | `sio-scan`, `sio-suggest` |
| `/sio-status` | Show the current state of the SIO pipeline — errors mined, patterns found, suggestions p… | `sio-review`, `sio-scan`, `sio-suggest` |
| `/sio-suggest` | Generate targeted CLAUDE.md rules from mined error patterns | `sio-review` |
| `/sio-suggestion-generator` | SIO-distilled CLAUDE.md rule generator. Given a Claude Code error pattern (pattern_descr… | — |
| `/sio-validate` | Generate tool argument validation rules from SIO error patterns. Mines the SIO database … | `sio` |
| `/sio-velocity` | Check if applied rules are actually reducing errors | `sio-budget`, `sio-scan`, `sio-suggest` |
| `/sio-violations` | Detect when rules in CLAUDE.md are being violated | `sio-budget`, `sio-scan`, `sio-suggest` |
| `/sio-watch` | Live-tail a coding-agent session's events in real time — see tool calls and responses as… | `sio-search` |

## Dependency graph (who depends on whom)

**Hub skills** (most depended-on — install no matter what):

- **`/sio-suggest`** ← 9 dependents: `sio-apply`, `sio-discover`, `sio-report`, `sio-rule-audit`, `sio-scan`, `sio-search`, `sio-status`, `sio-velocity`, `sio-violations`
- **`/sio-scan`** ← 7 dependents: `sio-flows`, `sio-recall`, `sio-report`, `sio-search`, `sio-status`, `sio-velocity`, `sio-violations`
- **`/sio-velocity`** ← 5 dependents: `sio-briefing`, `sio-budget`, `sio-codify-workflow`, `sio-promote-flow`, `sio-report`
- **`/sio-review`** ← 4 dependents: `sio-apply`, `sio-briefing`, `sio-status`, `sio-suggest`
- **`/sio-violations`** ← 4 dependents: `sio-briefing`, `sio-budget`, `sio-promote-rule`, `sio-rule-audit`
- **`/sio`** ← 3 dependents: `sio-codify-workflow`, `sio-rule-audit`, `sio-validate`

**Leaf skills** (self-contained, no intra-SIO deps): `/sio`, `/sio-export`, `/sio-feedback`, `/sio-health`, `/sio-optimize`, `/sio-recall-flow`, `/sio-recall-recovery`, `/sio-recall-router`, `/sio-rule-generator`, `/sio-suggestion-generator`

## Cross-agent search & live watch (newest surface)

- **`/sio-search`** — search session history across 6 harnesses (`--agent all|claude|codex|goose|opencode|gemini|aider`) and scope any analysis to one session via `--session <handle>`.
- **`/sio-watch`** — live-tail an in-progress session (`sio watch --session`). Live streaming currently supports the Claude harness.
See `docs/user-guide.md` → *Cross-Agent Search & Session-Scoped Analysis* for the full reference.

## Hooks SIO ships (surfaced)

SIO's skills read data captured by **5 lifecycle hooks** that `sio init` registers into
Claude Code's `settings.json`. These are a **global install prerequisite** — the whole
pipeline depends on them capturing telemetry — not a per-skill dependency:

| Hook event | SIO hook | Purpose |
|------------|----------|---------|
| `SessionStart` | `session_start.py` | Recall briefing / context priming |
| `PostToolUse` | `post_tool_use.py` | **Core telemetry** → `~/.sio/<platform>/behavior_invocations.db` |
| `PreCompact` | `pre_compact.py` | Capture discoveries before compaction |
| `Stop` | `stop.py` | Session-boundary bookkeeping |
| `UserPromptSubmit` | `user_prompt_submit.py` | Prompt-level signal capture |

Verify with `sio doctor` / `sio status` (Hooks section). Source: `src/sio/adapters/claude_code/hooks/`.

## External / optional references (NOT shipped by SIO)

Declared `requires.optional` — the skill works without them, or clearly flags the gap:

| Skill | External ref | Notes |
|-------|--------------|-------|
| `/sio` · `/sio-router` | `prd` | Incidental PRD-workflow mention; not required |
| `/sio-recall` | `memory-search` | Enhances recall if you also run the memory-search skill |
| `/sio-validate` | `cascade-shield` | **Bridge skill** — drives an external tool-arg-validation hook SIO does *not* ship; install cascade-shield separately |

## Special skills — locally regenerated

`/sio-recall-flow`, `/sio-recall-recovery`, `/sio-recall-router` are **DSPy-rendered
stubs**: their learned prompt lives in your local `~/.sio/sio.db` (optimized-module
rows), not in the SKILL.md. On a fresh machine they're inert until regenerated with
`sio render --module-id <N>`. They ship as placeholders so the slash command exists.

## Portability

All domain-specific examples were genericized; affected skills carry a
`> **Portability note:**` in their Dependencies section. No machine-specific absolute
paths, usernames, or secrets are shipped.
