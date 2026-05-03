# SIO — Self-Improving Organism

Installed by `sio init`. Source-of-truth lives inside the `self-improving-organism`
package; re-run `sio init` (or `sio init --force`) to refresh after a
`pip install -U self-improving-organism`. To opt out, edit this file freely —
SIO leaves user-modified files alone unless `--force` is passed.

## What SIO does

SIO mines your AI coding agent's session transcripts for recurring failure
patterns, clusters them, and uses DSPy to generate improvement rules — the
loop closes when an approved rule lands back in your harness's instruction
file (e.g., this file's neighbor, `~/.claude/CLAUDE.md`).

## Three signals SIO captures

| Signal | What it is | When it fires |
|---|---|---|
| **Errors / failures** | tool_failure, repeated_attempt | Tool returned non-zero / threw |
| **Friction / corrections** | user_correction, agent_admission, undo | User said "no, that's wrong" / agent self-corrected |
| **Positive flows** | recurring successful tool sequences | Same N-step combo recurs across sessions |

The third one is the part most people miss when they think SIO is "an error
tool" — flow mining is the efficiency-uplift signal, not the bugfix signal.

## Skill map

| Skill | Purpose |
|---|---|
| `/sio` | Master router — pick the right sub-skill |
| `/sio-briefing` | Session-start intel: violations + budget + declining rules + pending suggestions |
| `/sio-status` | Pipeline state (errors mined, patterns, suggestions) |
| `/sio-scan` | Mine recent sessions for errors + corrections |
| `/sio-discover` | Repo-specific skill candidates from mined patterns (workflow / guard / tool) |
| `/sio-suggest` | Generate rule suggestions from error/correction patterns (cross-project) |
| `/sio-validate` | Specialized parser: bad tool args → cascade-shield deny / auto-fix rules |
| `/sio-review` | Interactively review pending suggestions |
| `/sio-apply` | Apply an approved suggestion |
| `/sio-violations` | Detect existing CLAUDE.md rules being ignored by the agent |
| `/sio-velocity` | Per-rule effectiveness — are applied rules actually shrinking errors? |
| `/sio-budget` | Per-instruction-file size report (lines / cap / status) |
| `/sio-feedback` | Mark the last AI action ++ / -- (feeds DSPy ground-truth corpus) |
| `/sio-flows` | Discover recurring positive tool sequences |
| `/sio-distill` | Distill a long exploratory session into a clean playbook |
| `/sio-promote-flow` | Promote an existing mined flow into a SKILL.md |
| `/sio-codify-workflow` | One-shot: distill → promote-flow → optimize on the *current* session |
| `/sio-recall` | Recall how a task was solved in a previous session |
| `/sio-export` | Export structured training datasets (DSPy-ready) |

## Multi-Hop Targeted Search (`sio suggest --refine`)

When a wide-grep run returns generic / off-theme suggestions, narrow with
Hop-2:

```bash
# Hop-1 (wide): big net, lots of noise
sio suggest --grep 'tool,error,keywords' --type tool_failure --auto

# Hop-2 (narrow): keep the wide-grep error set, filter further
sio suggest --grep 'tool,error,keywords' --type tool_failure \
  --refine 'specific theme tokens' --strategy filter --auto

# Iterate without re-querying the DB
sio suggest --use-cache --refine 'different theme' --strategy hybrid
```

`--strategy filter` (default) narrows errors before clustering. Use
`--strategy hybrid` when filter over-prunes. (`--strategy recluster` is
documented but currently behaves like a stricter filter — implementation
follow-up tracked in the upstream PRD.)

## Pattern Trend (`sio trend`)

Track whether applied rules are shrinking a cluster or whether a new cluster
is emerging:

```bash
sio trend                              # weekly, top 10, last 6 weeks
sio trend --daily --top 5 --windows 14
sio trend --pattern <pattern-id>       # single pattern growth
```

Output is a compact table with one column per bucket and a trend arrow
(↑ ↓ →) on the last two buckets. Set `COLUMNS=160` if the render squashes.

## Database locations

| Database | Path | Purpose |
|---|---|---|
| Main DB | `~/.sio/sio.db` | Errors, patterns, suggestions, datasets, optimized modules |
| Per-platform DB | `~/.sio/<platform>/behavior_invocations.db` | Hook-written tool-call telemetry |
| Dataset cache | `~/.sio/datasets/<pattern_id>.json` | DSPy grounding examples (per-cycle rebuild) |
| Preview CSV | `~/.sio/previews/errors_preview.csv` | `--use-cache` source for Hop-2 iteration |

When querying SIO data directly via Python or sqlite3, use `~/.sio/sio.db`.
The `sio` CLI handles path resolution automatically.

## DSPy optimizers

Three are wired in via `sio optimize --optimizer <name>`:

| Optimizer | Flag | Best for |
|---|---|---|
| **GEPA** (default) | `--optimizer gepa` | Multi-step reasoning modules; uses reflection LM |
| **MIPROv2** | `--optimizer mipro` | Few-shot instruction optimization |
| **BootstrapFewShot** | `--optimizer bootstrap` | Fast bootstrapping with minimal labeled data |

All `dspy.LM(...)` construction goes through `sio.core.dspy.lm_factory` —
do not instantiate directly.

## When to invoke which skill

Read the *whole list* before defaulting to `/sio-suggest`. There are nine
specialized parsers below the generic suggestion path that handle specific
questions far better than `sio suggest --grep` ever can.

**Diagnosis (what's wrong / what should I know?)**
- Starting a session and wondering about pending issues → `/sio-briefing`
- "What is my agent doing wrong?" → `/sio-scan`
- "Which CLAUDE.md rules are being ignored?" → `/sio-violations`
- "Are my rules actually reducing errors?" → `/sio-velocity`
- "How much room is left in CLAUDE.md?" → `/sio-budget`
- "Pipeline status?" → `/sio-status`

**Generation (turn signal into rules / skills)**
- "What can SIO improve in THIS repo?" → `/sio-discover`  *(repo-scoped — preferred over /sio-suggest)*
- "What tool args keep failing?" → `/sio-validate`  *(specialized for tool-arg failures)*
- "Generate rules from what I've collected" → `/sio-suggest`  *(generic, cross-project)*

**Review + apply**
- "Review my suggestions" → `/sio-review`
- "Apply suggestion N" → `/sio-apply`

**Positive signal (what works)**
- "What workflows actually work for me?" → `/sio-flows`
- "Distill that session into a playbook" → `/sio-distill`
- "Promote that flow into a skill file" → `/sio-promote-flow`
- "Make a skill from the workflow we just did" → `/sio-codify-workflow`
- "How did I solve X last time?" → `/sio-recall`

**Training data + closed-loop feedback**
- "Export training data for DSPy" → `/sio-export`
- "Mark this action ++ / --" → `/sio-feedback`
