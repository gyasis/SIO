---
name: sio-apply
description: Apply an approved suggestion to CLAUDE.md or other config files. Ask naturally like "apply suggestion 5" or "add that rule to my config".
---

# SIO Apply — Add Approved Rules to Your Config

When the user wants to apply an approved suggestion — write it to the target file (usually CLAUDE.md).

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
    echo "Usage: sio apply <suggestion_id>"
    echo ""
    echo "Pending suggestions:"
    sio suggest-review 2>/dev/null | head -20 || echo "  No pending suggestions. Run /sio-suggest first."
    exit 1
fi

sio apply "${SUGGESTION_ID}"
```

## Workflow

1. User runs `/sio-suggest` to generate suggestions
2. User runs `/sio-review` to approve/reject
3. User runs `/sio-apply` with the suggestion ID to write to CLAUDE.md
4. If something goes wrong: `sio rollback <change_id>`

## Safety

- Only approved suggestions can be applied
- Every applied change records diff_before/diff_after for rollback
- Changes append to files — never overwrite existing content
- Ask user to confirm before applying if the target file is not CLAUDE.md
