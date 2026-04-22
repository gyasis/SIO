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
| Find what's going wrong | `/sio-scan` | `sio mine` + `sio errors` | Free |
| Get improvement suggestions | `/sio-suggest` | `sio suggest` | ~$0.05 (LLM) |
| Review pending suggestions | `/sio-review` | `sio suggest-review` | Free |
| Apply a suggestion | `/sio-apply` | `sio apply` | Free |
| Check pipeline status | `/sio-status` | `sio status` | Free |
| **Discover what works** | `/sio-flows` | `sio flows` | Free |
| **Distill a session** | `/sio-distill` | `sio distill --latest` | Free |
| **Recall a workflow** | `/sio-recall` | `sio recall "query"` | Free / ~$0.02 with --polish |
| **Export training data** | `/sio-export` | `sio export-dataset --task all` | Free |

## How to Route (for the agent)

When the user asks something SIO-related, determine which sub-command to invoke:

### "What's going wrong?" / "scan for errors" / "what patterns?"
→ `/sio-scan`
Mines recent sessions, shows error breakdown, top patterns.

### "Generate rules" / "suggest improvements" / "how can I improve?"
→ `/sio-suggest`
Clusters errors into patterns, generates CLAUDE.md rules using DSPy.

### "Review suggestions" / "what did SIO recommend?"
→ `/sio-review`
Shows pending suggestions for user approval.

### "Apply that rule" / "add suggestion N"
→ `/sio-apply`
Applies an approved suggestion to CLAUDE.md or config files.

### "What patterns work?" / "show productive workflows" / "what sequences?"
→ `/sio-flows`
Discovers recurring positive tool sequences. Shows what works, not just errors.

### "Distill that session" / "extract the steps" / "create a playbook"
→ `/sio-distill`
Takes a long session, removes failures/retries, outputs a numbered playbook.

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
/sio what patterns work well?
/sio distill the latest session
/sio how did we set up dbt locally?
/sio export training data for DSPy
/sio what's the pipeline status?
```
