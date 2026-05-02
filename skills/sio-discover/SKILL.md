---
name: sio-discover
description: Find repo-specific skill candidates from mined patterns. Ask naturally like "what can SIO improve in this repo?" or "find improvement opportunities".
---

# SIO Discover — Find Improvement Opportunities

Run this when starting work on a repo to find improvement opportunities. Analyzes mined patterns to identify repo-specific skill candidates — recurring workflows that could become reusable skills.

## Triggers (natural language)

- "What can SIO improve in this repo?"
- "Find improvement opportunities"
- "Discover skill candidates"
- "What patterns could become skills?"
- "Analyze this repo for automation opportunities"
- "What workflows should be automated?"

## Execution

Run the SIO discovery command:

```bash
#!/bin/bash
set -e
sio discover
```

## How to Interpret Results

After running, explain the candidates to the user:

| Candidate Type | What It Means |
|---|---|
| **Workflow skill** | A multi-step sequence that happens often enough to codify as a skill |
| **Guard rule** | A recurring error pattern that should become a preventive rule |
| **Tool pattern** | A specific tool usage pattern that works well and should be documented |

Evaluate each candidate:
- **High frequency + low complexity** = easy win, implement first
- **High frequency + high complexity** = high value but needs careful design
- **Low frequency + high impact** = worth automating if the error cost is high

## Follow-up Actions

Suggest next steps based on what was found:
- Workflow skills found? -> "Want to see the full flow details?" -> run `/sio-flows`
- Guard rules identified? -> "Want to generate the rule text?" -> run `/sio-suggest`
- Want to promote a flow to a skill? -> "Let me show available flows" -> run `/sio-promote-flow`
