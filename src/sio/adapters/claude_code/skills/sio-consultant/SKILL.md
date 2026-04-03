---
name: sio-consultant
description: Session-start briefing — check for known issues, declining rules, or pending suggestions before starting work.
---

# SIO Consultant — Session Briefing

At the start of a session, consider running `sio briefing` to check for known issues, declining rules, or pending suggestions in this project.

## When to Use

- Beginning of a new coding session
- After returning from a break or context switch
- Before starting a complex task (to know what rules are struggling)

## Execution

```bash
#!/bin/bash
set -e
sio briefing
```

## What It Reports

- **Recent Violations**: Rules being ignored in the last 7 days
- **Declining Rules**: Rules where error rate increased after application
- **Budget Warnings**: CLAUDE.md nearing line capacity
- **Pending Suggestions**: High-confidence improvements awaiting review
- **Session Trend**: Error count trajectory across recent sessions

If everything is clean, it prints "All clear" and you can proceed normally.
