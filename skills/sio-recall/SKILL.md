---
name: sio-recall
description: Recall how a task was solved in a previous session. Topic-filters distilled sessions, detects struggle→fix transitions, polishes via Gemini. Ask "how did we do X?" or "recall the dbt setup workflow".
user-invocable: true
---

# SIO Recall — How Did We Do That?

## When to Use
- "How did we run dbt locally?"
- "Recall the auth fix from last week"
- "What was the snowflake deploy workflow?"
- "How did we set up the Cube connection?"
- Any time the user references a previous session's workflow

## The Pipeline

```
User question → Find session → Distill → Topic-filter → Struggle detection → Gemini polish → Clean runbook
```

## Execution

### Step 1: Run the CLI recall (cheap tier)
```bash
sio recall "USER_QUERY" --project PROJECT_NAME
```

This internally:
1. Finds the most recent matching JSONL session
2. Distills it (removes failures, retries)
3. Topic-filters to only steps matching the query
4. Detects struggle→fix transitions
5. Outputs a structured recall

### Step 2: Read the output and assess quality

If the output is clean enough (10-30 steps, clear bash commands), present it directly.

If the output is still too raw or noisy (50+ steps), proceed to Step 3.

### Step 3: Gemini Polish (expensive tier)

Take the recall output and send to Gemini for polishing:

```python
gemini_brainstorm(
    topic=f"Create a clean 10-15 step runbook for: {USER_QUERY}",
    context=f"""Below are raw steps from a session. Rules:
1. Identify the Fix: Format as 'Problem: X → Fix: Y'
2. Prune noise: Skip ls, cat, git status
3. Consolidate: Show only the final successful command
4. Environment first: Setup/exports in first 3 steps
5. Include exact bash commands

RAW STEPS:
{PASTE_RECALL_OUTPUT}"""
)
```

### Step 4: Present the polished runbook to the user

Format as:
```markdown
## Recalled Workflow: {query}
*Source: Session from {date}, {N} steps distilled to {M}*

### Key Fixes
- Problem: X → Fix: Y

### Steps
1. ...
2. ...
```

### Step 5: Offer to save

Ask the user: "Save this runbook to `.memory/` for future reference?"

## CLI Options

```bash
sio recall "query"                          # Cheap: topic-filtered distill
sio recall "query" --polish                 # Show Gemini prompt for polish
sio recall "query" --project hh-dev         # Filter by project
sio recall "query" -o runbook.md            # Save to file
sio recall "query" --session /path/to.jsonl # Specific session
```

## How Topic Filtering Works

The query is expanded into a keyword cluster:
- "dbt" → also searches for: profiles.yml, dbt_project, models/, target/
- "hhdev" → also searches for: hh-dev, start.sh, stop.sh, local-dev
- "cube" → also searches for: cubejs, schema/, .yml
- "snowflake" → also searches for: snowsql, TWICE, H_EXP

Steps are included if their summary, tool_input, or output matches ANY keyword.
Context window: 1 step before and 1 after each match is also included.

## Struggle Detection

Finds patterns where:
1. A tool call FAILED (error in output)
2. Within 3 steps, the SAME tool or file succeeds

These are formatted as "Problem → Fix" in the output — the most valuable part of the recall.

## Anti-Patterns
- Don't use for simple fact lookup → use `/memory-search` instead
- Don't use for errors only → use `/sio-scan` instead
- If query is too broad ("everything"), narrow it or specify a project
