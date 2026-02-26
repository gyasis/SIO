---
name: sio-scan
description: Mine and analyze recent Claude Code session errors. Ask naturally like "what's my agent doing wrong?" or "scan my recent sessions for problems".
---

# SIO Scan — What's My Agent Doing Wrong?

When the user asks about agent mistakes, errors, patterns, or problems — mine their recent sessions and show what's going wrong.

## Triggers (natural language)

- "What's my agent doing wrong?"
- "Scan my recent sessions"
- "What errors is Claude making?"
- "Show me agent mistakes from the last week"
- "What keeps failing?"
- "Any patterns in my agent's errors?"

## Execution

Run the SIO mining and error analysis pipeline:

```bash
#!/bin/bash
set -e

# Default to last 7 days if user doesn't specify a time range
SINCE="${SIO_SINCE:-7 days}"

echo "Mining sessions from the last ${SINCE}..."
sio mine --since "${SINCE}"

echo ""
echo "Error breakdown:"
sio errors

echo ""
echo "Top patterns:"
sio patterns 2>/dev/null || echo "  (Run 'sio suggest' to cluster into patterns)"
```

## How to Interpret Results

After running, explain the error types to the user:

| Error Type | What It Means |
|---|---|
| **repeated_attempt** | Agent retried the same tool 3+ times (spinning wheels) |
| **tool_failure** | Tool call actually errored (permission denied, file not found, etc.) |
| **user_correction** | User said "no, that's wrong" or "I meant..." |
| **agent_admission** | Agent said "I missed", "I should have", "my apologies" |
| **undo** | User asked to revert/undo a change |

Highlight the most actionable findings:
- High **repeated_attempt** count → agent is spinning, needs better error handling rules
- High **tool_failure** → specific tools keep failing, suggest running `sio suggest --type tool_failure`
- **agent_admission** → most valuable — the agent knows what went wrong

## Follow-up Actions

Suggest next steps based on what was found:
- "Want me to generate targeted improvement suggestions?" → run `/sio-suggest`
- "Want to drill into a specific error type?" → run `sio errors --type <type> -n 20`
- "Want to search for a specific tool or keyword?" → run `sio errors --grep <keyword>`
