---
name: sio
description: "SIO Suite — Session Intelligence Observer. Master skill that routes to the right SIO sub-command. Say 'sio' with any question about sessions, errors, patterns, workflows, training data, curation, optimization, or recall."
user-invocable: true
requires:
  cli: "sio>=0.3.0"
  skills: [sio-apply, sio-codify-workflow, sio-discover, sio-distill, sio-export, sio-flows, sio-recall, sio-review, sio-scan, sio-status, sio-suggest, sio-violations]
  hooks: []
  optional: [prd]
---

# SIO — Session Intelligence Observer

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio-apply`, `/sio-codify-workflow`, `/sio-discover`, `/sio-distill`, `/sio-export`, `/sio-flows`, `/sio-recall`, `/sio-review`, `/sio-scan`, `/sio-status`, `/sio-suggest`, `/sio-violations` — all sub-skills routed by this master skill
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)
- **Optional:** `/prd` — referenced for cross-session plan tracking; skill works without it

SIO mines your Claude Code session history to find errors, classify them, curate
training data, optimize DSPy modules against that data, apply suggestions, and
measure whether the rules actually work. This master skill routes your request
to the right tool.

**Last updated:** 2026-05-15 — added the curate / optimize / baseline-against / by-rule pipeline.

## Decision Tree — Pick ONE Path (read this first)

Match the user's situation to ONE row. Do not list every skill — pick the path and run it.

| If the user just... | Run this | Why |
|---|---|---|
| Finished a useful workflow and wants to save it | `/sio-codify-workflow` | One-shot: distill → promote → optimize |
| Wants to TRAIN a better DSPy module | `sio curate` → `sio optimize --trainset-file --baseline-against` | The 2026-05-15 canonical training loop |
| Asked "what's broken?" or "scan errors" | `/sio-scan` | Mine recent failures |
| Asked "what cognitive failures recur?" | `sio analyze same-error` | Find error signatures repeated ≥N times |
| Asked "how do I improve?" / "generate rules" | `/sio-suggest` → `/sio-review` → `/sio-apply` | Error-pattern → rule pipeline |
| Asked "how did we do X before?" | `/sio-recall "X"` | Topic search over distilled sessions |
| Asked "what workflows actually work?" | `/sio-flows` or `sio differential-flows` | Positive-pattern miner / twin-flow finder |
| Asked "is anything skill-worthy in this repo?" | `/sio-discover` | Skill candidate detector |
| Asked "are my rules working?" | `sio velocity --by-rule` or `/sio-violations` | Per-rule attribution; rule-effectiveness |
| Asked "what's SIO doing?" / status check | `/sio-status` and `sio doctor` | Pipeline state + DSPy liveness |
| Wants to BULK apply high-confidence rules | `sio apply --auto-threshold 0.9 --skip-dupes` | Bulk-apply with regression-safe defaults |
| Doesn't know what to ask — open question | Stay in `/sio`, show table below | Let them browse |

## The Canonical Training Loop (NEW 2026-05-15)

This is the production value path. Use it when the user says "improve SIO" / "train a better module" / "I want suggestions to be smarter."

```
sio mine                                  # ingest + auto-classify + active-rules snapshot
sio curate --emphasis --classified        # build a targeted JSONL trainset
sio optimize --trainset-file <jsonl> \    # train DSPy against it
            --baseline-against <prior_id>  # refuse to promote on regression
