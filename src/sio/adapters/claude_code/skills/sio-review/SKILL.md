---
name: sio-review
description: Interactively review pending improvement suggestions. Ask naturally like "review my suggestions" or "show me what SIO recommends".
---

# SIO Review — Approve or Reject Suggestions

When the user wants to review pending suggestions — show each one with its proposed rule, confidence score, and target file, then let them approve, reject, or defer.

## Triggers (natural language)

- "Review my suggestions"
- "Show me what SIO recommends"
- "What suggestions are pending?"
- "Let me review the improvement rules"
- "Go through the suggestions"

## Execution

```bash
#!/bin/bash
set -e
sio suggest-review
```

## Shortcut Commands

For quick approve/reject without interactive mode:

```bash
# Approve with a note
sio approve 42 --note "prevents repeated Bash retries"

# Reject with a note
sio reject 43 --note "too generic, need more data"
```

## What Each Suggestion Contains

For each suggestion, show the user:
1. **Description**: What error pattern this addresses
2. **Confidence**: How sure SIO is (based on error count, session count, examples)
3. **Target file**: Where the rule would be written (CLAUDE.md, SKILL.md, hooks)
4. **Proposed change**: The actual rule text that would be added

## After Review

- Approved suggestions can be applied with `/sio-apply`
- Rejected suggestions are archived with the user's note
- Deferred suggestions stay in the queue for later review
