---
name: sio-codify-workflow
description: One-shot pipeline to codify a recent successful workflow into a reusable skill — runs distill → promote → optimize with confirmation between steps. Use when the user says "codify this", "save this workflow as a skill", "turn what I just did into a skill", "make a skill from this session".
user-invocable: true
---

# SIO Codify Workflow — Session → Skill in One Pipeline

This skill is the "I just did something useful, save it" wrapper. It chains the three SIO codification steps — distill, promote, optimize — with a confirmation pause between each so the user can abort or edit before the next stage runs.

## When to Use

- User finished a useful multi-step workflow and wants it captured
- User says "codify this", "make this a skill", "save what I just did", "turn this into a slash command"
- User wants the full DSPy pipeline (not just a raw promotion)

If the user only wants ONE of the three stages, route to that single skill instead:
- Just clean a session into a playbook → `/sio-distill`
- Already have a flow hash, just promote it → `/sio-promote-flow`
- Already have a skill, just tune the prompt → `/sio-optimize`

## Pipeline Stages

```
[1] /sio-distill      → cleans the session into an ordered playbook
        ↓ confirm
[2] /sio-promote-flow → writes the playbook as ~/.claude/skills/<name>/SKILL.md
        ↓ confirm
[3] /sio-optimize     → DSPy-tunes the skill prompt against gold examples
        ↓
   Done — new skill is live
```

## Execution

### Step 0 — Identify the source session
Ask the user: "Codify which session — the current one, the latest, or a specific one?"
- Current → use the active session JSONL
- Latest → `sio distill --latest`
- Specific → ask for keywords, run `sio recall "<keywords>" --files` first

### Step 1 — Distill
Run `/sio-distill` (or directly `sio distill --latest`).
Show the cleaned playbook. Ask: **"Distilled. Promote this to a skill? (yes / edit / abort)"**
- `yes` → continue
- `edit` → let the user edit the playbook before promotion
- `abort` → stop, leave playbook in `~/.sio/distilled/`

### Step 2 — Promote
Get the flow hash from step 1, then `sio promote-flow <hash>`.
Show the new SKILL.md path. Ask: **"Skill created at <path>. Run DSPy optimization? (yes / skip)"**
- `yes` → continue to step 3
- `skip` → done, skill is usable as-is

### Step 3 — Optimize (optional)
Run `/sio-optimize` against the new skill. Show the before/after prompt diff.
Ask: **"Apply optimized prompt? (yes / no)"**

### Step 4 — Confirm
Tell the user the slash command name and how to invoke it. Suggest follow-ups:
- "Run `/sio-velocity` later to check if it's actually getting used"
- "Run `/sio-discover` to find more skill candidates from the same patterns"

## Cost

| Stage | Cost | Engine |
|---|---|---|
| Distill | Free | Regex + SQLite |
| Promote | Free | Template render |
| Optimize | ~$0.05–0.20 | DSPy + LLM (Gemini/Haiku) |

## Naming Convention

The promoted skill name is auto-derived from the dominant tools in the flow. The user can override at the promote step. Skills land in `~/.claude/skills/<name>/SKILL.md`.

## Failure Modes

- **No flow hash from distill** → session was too short or fragmented; suggest `/sio-flows` to mine across multiple sessions instead
- **Promote fails** → usually a name collision; rename and retry
- **Optimize fails** → likely no gold examples yet; skip optimization, skill is still usable

## Related Skills

- `/sio` — master router (use this if codify isn't actually what you want)
- `/sio-distill` — just distill, no promotion
- `/sio-promote-flow` — promote a known hash, no distill
- `/sio-optimize` — tune a pre-existing skill
- `/sio-discover` — find which workflows in this repo are skill-worthy (run before codify-workflow if you're not sure what to save)
