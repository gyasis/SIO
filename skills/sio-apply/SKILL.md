---
name: sio-apply
description: Apply an approved suggestion to CLAUDE.md or other config files. Ask naturally like "apply suggestion 5" or "add that rule to my config".
---

# SIO Apply — Add Approved Rules to Your Config

Write an approved suggestion to its target file (usually CLAUDE.md). Every change is recorded with before/after diffs for safe rollback.

## Triggers (natural language)

- "Apply suggestion 5"
- "Add that rule to my CLAUDE.md"
- "Install suggestion #3"
- "Apply the top suggestion"
- "Put that fix into my config"

## Execution

```bash
#!/bin/bash
set -e

SUGGESTION_ID="${SIO_SUGGESTION_ID}"

if [ -z "$SUGGESTION_ID" ]; then
    echo "=== SIO Apply ==="
    echo ""
    echo "No suggestion ID provided. Showing pending approved suggestions:"
    echo ""
    sio suggest-review 2>/dev/null | head -30 || echo "  No pending suggestions. Run /sio-suggest first."
    echo ""
    echo "Usage: Set SIO_SUGGESTION_ID=<id> and run /sio-apply"
    echo "   Or: sio apply <id>"
    exit 0
fi

echo "=== Applying Suggestion #${SUGGESTION_ID} ==="
echo ""
sio apply "${SUGGESTION_ID}"
```

## Full Workflow

```
/sio-scan     → Mine errors from recent sessions
/sio-suggest  → Cluster → dataset → generate CLAUDE.md rules
/sio-review   → Approve or reject each suggestion
/sio-apply    → Write approved rules to target files
```

## Safety

- **Only approved suggestions** can be applied — rejected ones are archived
- **Diff tracking** — Every applied change records `diff_before` / `diff_after`
- **Rollback** — Undo any change with `sio rollback <change_id>`
- **Append-only** — Changes append to files, never overwrite existing content
- **Confirmation** — If target file is NOT CLAUDE.md, confirm with user first

## Rollback

If a rule causes problems:
```bash
sio changes                    # List all applied changes
sio rollback <change_id>       # Undo a specific change
```

## Applied Changes

View what's been applied:
```bash
sio changes                    # Shows all applied rules with status
```
