---
name: sio-search
description: Search session history across all coding-agent harnesses (Claude, Codex, Gemini, Goose, OpenCode, Aider, PromptChain) via --agent, or scope any SIO analysis command to a single session. Ask naturally like "find where I fixed that dbt error", "search my sessions for X", "search Codex/PromptChain for Y", "scope to one session", or "/sio-search".
user-invocable: true
requires:
  cli: "sio>=0.3.0"
  skills: [sio-scan, sio-suggest]
  hooks: []
  optional: []
---

# SIO Search — Cross-Harness Session Search & Session Scoping

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio-scan` — run after finding the relevant session to mine it for errors; `/sio-suggest` — generate rules once a session is targeted with `--session`
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)

Search session history across all coding-agent harnesses (Claude, Codex, Goose,
OpenCode, Gemini, Aider, PromptChain), or narrow every SIO analysis command to one
specific session using the `--session` flag. **Use `--agent <harness>` to target a
specific coding agent** — it defaults to `claude`, so searching any other agent's
history requires passing it explicitly (e.g. `--agent codex`, `--agent promptchain`,
`--agent all`).

> **Read Part 0 first.** The pattern is a **regex**, and searching is a **two-hop**
> operation (EXPAND with `a|b`, CONTRACT with `--refine`). One-shot NL-phrase searches
> are the most common failure mode.

## Triggers (natural language)

- "Search my sessions for X"
- "Find where I did Y"
- "Which sessions mention Z?"
- "Scope errors to this session"
- "Scope suggest to one session"
- "/sio-search"

## Part 0 — Search discipline: the EXPAND → CONTRACT ladder (READ FIRST)

**The pattern is a REGEX (ripgrep semantics), NOT a natural-language phrase.** A
multi-word pattern only matches when those words are **adjacent** — which is why
NL-phrase searches almost always return 0.

| ❌ Don't | ✅ Do |
|---|---|
| `sio search "salary Europe pay comparison"` → **0 hits** | `sio search "salary\|stipendio\|pay"` → matches ANY term (**EXPAND**) |
| `for q in "a" "b" "c"; do sio search "$q"; done` — N one-shots | ONE alternation, then `--refine` (**CONTRACT**) |

**The ladder — always hop; never re-fire a fresh broad one-shot:**

1. **Anchor** — one term, or `a|b|c` alternation to EXPAND across synonyms.
2. **Too many hits → CONTRACT with `--refine`** (Hop-2):
   ```bash
   sio search "error" --recent 7 --files                     # Hop-1 (wide)
   sio search "error" --recent 7 --files --refine "timeout"  # Hop-2 (AND-narrow)
   sio search "error" --refine "timeout,socket"              # comma = OR within Hop-2
   ```
   `--refine` applies in `--files` / `--count` / record modes.
   `--strategy filter` is the default; `recluster`/`hybrid` are `sio suggest`-only
   (they raise an error here — session records have no cluster schema).
3. **Zero hits → EXPAND**: widen with `--recent 0`, add alternation, or drop terms.
   On a zero-result multi-word phrase the CLI prints the exact alternation to try.
4. **Walk context with `--around N`** — role-aware ±N turns around each hit. This is
   DISTINCT from `--context N` (raw rg lines) and `--session <uuid>` (full transcript).
5. `--recent` is recency-first discipline (default 7d). `--files` to locate, then
   `--session <handle>` to read.

**When Hop-1 is noisy** the CLI emits a Hop-2 suggestion on stderr (`--noise-threshold N`,
default 20). Take it — don't ignore it and re-broaden.

### It is measured — `sio search-discipline`

Reports recency / multi-hop / files-first / context-walk rates against targets:

| rate | counts | target |
|---|---|---|
| recency-first | `--recent` | ≥85% |
| **multi-hop** | **`--refine` / `--strategy`** | ≥5% |
| files-first | `--files` | (observability) |
| context-walk | **`--context` / `--around`** | ≥15% |

`--within` / `--use-cache` are **`sio suggest`-only** — they are NOT `sio search` flags.
A 0% multi-hop rate means you are one-shotting; hop instead.

### Immediate feedback is captured

If the user corrects a search ("you must narrow that", "too broad", "refine it"), the
`UserPromptSubmit` hook labels **that search's** invocation row (`user_satisfied=0`,
`correct_action=0`, `labeled_by=search_feedback_hook`). Treat such a correction as a
direct instruction to re-run with `--refine` or a tighter alternation — never to
re-broaden or to shotgun more one-shots.

## Part 1 — Cross-Harness Search (`sio search`)

`sio search` is a thin passthrough to the absorbed `session-search` engine. All flags
flow through unchanged.

### Basic usage

```bash
# Search the current (Claude) harness only
sio search "pattern"

