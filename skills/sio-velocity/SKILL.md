---
name: sio-velocity
description: Check if applied rules are actually reducing errors. Ask naturally like "are my rules working?" or "check rule effectiveness".
---

# SIO Velocity — Are My Rules Working?

Run this to verify rule effectiveness. Shows whether applied rules are actually reducing the errors they target. Flag rules that are not working so they can be revised or removed.

## Triggers (natural language)

- "Are my rules working?"
- "Check rule effectiveness"
- "Which rules are reducing errors?"
- "Are errors going down?"
- "Show me rule velocity"
- "Which rules should I remove?"

## Execution

Run the SIO velocity check:

```bash
#!/bin/bash
set -e
sio velocity
```

## How to Interpret Results

After running, explain the metrics to the user:

| Metric | What It Means |
|---|---|
| **Improving** | Error count for this pattern is decreasing since the rule was applied |
| **Stable** | Error count is flat — rule may be preventing new errors but not fixing old ones |
| **Declining** | Error count is increasing despite the rule — rule is ineffective |
| **Insufficient data** | Not enough sessions since the rule was applied to measure impact |

Focus on actionable findings:
- **Declining** rules need attention — they may be too vague, incorrectly targeted, or the root cause is different
- **Stable** rules are working as guardrails — they prevent regression
- **Improving** rules are delivering value — leave them in place

## Follow-up Actions

Suggest next steps based on what was found:
- Declining rules? -> "Want me to regenerate suggestions for those patterns?" -> run `/sio-suggest`
- Want to see the underlying errors? -> "Let me scan recent sessions" -> run `/sio-scan`
- Rules taking up too much space? -> "Let me check the budget" -> run `/sio-budget`
