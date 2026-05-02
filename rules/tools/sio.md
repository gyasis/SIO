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
| `/sio-status` | Pipeline state (errors mined, patterns, suggestions) |
| `/sio-scan` | Mine recent sessions for errors + corrections |
| `/sio-suggest` | Generate rule suggestions from error/correction patterns |
| `/sio-review` | Interactively review pending suggestions |
| `/sio-apply` | Apply an approved suggestion |
| `/sio-flows` | Discover recurring positive tool sequences |
| `/sio-discover` | Find skill candidates from recurring flow patterns |
| `/sio-distill` | Distill a long exploratory session into a clean playbook |
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

- Starting a session and wondering about pending issues → `/sio-briefing`
- "What is my agent doing wrong?" → `/sio-scan`
- "Generate rules from what I've collected" → `/sio-suggest`
- "What workflows actually work for me?" → `/sio-flows`
- "How did I solve X last time?" → `/sio-recall`
- "Make a skill from the workflow we just did" → `/sio-codify-workflow`
