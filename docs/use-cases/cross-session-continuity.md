# Use Case: Cross-Session Continuity — Resuming After /compact, /clear, or a New Shell

**Scenario class:** Your context just got truncated mid-task. Or it's the next morning and yesterday's plan lives only in your head and the JSONL trail. Or a multi-day task is being picked up in a fresh session — maybe by you, maybe by a colleague continuing your work. Without SIO, you re-explain everything from scratch and probably re-do work that was already shipped. With SIO, the agent reconstructs context from the cascade of project-local breadcrumbs and the JSONL ground truth.

This is the loop SIO is built for on the *resume* side: **the past doesn't get re-narrated, it gets re-read.**

---

## The story

You're three hours into a multi-step refactor. Six files touched, one PR drafted, two subagents spawned, a couple of architectural pivots logged. Context hits 90%. `/compact` fires. The transcript collapses into a summary, and the next turn lands in a session that knows roughly what you were doing but has lost the *specifics* — which file you stopped mid-edit on, what the last failing test was, what the user said "no, the other one" to.

Or: it's 8 AM the next day. You open a new terminal in the same project. You type "let's keep going" — and the agent has zero memory of what "keep going" means. It will *guess*, and the guess will be wrong.

Or: you're sharing a project with a colleague. They open a session in your repo to continue a `<task description>` you started Tuesday. They have no transcript, no PRD in their head, no idea which subagent decisions were already made.

All three scenarios collapse to the same problem: **the plan persists, but the *anchor* to the plan has been lost.** SIO + the cascade memory protocol re-anchor it.

---

## Phase 1 — Re-anchor (do this before anything else)

The order matters. Do these three reads *before* you read any source file, before you run any tool, before you respond to the user. Each step narrows the surface; doing them out of order attaches you to the wrong context.

### Step 1a — PRD attach check (BLOCKING)

```bash
cat <cwd>/.memory/active-prds.json | jq '.active[]'
```

If an entry exists whose `branch_at_creation` matches the current git branch AND `session_origin` matches this session — re-read that PRD file in full *before doing anything else*. The PRD is the persisted plan; everything else is tactical.

If multiple candidates exist and none unambiguously matches this session (by session ID, then branch+cwd, then mtime <4h), **do NOT auto-pick**. List candidates to the user and ask. A wrongly-attached PRD poisons the recovered context worse than no PRD at all.

If no `.memory/.prd-owner` sentinel exists at any walk-up directory, skip this step — there's no legitimate PRD scope here.

### Step 1b — Session state

```bash
cat <cwd>/.memory/session.json | jq '.'
```

This is the save-game. Current tasks, recent discoveries, open questions, the last decision logged. Survives `/compact`. Read it second so the PRD frames what you find here.

### Step 1c — Pre-compact discoveries (only if /compact just fired)

```bash
cat <cwd>/.memory/.pre-compact-discoveries.json | jq '.'
```

The pre-compact hook extracts errors→fixes, "important" markers, decisions, and tool patterns *immediately before* the transcript collapses. If you just got compacted, this is the highest-density tactical context you have. If you're resuming the next morning, this file is stale — skip it.

---

## Phase 2 — Recover detail with session-search

The breadcrumbs above tell you *what* was happening. They don't always tell you *what was said* or *which exact path failed*. For that, mine the JSONL.

```bash
session-search "<last-known keywords>" --recent 1 --context 3
```

`--recent 1` means today's sessions. `--context 3` gives three lines of surrounding context per hit — enough to reconstruct a turn. Pull the top 1-2 results and read them. Free, ~200ms, ground truth.

If `--recent 1` returns nothing, widen to `--recent 7` and **flag the age explicitly** to the user: "The most recent session on `<topic>` is N days old. Is that what you mean, or is there newer work?" Never auto-resume work >7 days old without explicit confirmation. See `~/.claude/rules/domains/memory-search.md` for the full recency-first gate.

### When the user says "we did this before"

Different signal. Different cascade.

```
/done-before
```

This is BLOCKING. Runs Graphiti + `/sio-recall` + `session-search` in parallel, synthesizes one answer, and on success auto-codifies the winning workflow back to Graphiti. Use it whenever the user references prior successful work — "last time", "remember when we", "done before". Do NOT just run `session-search` and call it good; `/done-before` is the supervised version that prevents re-discovery loops.

### When you want the codified version, not the raw trail

