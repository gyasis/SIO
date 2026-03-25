---
name: sio-scan
description: Mine and analyze recent Claude Code session errors. Ask naturally like "what's my agent doing wrong?" or "scan my recent sessions for problems".
---

# SIO Scan — What's My Agent Doing Wrong?

Mine recent sessions and show what's going wrong. Auto-detects project, translates
natural language into filters.

## User Input

```text

```

You **MUST** consider the user input before proceeding (if not empty).

## Natural Language → Filter Translation

Parse the user's request into CLI filters (same rules as `/sio-suggest`):

| User says | Filters applied |
|---|---|
| "what's my agent doing wrong?" | `--project <auto>` (broad scan) |
| "scan for placeholder issues" | `--project <auto> --grep placeholder` |
| "what development gaps exist?" | `--project <auto> --grep "placeholder,hardcoded,stub,empty" --exclude-type repeated_attempt` |
| "show me agent admissions" | `--project <auto> --type agent_admission` |
| "what tools keep failing?" | `--project <auto> --type tool_failure` |

## Execution

```bash
#!/bin/bash
set -e

AUTO_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
PROJECT="${SIO_PROJECT:-$AUTO_PROJECT}"
SINCE="${SIO_SINCE:-7 days}"

# Build flags from parsed user intent
GREP_FLAG=""
TYPE_FLAG=""
EXCLUDE_FLAG=""
if [ -n "${SIO_GREP}" ]; then
    GREP_FLAG="--grep ${SIO_GREP}"
fi
if [ -n "${SIO_TYPE}" ]; then
    TYPE_FLAG="--type ${SIO_TYPE}"
fi
if [ -n "${SIO_EXCLUDE_TYPE}" ]; then
    EXCLUDE_FLAG="--exclude-type ${SIO_EXCLUDE_TYPE}"
fi

echo "=== SIO Scan: ${PROJECT} (last ${SINCE}) ==="
echo "  Filters: ${TYPE_FLAG} ${GREP_FLAG} ${EXCLUDE_FLAG}"
echo ""

# Step 1: Mine fresh data
echo "Step 1: Mining sessions..."
sio mine --since "${SINCE}"
echo ""

# Step 2: Show filtered error breakdown
echo "Step 2: Error breakdown:"
sio errors --project "${PROJECT}" ${TYPE_FLAG} ${GREP_FLAG} ${EXCLUDE_FLAG}
echo ""

# Step 3: Show top patterns
echo "Step 3: Top patterns:"
sio patterns --project "${PROJECT}" 2>/dev/null || echo "  (Run '/sio-suggest' to cluster)"
echo ""

echo "Step 4: Pipeline status:"
sio status
```

## How to Interpret Results

| Error Type | Signal Value | What It Means |
|---|---|---|
| **agent_admission** | Highest | Agent knows it failed — best training data |
| **user_correction** | High | You corrected the agent — misunderstood intent |
| **undo** | High | You reverted agent's work — unwanted changes |
| **tool_failure** | Medium | Tool errored — specific tool fixes needed |
| **repeated_attempt** | Low (noise) | Tool retried 3+ times — usually not actionable |

## Follow-up

- Many quality errors → `/sio-suggest` to generate improvement rules
- Want to drill in → `sio errors --project <name> --grep <keyword> --exclude-type repeated_attempt -n 50`
- Ready to improve → `/sio-suggest` then `/sio-review` then `/sio-apply`
