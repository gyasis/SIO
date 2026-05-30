---
name: sio-search
description: Search session history across all six coding-agent harnesses, or scope any SIO analysis command to a single session. Ask naturally like "find where I fixed that dbt error", "search my sessions for X", "scope to one session", or "/sio-search".
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

Search session history across all six coding-agent harnesses (Claude, Codex, Goose,
OpenCode, Gemini, Aider), or narrow every SIO analysis command to one specific session
using the `--session` flag.

## Triggers (natural language)

- "Search my sessions for X"
- "Find where I did Y"
- "Which sessions mention Z?"
- "Scope errors to this session"
- "Scope suggest to one session"
- "/sio-search"

## Part 1 — Cross-Harness Search (`sio search`)

`sio search` is a thin passthrough to the absorbed `session-search` engine. All flags
flow through unchanged.

### Basic usage

```bash
# Search the current (Claude) harness only
sio search "pattern"

# Search across all six harnesses at once
sio search "pattern" --agent all

# Search a specific harness
sio search "pattern" --agent goose
sio search "pattern" --agent codex

# Narrow to the last N days
sio search "pattern" --agent all --recent 7

# Emit matching file paths (no content) — useful for piping
sio search "pattern" --agent claude --files

# Count matches without showing them
sio search "pattern" --agent all --count

# List which harnesses have on-disk history
sio search --list-agents

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
| `all` | All six harnesses |

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

## Follow-up

- Found the session → `/sio-scan` (mine it) or `sio errors --session <handle>`
- Want improvement rules from it → `sio suggest --session <handle>` then `/sio-review`
- Want to watch it live → `/sio-watch`
