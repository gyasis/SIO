---
name: sio-briefing
description: Session-start intelligence check. Shows violations, budget warnings, declining rules, and pending suggestions. Ask naturally like "what should I know?" or "any issues to be aware of?".
---

# SIO Briefing — Session-Start Intelligence Check

Run this at the start of a session to check for known issues. Shows violations, budget warnings, declining rules, and pending suggestions so you know what to watch for.

## Triggers (natural language)

- "What should I know before we start?"
- "Any issues to be aware of?"
- "Session briefing"
- "What's the current state of things?"
- "Start-of-session check"
- "Brief me on agent health"

## Execution

Run the SIO briefing command:

```bash
#!/bin/bash
set -e
sio briefing
```

## How to Interpret Results

After running, explain the sections to the user:

| Section | What It Means |
|---|---|
| **Violations** | Rules in CLAUDE.md that are being ignored in recent sessions |
| **Budget warnings** | Instruction files nearing their line/token limits |
| **Declining rules** | Applied rules that are not reducing errors (may need revision) |
| **Pending suggestions** | Improvement suggestions waiting for review |

Prioritize action based on severity:
- **Violations** are the most urgent — rules exist but are being broken
- **Budget warnings** mean you may not be able to add more rules without cleanup
- **Declining rules** may need to be revised or removed
- **Pending suggestions** are informational — review when convenient

## Follow-up Actions

Suggest next steps based on what was found:
- Violations detected? -> "Want me to investigate why these rules are being broken?" -> run `/sio-violations`
- Budget running low? -> "Want to check budget details?" -> run `/sio-budget`
- Declining rules? -> "Want to check rule effectiveness?" -> run `/sio-velocity`
- Pending suggestions? -> "Want to review them?" -> run `/sio-review`
