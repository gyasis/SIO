---
name: sio-status
description: Show the current state of the SIO pipeline — errors mined, patterns found, suggestions pending. Ask naturally like "how is SIO doing?" or "what's the pipeline status?".
---

# SIO Status — Pipeline Dashboard

Quick overview of the SIO pipeline state with project-aware context.

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

# Auto-detect project from current repo directory name
AUTO_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
PROJECT="${SIO_PROJECT:-$AUTO_PROJECT}"

echo "=== SIO Pipeline Status ==="
echo "  Detected project: ${PROJECT}"
echo ""

# Overall status
sio status
echo ""

# Project-specific error count
echo "Project-scoped counts for '${PROJECT}':"
sio errors --project "${PROJECT}" -n 0 2>/dev/null || echo "  No errors for this project"
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

- **0 errors** → Run `/sio-scan` first to mine sessions
- **Errors but 0 patterns** → Run `/sio-suggest` to cluster and generate
- **Patterns but 0 pending** → All suggestions reviewed, or run `/sio-suggest` again
- **Pending > 0** → Run `/sio-review` to approve/reject
- **Applied > 0** → Rules are active; re-scan to see if errors decrease

## Important Context

- The DB contains errors from ALL projects. Status shows the global view.
- Downstream commands (`errors`, `patterns`, `suggest`) auto-detect the current project.
- Datasets are non-destructively versioned: each `sio suggest` run assigns a new
  `cycle_id` (UUID) and marks prior rows `active=0` rather than deleting them
  (FR-003, Audit Round 2 C-R2.6). Historical datasets stay queryable; add
  `WHERE active=1` when you only want the current cycle. `applied_changes` are
  NEVER touched by `sio suggest`.
