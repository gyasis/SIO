---
name: sio-violations
description: Detect when rules in CLAUDE.md are being violated. Ask naturally like "which rules are being ignored?" or "check for rule violations".
requires:
  cli: "sio>=0.3.0"
  skills: [sio-budget, sio-scan, sio-suggest]
  hooks: []
  optional: []
---

# SIO Violations — Find Ignored Rules

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio-budget` — audit stale rule budget after removing ineffective rules; `/sio-scan` — scan sessions where violations occurred for deeper context; `/sio-suggest` — regenerate rules with improved wording when existing rules are frequently violated
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)

Run this to find rules that are being ignored. Compares rules in CLAUDE.md against recent session behavior to detect violations.

## Triggers (natural language)

- "Which rules are being ignored?"
- "Check for rule violations"
- "Are any CLAUDE.md rules being broken?"
- "Show me violated rules"
- "What rules aren't being followed?"
- "Find rule violations"

## Execution

Run the SIO violations detector:

```bash
#!/bin/bash
set -e
sio violations
```

## How to Interpret Results

After running, explain the violation types to the user:

| Violation Type | What It Means |
|---|---|
| **Direct violation** | A rule explicitly says "do X" or "never do Y" and the agent did the opposite |
| **Partial compliance** | The agent follows the rule sometimes but not consistently |
| **Stale rule** | The rule references tools, paths, or patterns that no longer exist |

Prioritize action based on impact:
- **Direct violations** with high frequency are the most important — the rule may need to be more prominent or rephrased
- **Partial compliance** suggests the rule is ambiguous — consider making it more specific
- **Stale rules** should be removed to free up budget space

## Follow-up Actions

Suggest next steps based on what was found:
- High-frequency violations? -> "Want me to check if the rule wording needs improvement?" -> run `/sio-suggest`
- Stale rules wasting budget? -> "Want to check budget usage?" -> run `/sio-budget`
- Want to see the sessions where violations occurred? -> "Let me scan those sessions" -> run `/sio-scan`
