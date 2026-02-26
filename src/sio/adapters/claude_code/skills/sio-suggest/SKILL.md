---
name: sio-suggest
description: Generate targeted CLAUDE.md rules from mined error patterns. Ask naturally like "how can I improve my agent?" or "generate suggestions from my errors".
---

# SIO Suggest — Turn Errors into Improvement Rules

When the user asks for improvement suggestions, rules, or wants to make their agent better — run the full SIO pipeline (cluster → dataset → suggest).

## Triggers (natural language)

- "How can I improve my agent?"
- "Generate suggestions from my errors"
- "Turn those errors into CLAUDE.md rules"
- "What rules should I add?"
- "Suggest improvements based on what's been failing"
- "Make my agent better"
- "What should I fix based on the error patterns?"

## Execution

Run the full suggestion pipeline:

```bash
#!/bin/bash
set -e

# Check if errors exist
ERROR_COUNT=$(sio status 2>/dev/null | grep "Errors mined" | awk '{print $NF}')

if [ -z "$ERROR_COUNT" ] || [ "$ERROR_COUNT" = "0" ]; then
    echo "No errors mined yet. Mining last 7 days first..."
    sio mine --since "7 days"
    echo ""
fi

# Accept optional type filter and grep term from environment
TYPE_FLAG=""
GREP_FLAG=""
MIN_EXAMPLES="${SIO_MIN_EXAMPLES:-3}"

if [ -n "${SIO_TYPE}" ]; then
    TYPE_FLAG="--type ${SIO_TYPE}"
fi

if [ -n "${SIO_GREP}" ]; then
    GREP_FLAG="--grep ${SIO_GREP}"
fi

echo "Running full suggestion pipeline..."
sio suggest ${TYPE_FLAG} ${GREP_FLAG} --min-examples ${MIN_EXAMPLES}
```

## After Generation

Present each suggestion to the user with:
1. The pattern it addresses (error count, session count)
2. The proposed CLAUDE.md rule (the actual text)
3. Confidence score

Then ask: "Want to review and approve these? Run `/sio-review`"

## Type-Specific Suggestions

If the user asks about a specific category:
- "What tools keep failing?" → `SIO_TYPE=tool_failure`
- "What do I keep correcting?" → `SIO_TYPE=user_correction`
- "When does the agent admit mistakes?" → `SIO_TYPE=agent_admission`
- "What gets retried too many times?" → `SIO_TYPE=repeated_attempt`
- "What changes do I keep undoing?" → `SIO_TYPE=undo`

## Cross-Project Search

If the user asks about errors across projects or with a specific technology:
- "Find all Databricks errors" → `SIO_GREP=databricks`
- "What Snowflake queries keep failing?" → `SIO_GREP=snowflake`
- "Show MCP tool failures" → `SIO_GREP=mcp__`
