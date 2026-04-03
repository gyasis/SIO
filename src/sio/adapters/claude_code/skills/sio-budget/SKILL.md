---
name: sio-budget
description: Check instruction file budget usage. Ask naturally like "how much budget is left?" or "can I add more rules?".
---

# SIO Budget — Instruction File Budget Check

Run this before applying rules to check remaining budget. Shows current line and token usage of CLAUDE.md and other instruction files so you know how much room is left.

## Triggers (natural language)

- "How much budget is left?"
- "Can I add more rules?"
- "Check CLAUDE.md budget"
- "How full is my config?"
- "Show budget usage"
- "Is there room for more rules?"

## Execution

Run the SIO budget check:

```bash
#!/bin/bash
set -e
sio budget
```

## How to Interpret Results

After running, explain the metrics to the user:

| Metric | What It Means |
|---|---|
| **Lines used / limit** | Current line count vs the recommended maximum |
| **Tokens used / limit** | Estimated token count vs the context budget |
| **Utilization %** | How full the file is — above 80% means cleanup is recommended |
| **Headroom** | Remaining lines/tokens available for new rules |

Budget thresholds:
- **< 60%** = plenty of room, add rules freely
- **60-80%** = moderate usage, be selective about new rules
- **> 80%** = cleanup recommended before adding more — consider removing stale or ineffective rules
- **> 95%** = critical — new rules may push the file over the context window limit

## Follow-up Actions

Suggest next steps based on budget state:
- Budget tight? -> "Want to find ineffective rules to remove?" -> run `/sio-velocity`
- Stale rules? -> "Want to check for violated or outdated rules?" -> run `/sio-violations`
- Want to add a rule anyway? -> "Let me apply it" -> run `/sio-apply`