sio doctor                                # verify DSPy pipeline is alive (real score, not trivial 1.0)
sio velocity --by-rule                    # per-rule attribution over time
```

**Why this matters:** before 2026-05-15, `sio optimize` would silently fall back to template mode + score trivial 1.0 on a broken metric. Now it actually trains, scores realistically, and refuses to promote a regression. See `docs/cookbook-2026-05-15.md` in the SIO repo.

## Ordered Priority for Ambiguous Requests

When the user's intent is unclear, prefer skills in this order (most-likely-useful first):

1. **`/sio-status`** — cheapest, shows what's available to act on
2. **`sio doctor`** — is the pipeline alive? specifically DSPy module
3. **`/sio-scan`** — if any errors mentioned
4. **`sio analyze same-error`** — if "recurring" / "same issue" / "keep hitting" mentioned
5. **`/sio-recall`** — if past work referenced
6. **`/sio-flows`** or **`sio differential-flows`** — if "workflow" / "pattern" / "what works" mentioned
7. **`/sio-codify-workflow`** — if "save this" / "make a skill" / "codify" mentioned
8. **`/sio-suggest`** — only after `/sio-scan` has surfaced patterns

## Quick Reference

| What you want | Slash command / CLI | Cost |
|---|---|---|
| Find what's going wrong | `/sio-scan` / `sio mine` + `sio errors` | Free |
| **Find recurring cognitive failures** | `sio analyze same-error --min-count 5` | Free |
| Get improvement suggestions | `/sio-suggest` / `sio suggest` | ~$0.05 (LLM) |
| Review pending suggestions | `/sio-review` / `sio suggest-review` | Free |
| Apply one suggestion | `/sio-apply N` / `sio apply N` | Free |
| **Bulk apply high-confidence rules** | `sio apply --auto-threshold 0.9 --skip-dupes` | Free |
| Check pipeline status | `/sio-status` / `sio status` | Free |
| **Check DSPy is alive (real score)** | `sio doctor` | Free |
| **Build a targeted training set** | `sio curate --emphasis --classified --since "30 days"` | Free |
| **Train DSPy with regression gate** | `sio optimize --trainset-file <jsonl> --baseline-against <id>` | ~$1 (Gemini) |
| **Synthetic amplify** (v1, has regression risk) | `sio amplify -i <jsonl> -o <out>` | ~$0.20 (Gemini Flash) |
| Per-rule effectiveness | `sio velocity --by-rule` | Free |
| Discover positive workflows | `/sio-flows` / `sio flows` | Free |
| **Twin-flow paired success/failure** | `sio differential-flows --positives-for-builder` | Free |
| **Promote ground-truth positives** | `sio promote-positives` | Free |
| Promote behavior_invocations to gold | `sio promote-to-gold --all-eligible` | Free |
| Distill a session | `/sio-distill` / `sio distill --latest` | Free |
| Recall a workflow | `/sio-recall "query"` | Free / ~$0.02 with --polish |
| Export training data | `/sio-export` / `sio export-dataset --task all` | Free |

## How to Route (sub-skill triggers — for the agent)

### "What's going wrong?" / "scan for errors" / "what patterns?"
→ `/sio-scan` — Mines recent sessions, shows error breakdown, top patterns.

### "What recurring errors do I have?" / "same issue keeps happening"
→ `sio analyze same-error --min-count 5 --with-context` — Surfaces normalised error signatures seen ≥N times, optionally with context.

### "Generate rules" / "suggest improvements"
→ `/sio-suggest` — Clusters errors → patterns → DSPy-generated CLAUDE.md rules.

### "Review suggestions" / "what did SIO recommend?"
→ `/sio-review` — Shows pending suggestions.

### "Apply that rule" / "add suggestion N"
→ `/sio-apply N` — Applies one. For bulk: `sio apply --auto-threshold 0.9`.

### "Train a better module" / "optimize against curated data"
→ NEW path: `sio curate` → `sio optimize --trainset-file <out> --baseline-against <prior>` — The 2026-05-15 way.

### "What patterns work?" / "show productive workflows"
→ `/sio-flows` — Recurring positive tool sequences.

### "Find pairs of success and failure for the same sequence"
→ `sio differential-flows --positives-for-builder` — Twin flows; populates positive side of training datasets.

### "Distill that session" / "create a playbook"
→ `/sio-distill` — Removes failures/retries, outputs numbered playbook.

### "How did we do X?" / "recall a previous workflow"
→ `/sio-recall` — Topic filter + struggle→fix detection.

### "Export training data"
→ `/sio-export` — Structured JSONL/Parquet datasets.

### "How is SIO doing?" / "pipeline status"
→ `/sio-status` plus `sio doctor` — Status counters + DSPy liveness check.

### "Are my rules working?" / "per-rule effectiveness"
→ `sio velocity --by-rule --min-records 10` — JOINs error_records.active_rules to compute pre/post deltas per rule. Data accumulates as rules churn.

### "Promote positive signals to training data"
→ `sio promote-positives` (gratitude/confirmation signals → ground_truth pending). After: `sio approve <id>`.

## The SIO Pipeline (How It All Connects — 2026-05-15)

```
                Session JSONL transcripts
                          ↓
                       sio mine
              (parse + classify-on-mine + stamp active_rules)
                          ↓
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
   sio errors         sio flows       sio analyze same-error
   (error browse)     (positive seq)  (cognitive failures)
        ↓                 ↓
   sio suggest      sio differential-flows
   (raw → rules)    (twin success/failure pairs)
        ↓                 ↓
   sio approve      sio promote-positives
   (→ ground_truth) (positive signals → pending)
                          ↓
                    sio curate
            (composable filters → trainset JSONL)
                          ↓
              sio optimize --trainset-file
                  --baseline-against <prior>
                          ↓                 ↓
                   PROMOTED            REGRESSED
                          ↓                 ↓
              sio apply             (auto is_active=0)
              (--auto-threshold)
                          ↓
                sio velocity --by-rule
                (per-rule deltas via active_rules)
```

## Two Tiers (Cost)

| Tier | Commands | Cost | Engine |
|---|---|---|---|
| **Cheap** | mine, errors, flows, distill, recall (no --polish), export, curate, analyze, differential-flows, promote-positives, promote-to-gold, apply, status, doctor, velocity | $0 | Regex + SQLite |
| **LLM** | suggest, optimize, amplify, recall --polish | ~$0.05-1.00 | Gemini Pro / Flash via litellm |

## Data Locations

| Data | Location |
|---|---|
| SIO database | `~/.sio/sio.db` |
| Exported datasets | `~/.sio/datasets/` |
| **Curated trainsets** | `~/.sio/curated/` |
| **Amplified trainsets** | `~/.sio/amplified/` |
| **Differential-flow JSONL** | `~/.sio/differential/` |
| Optimized DSPy artifacts | `~/.sio/optimized/` |
| LLM config | `~/.sio/config.toml` |
| Session transcripts | `~/.claude/projects/*/` |
| SpecStory history | `~/.specstory/history/` |

## Examples

```
/sio scan for errors in the last week
/sio what patterns recur 5+ times?              → sio analyze same-error
/sio build a training set for the Edit-bug      → sio curate --pattern-prefix tool_failure__readbeforeedit
/sio train a new module against this trainset   → sio optimize --trainset-file ... --baseline-against ...
/sio is DSPy actually working?                  → sio doctor
/sio are the new rules reducing errors?         → sio velocity --by-rule
/sio distill the latest session
/sio how did we set up the local pipeline?
/sio export training data for DSPy
```

## Related Documentation

- **Cookbook:** `docs/cookbook-2026-05-15.md` (in the SIO repo) — full canonical loop with examples
- **SIO hooks:** registered by `sio init` — see `sio doctor` for current hook status