```
/sio-recall "<task description>"
```

If a past session was distilled into a reusable playbook, this returns the polished version — the tool order, the gotchas, the pitfalls. Faster than reading the JSONL if the playbook exists. If it doesn't, fall back to `session-search`.

### To see what session memory thinks is active right now

```
/session-review
```

Shows discoveries, open tasks, session status. Use it after `/compact` to confirm what survived, or at the start of any resume to see what the agent already considers "in scope".

---

## Phase 3 — Confirm before resuming

Do not charge ahead. The most expensive failure mode of cross-session resume is *guessing wrong about scope* and proceeding silently.

Once Phases 1 and 2 have given you a picture, restate it back to the user in 2-3 sentences:

> "I see we were working on `<X>` in `<file>`, with the last decision being `<Y>` (logged at `<time>`). The next step from the PRD is `<Z>`. Resume from there?"

Then **wait for YES**. Acceptable responses: confirmation, correction, or scope change. If correction — drop the previous candidate completely and re-pivot using the new context terms the user supplied. Never argue for the original match; the user's correction is ground truth.

This is the gate that turns recovered context into *resumable* context.

---

## Putting it together — a concrete checklist

### On any session start where prior work is in scope

```bash
# Phase 1 — Re-anchor (in order, do not skip steps)
cat <cwd>/.memory/active-prds.json | jq '.active[]'        # 1a — PRD attach
cat <cwd>/.memory/session.json | jq '.'                     # 1b — session state
cat <cwd>/.memory/.pre-compact-discoveries.json | jq '.'    # 1c — only if /compact just fired
```

### On the first turn that needs tactical detail

```bash
# Phase 2 — Recover detail
session-search "<last-known keywords>" --recent 1 --context 3
```

Or, if the user invoked prior work:

```
/done-before                            # BLOCKING when user says "we did this"
/sio-recall "<task description>"        # codified playbook lookup
/session-review                         # current memory state
```

### Before any tool call resumes the work

> Restate findings → ask → wait for YES.

---

## Why this loop matters

Compactions and clears *used to be* silent context loss. The transcript collapsed, the agent guessed, the user spent the next ten minutes re-explaining what got lost. Multi-day handoffs were worse — there was no transcript at all, just a vague "let's keep going."

The cascade memory protocol turns that into a recoverable read. `.memory/active-prds.json` anchors the plan. `.memory/session.json` and `.pre-compact-discoveries.json` anchor the tactical state. `session-search`, `/done-before`, and `/sio-recall` reach into the JSONL when those breadcrumbs aren't enough. Each layer is cheap (file read or 200ms grep), so doing all of them is faster than re-explaining once.

The win isn't just resuming faster. It's *resuming correctly* — without re-doing work that already shipped, without re-asking decisions that were already made, without re-attaching to the wrong PRD because two are open in the project.

---

## What this use case is *not*

- Not a substitute for the user being explicit about scope when starting fresh. If you're genuinely starting a new task with no prior context, say so — don't make SIO go hunting for a parent that doesn't exist.
- Not a memory system that survives full repo wipes. `.memory/` is project-local; if the project is deleted or moved without the directory, the breadcrumbs go with it. The JSONL trail in `~/.claude/projects/` persists separately, but PRD/session.json continuity does not.
- Not real-time across simultaneous sessions. Two sessions running in the same project at the same time can write conflicting state to `.memory/` — this is a known multi-session gap. Resume sequentially, not in parallel.
- Not a replacement for committing your work. PRDs and session state are *ephemeral persistence*; only git is durable persistence. If the work is real, commit it before the next compaction.

---

## Cross-references

- `~/.claude/rules/domains/compaction.md` — pre-compact hook behavior, post-compaction recovery order, PRD attach BLOCKING rule
- `~/.claude/rules/domains/memory-search.md` — full cascade memory protocol, recency-first gate, correction re-pivot rule
- `~/.claude/rules/domains/plan-persistence.md` — when temp PRDs are auto-created, sentinel requirement, registry schema
- `docs/use-cases/validating-a-config-change.md` — sibling doc; the *forward-looking* SIO loop (this doc is the *backward-looking* one)
- `~/.claude/skills/done-before.md` — the BLOCKING cascade when user references prior work
- `~/.claude/skills/sio-recall.md` — codified playbook lookup
- `~/.claude/skills/session-review.md` — current session memory state
