---
name: sio-flows
description: Discover recurring positive tool sequence patterns from sessions. Shows what workflows work well, not just errors. Ask naturally like "what patterns work?" or "show my productive workflows".
user-invocable: true
requires:
  cli: "sio>=0.3.0"
  skills: [sio-distill, sio-export, sio-scan]
  hooks: []
  optional: []
---

# SIO Flows — Discover Positive Patterns

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio-scan` — mine error+success patterns to compare against flows; `/sio-export` — export flow data as training sets; `/sio-distill` — distill a specific session into a reusable playbook
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)

## When to Use
- "What patterns work well in my sessions?"
- "Show me my productive workflows"
- "What tool sequences am I using most?"
- After `/sio-scan` to see both errors AND successes

## User Input

Parse natural language into CLI options:

| User says | Options |
|-----------|---------|
| "show my flows" | `--since "14 days"` (default) |
| "last week flows" | `--since "7 days"` |
| "frequent patterns only" | `--min-count 10` |
| "flows for project X" | `--project X` |

## Execution

```bash
sio flows --since "${SINCE:-14 days}" ${PROJECT:+--project $PROJECT} --min-count ${MIN_COUNT:-3} --limit ${LIMIT:-20}
```

## Interpreting Results

| Confidence | Meaning |
|-----------|---------|
| **HIGH** | 10+ occurrences, 80%+ success — automate this |
| **MEDIUM** | 5+ occurrences, 60%+ success — emerging pattern |
| **LOW** | Infrequent or low success — not yet proven |

## Follow-up
- High-confidence flows → candidates for new skills
- Want training data → `/sio-export`
- Want to distill a specific session → `/sio-distill`