# Search across ALL harnesses at once
sio search "pattern" --agent all

# Search a specific harness (--agent picks which coding agent; default=claude)
sio search "pattern" --agent goose
sio search "pattern" --agent codex
sio search "pattern" --agent promptchain   # the PromptChain TUI/CLI agent

# Narrow to the last N days
sio search "pattern" --agent all --recent 7

# Emit matching file paths (no content) — useful for piping
sio search "pattern" --agent claude --files

# Count matches without showing them
sio search "pattern" --agent all --count

# EXPAND: regex alternation matches ANY of the terms
sio search "dbt|snowflake|databricks" --recent 14 --files

# CONTRACT: Hop-2 narrowing (works with --files/--count/records)
sio search "error" --recent 7 --files --refine "timeout"
sio search "error" --refine "timeout,socket"        # comma = OR within Hop-2

# WALK: role-aware ±N turns around each hit (NOT raw rg lines)
sio search "FileNotFoundError" --around 3 --recent 0

# List which harnesses have on-disk history
sio search --list-agents

# Am I searching well? (recency / multi-hop / files / context-walk rates)
sio search-discipline

# Full flag set
sio search --help
```

### Agent values

| `--agent` value | Harness |
|---|---|
| `claude` | Claude Code (default) |
| `codex` | OpenAI Codex |
| `goose` | Goose |
| `opencode` | OpenCode |
| `gemini` | Gemini CLI |
| `aider` | Aider |
| `promptchain` | PromptChain TUI/CLI agent |
| `all` | All seven harnesses |

## Part 2 — Session Handle Syntax

A **session handle** identifies one session across any SIO command that accepts
`--session`. Three canonical forms are accepted:

| Form | Example | Notes |
|---|---|---|
| `agent:native_id` | `claude:c6428f4f-...` | Fully explicit; preferred |
| File path | `/path/to/session.jsonl` | A search-result path from `--files` |
| Bare id | `c6428f4f` | Assumed `claude:`; partial ids resolve fuzzy (lists candidates if ambiguous) |
| `-` | `-` | Read from stdin — use in pipes |

## Part 3 — Scoping Analysis to One Session (`--session`)

Every major SIO analysis command accepts `--session <handle>` to target a single
session instead of the full database:

```bash
# Show errors from one session only
sio errors --session claude:<uuid>

# Mine one session on demand (--since becomes optional)
sio mine --session claude:<uuid>

# Generate rules from a single session
sio suggest --session claude:<uuid>
```

## Part 4 — Pipe Pattern (`--files | --session -`)

The cleanest workflow: search for sessions matching a keyword, then pipe the path
directly into a scoped command via `-` (stdin handle):

```bash
# Find the session, scope errors to it — no copy-paste needed
sio search "some error keyword" --files | sio errors --session -

# Find a session, then generate rules from it
sio search "some error keyword" --files | sio suggest --session -

# Find a session, then mine it immediately
sio search "some error keyword" --files | sio mine --session -
```

When `--files` returns multiple matches, pipe the first result:

```bash
sio search "keyword" --agent claude --files | head -1 | sio errors --session -
```

## Interpreting Search Results

- Results show matching lines from JSONL transcripts (or SpecStory `.md` files when
  `--specstory` is passed).
- `--files` output is one file path per line — ideal for scripting and piping.
- `--count` outputs a single integer — use in conditionals.
- **Too many results?** That's the CONTRACT signal — re-run with `--refine`, don't scan
  the noise or fire another broad search.
- **Zero results?** That's the EXPAND signal — widen `--recent`, add `|` alternation, or
  drop terms. Check stderr: the CLI suggests the alternation for you.

## Follow-up

- Found the session → `/sio-scan` (mine it) or `sio errors --session <handle>`
- Want improvement rules from it → `sio suggest --session <handle>` then `/sio-review`
- Want to watch it live → `/sio-watch`
- **Am I searching well?** → `sio search-discipline` (recency / multi-hop / context-walk
  rates vs targets). A 0% multi-hop rate means you never hopped — go back to Part 0.
