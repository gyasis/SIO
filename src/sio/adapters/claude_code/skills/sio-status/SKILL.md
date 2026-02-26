---
name: sio-status
description: Show the current state of the SIO pipeline — errors mined, patterns found, suggestions pending. Ask naturally like "how is SIO doing?" or "what's the pipeline status?".
---

# SIO Status — Pipeline Dashboard

Quick overview of the SIO pipeline state.

## Triggers (natural language)

- "How is SIO doing?"
- "Show SIO status"
- "What's in the pipeline?"
- "How many errors have been mined?"
- "Pipeline status"

## Execution

```bash
#!/bin/bash
set -e
sio status
```

## What the Numbers Mean

| Metric | What It Tells You |
|---|---|
| **Errors mined** | Total errors extracted from SpecStory + JSONL sessions |
| **Patterns found** | Clustered error groups (similar errors grouped together) |
| **Datasets built** | Patterns with enough examples for suggestion generation |
| **Pending reviews** | Suggestions waiting for your approval |
| **Applied changes** | Rules already written to CLAUDE.md or other files |

## Interpreting the State

- **0 errors** → Run `/sio-scan` first
- **Errors but 0 patterns** → Run `/sio-suggest` to cluster and generate
- **Patterns but 0 pending** → All suggestions reviewed, or run `/sio-suggest` again
- **Pending > 0** → Run `/sio-review` to approve/reject
- **Applied > 0** → Rules are active; monitor with `/sio-scan` to see if errors decrease
