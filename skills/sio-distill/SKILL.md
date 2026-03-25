---
name: sio-distill
description: Distill a long exploratory session into a clean playbook of winning steps. Removes failures, retries, dead ends. Ask naturally like "distill that session" or "extract the workflow from yesterday".
user-invocable: true
---

# SIO Distill — Extract the Winning Path

## When to Use
- After a long session where you figured out a workflow
- "Distill the latest session"
- "Extract the steps that worked from yesterday"
- "Create a playbook from session X"
- To capture a workflow before you forget the steps

## User Input

Parse natural language into CLI options:

| User says | Options |
|-----------|---------|
| "distill latest" | `--latest` |
| "distill for project X" | `--latest --project X` |
| "distill and save" | `--latest -o <auto-path>` |
| "distill session abc123" | `<path to session.jsonl>` |

## Execution

### Step 1: Identify the session

If user says "latest" or doesn't specify:
```bash
sio distill --latest ${PROJECT:+--project $PROJECT}
```

If user provides a session path:
```bash
sio distill /path/to/session.jsonl
```

### Step 2: Save if requested

Default: print to stdout. If user wants to save:
```bash
sio distill --latest -o <project>/.memory/<descriptive_name>_playbook.md
```

### Step 3: Optional — LLM Polish (Expensive Tier)

If the cheap playbook is too raw, send the output to `gemini_brainstorm` to polish into a clean runbook:

```
gemini_brainstorm(
    topic="Polish this raw session playbook into a clean step-by-step workflow",
    context="<paste the raw playbook output>"
)
```

## What It Does

Takes a session with 100s of tool calls (including dead ends, errors, retries) and:
1. Removes all failed tool calls
2. Removes retries (same tool, similar input)
3. Removes undone work (after "undo" messages)
4. Deduplicates consecutive reads of same file
5. Groups remaining steps into Explore → Implement → Verify phases
6. Outputs a numbered playbook with bash commands preserved

## Output

- **Compression ratio:** Typically 40-60% (half the noise removed)
- **Phases:** Explore (first 30%), Implement (30-80%), Verify (last 20%)
- **Bash commands:** Preserved verbatim for copy-paste reuse

## Follow-up
- Save playbook to project `.memory/` folder for future reference
- Convert to a Claude Code skill if the workflow is reusable
- Feed to `/sio-export --task flow` for DSPy training data
