---
name: sio-promote-flow
description: Promote a successful workflow to a reusable skill file. Ask naturally like "promote that flow" or "turn that workflow into a skill".
---

# SIO Promote Flow — Turn Workflows into Skills

Run `sio flows` first to see available flows, then promote the selected one to a reusable skill file. This is a two-step process.

## Triggers (natural language)

- "Promote that flow"
- "Turn that workflow into a skill"
- "Make that flow reusable"
- "Save that workflow as a skill"
- "Promote flow to skill"
- "Create a skill from that pattern"

## Execution

Step 1: List available flows to find the right one.

```bash
#!/bin/bash
set -e
echo "Available flows:"
sio flows
```

Step 2: After the user selects a flow, promote it by hash.

```bash
#!/bin/bash
set -e
# Replace <hash> with the flow hash from step 1
sio promote-flow <hash>
```

## How to Use This Skill

1. Run step 1 to display all discovered flows with their hashes
2. Present the flows to the user with their descriptions and frequency counts
3. Ask the user which flow they want to promote (by hash or description)
4. Run step 2 with the selected hash
5. Show the user the generated skill file path

## What Gets Created

When a flow is promoted:
- A new SKILL.md file is generated in the skills directory
- The skill captures the tool sequence, triggers, and execution steps
- The skill can then be invoked as a slash command in future sessions

## Follow-up Actions

Suggest next steps after promotion:
- "Want to see all your flows?" -> run `/sio-flows`
- "Want to discover more candidates?" -> run `/sio-discover`
- "Want to check if existing skills are effective?" -> run `/sio-velocity`
