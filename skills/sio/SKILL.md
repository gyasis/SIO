---
name: sio
description: "SIO Suite — Session Intelligence Observer. Master skill that routes to the right SIO sub-command. Say 'sio' with any question about sessions, errors, patterns, workflows, training data, or recall."
user-invocable: true
---

# SIO — Session Intelligence Observer

SIO mines your Claude Code session history to find errors, discover workflows, distill playbooks, recall solutions, and export training data. This master skill routes your request to the right tool.

## Quick Reference

| What you want | Slash command | CLI command | Cost |
|--------------|--------------|-------------|------|
| Session-start intel briefing | `/sio-briefing` | `sio briefing` | Free |
| Find what's going wrong | `/sio-scan` | `sio mine` + `sio errors` | Free |
| Find skill candidates for THIS repo | `/sio-discover` | `sio discover` | Free |
| Get improvement suggestions | `/sio-suggest` | `sio suggest` | ~$0.05 (LLM) |
| Generate tool-arg validation rules | `/sio-validate` | (cascade-shield generator) | Free |
| Review pending suggestions | `/sio-review` | `sio suggest-review` | Free |
| Apply a suggestion | `/sio-apply` | `sio apply` | Free |
| Check rules being violated | `/sio-violations` | `sio violations` | Free |
| Check if rules are reducing errors | `/sio-velocity` | `sio velocity` | Free |
| Check instruction-file budget | `/sio-budget` | `sio budget` | Free |
| Label last AI action (++ / --) | `/sio-feedback` | `sio feedback` | Free |
| Check pipeline status | `/sio-status` | `sio status` | Free |
| **Discover what works** | `/sio-flows` | `sio flows` | Free |
| **Distill a session** | `/sio-distill` | `sio distill --latest` | Free |
| **Promote a flow into a skill file** | `/sio-promote-flow` | `sio promote-flow` | Free |
| **Codify a successful workflow as a new skill** | `/sio-codify-workflow` | distill + promote + optimize | ~$0.05 (LLM) |
| **Recall a workflow** | `/sio-recall` | `sio recall "query"` | Free / ~$0.02 with --polish |
| **Export training data** | `/sio-export` | `sio export-dataset --task all` | Free |

## How to Route (for the agent)

When the user asks something SIO-related, determine which sub-command to invoke.
**Read this whole list before defaulting to `/sio-suggest`** — there are 9
specialized parsers below the generic suggestion path that handle specific
questions far better than `sio suggest --grep` ever can.

### "What should I know?" / "any issues to be aware of?" / "session briefing"
→ `/sio-briefing`
Composite session-start intel: violations, budget warnings, declining rules,
pending suggestions — one screen.

### "What's going wrong?" / "scan for errors" / "what patterns?"
→ `/sio-scan`
Mines recent sessions, shows error breakdown, top patterns.

### "What can SIO improve in THIS repo?" / "find improvement opportunities" / "discover skill candidates"
→ `/sio-discover`
Repo-specific skill-candidate finder. Categorises patterns into workflow
skills, guard rules, and tool patterns. **Use this — not `/sio-suggest` —
when the question is repo-scoped.**

### "Generate rules" / "suggest improvements" / "how can I improve my agent?"
→ `/sio-suggest`
Clusters errors into patterns, generates CLAUDE.md rules using DSPy.
Generic, cross-project. Prefer `/sio-discover` for repo-specific work and
`/sio-validate` for tool-arg failures.

### "What tool args keep failing?" / "generate validation rules" / "update validations from errors"
→ `/sio-validate`
Specialized parser for `tool_failure` errors caused by bad arguments.
Proposes deny / auto-fix rules for the cascade-shield `validate-args.js`
hook. Cleaner output than `sio suggest` for this specific class.

### "Review suggestions" / "what did SIO recommend?"
→ `/sio-review`
Shows pending suggestions for user approval.

### "Apply that rule" / "add suggestion N"
→ `/sio-apply`
Applies an approved suggestion to CLAUDE.md or config files.

### "Which rules are being ignored?" / "check for rule violations"
→ `/sio-violations`
Detects when rules already in CLAUDE.md are being violated by the agent.
Different from errors: violations mean the rule exists but isn't being
followed; errors mean no rule exists yet.

### "Are my rules working?" / "check rule effectiveness"
→ `/sio-velocity`
Per-rule learning velocity — how fast error rates drop after a rule lands.
The closed-loop signal that says whether the SIO loop is actually working.

### "How much budget is left?" / "can I add more rules?" / "check CLAUDE.md size"
→ `/sio-budget`
Per-file instruction-file usage report (lines / cap / status). Run before
`/sio-apply` if CLAUDE.md is getting large.

### "++ / --" / "label the last action" / "give feedback"
→ `/sio-feedback`
Marks the last AI action with satisfaction. Feeds the ground-truth corpus
that DSPy uses for `sio optimize`.

### "What patterns work?" / "show productive workflows" / "what sequences?"
→ `/sio-flows`
Discovers recurring positive tool sequences. Shows what works, not just errors.

### "Distill that session" / "extract the steps" / "create a playbook"
→ `/sio-distill`
Takes a long session, removes failures/retries, outputs a numbered playbook.

### "Promote that flow" / "turn that workflow into a skill"
→ `/sio-promote-flow`
Single-step: take an existing mined flow and write it out as a SKILL.md.

### "Codify this" / "save this workflow as a skill" / "make a skill from what we just did"
→ `/sio-codify-workflow`
One-shot pipeline: distill → promote-flow → optimize, with confirmation
between steps. Use when the *current* session is the one to be codified.

### "How did we do X?" / "recall the dbt setup" / "what was the fix for Y?"
→ `/sio-recall`
Topic-filters a distilled session, detects struggle→fix transitions, optionally polishes via Gemini.

### "Export training data" / "DSPy dataset" / "routing pairs"
→ `/sio-export`
Exports structured JSONL/Parquet datasets for ML training.

### "How is SIO doing?" / "pipeline status"
→ `/sio-status`
Shows current state: errors mined, patterns found, suggestions pending.

## The SIO Pipeline (How It All Connects)

```
Session JSONL transcripts
    ↓
sio mine (parse + extract errors + extract flows)
    ↓                              ↓
sio errors (error analysis)    sio flows (positive patterns)
    ↓                              ↓
sio suggest (generate rules)   sio distill (session → playbook)
    ↓                              ↓
sio apply (write rules)        sio recall (topic filter + polish)
                                   ↓
                               sio export-dataset (ML training data)
                                   ↓
                               sio optimize-suggestions (GEPA / MIPROv2 /
                                                         BootstrapFewShot)
```

## Two Tiers

| Tier | Commands | Cost | Engine |
|------|----------|------|--------|
| **Cheap** | mine, errors, flows, distill, recall (no --polish), export | $0 | Regex + SQLite |
| **Expensive** | suggest, recall --polish, optimize-suggestions | ~$0.02-0.50 | LLM (task + reflection for GEPA) |

## Data Locations

| Data | Location |
|------|----------|
| SIO database | `~/.sio/sio.db` |
| Exported datasets | `~/.sio/datasets/` |
| Session transcripts | `~/.claude/projects/*/` |
| SpecStory history | `~/.specstory/history/` |

## Examples

```
/sio scan for errors in the last week
/sio what should I know to start this session?
/sio what can SIO improve in this repo?
/sio what tool args keep failing?
/sio which rules are being ignored?
/sio are my rules actually reducing errors?
/sio how much room is left in CLAUDE.md?
/sio what patterns work well?
/sio distill the latest session
/sio codify the workflow we just did
/sio how did we set up dbt locally?
/sio export training data for DSPy
/sio what's the pipeline status?
```
