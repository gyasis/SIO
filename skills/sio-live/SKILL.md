---
name: sio-live
description: Discover and read IN-PROGRESS (live) coding-agent sessions — not the indexed past ones. See every session running right now, each one's repo/branch/worktree, and get warned when two live sessions share a working tree (a concurrent-edit collision). Then read or attach to a specific one by id. Ask naturally like "what sessions are running right now", "any concurrent sessions on this repo", "are we about to collide", "show me what the cadastre session is doing", "attach to session X", or "/sio-live".
user-invocable: true
requires:
  cli: "sio>=0.5.0"
  skills: [sio-search, sio-watch]
  hooks: []
  optional: []
---

# SIO Live — Discover & Read In-Progress Sessions

## Dependencies
- **CLI:** `sio >= 0.5.0`
- **Skills:** `/sio-search` (find/inspect INDEXED history), `/sio-watch` (tail a single session by handle). `sio live` complements both — it covers the one thing they can't: sessions that are *still running right now*.
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`).

## Why this exists (the gap it closes)

`sio search` only sees **finished, indexed** transcripts — a
session that is *still being written* is invisible to it. `sio live` finds
sessions by a **real-time signal** instead (recent transcript mtime) and reads
the fields SIO's indexers never touched: Claude Code writes `cwd`, `gitBranch`,
and `sessionId` on **every JSONL line**. From those it resolves each live
session's repository, branch, and working tree — and flags **collisions**: two
live sessions editing the same working tree on the same branch, which is a
concurrent-modification hazard (the classic "two agents on one checkout will
stomp each other" problem).

Use it when you (or another agent) need to know: *what else is running right now,
and am I about to collide with it?*

## Triggers (natural language)

- "What sessions are running right now?" / "list live / active sessions"
- "Any concurrent sessions on this repo / branch?"
- "Are we about to collide with another session?"
- "Is another agent working in this checkout?"
- "Show me / catch me up on what the `<repo>` session is doing"
- "Attach to session `<id>`" / "let me watch the `<repo>` session"
- "/sio-live"

## Commands

### `sio live ls` — enumerate active sessions + flag collisions

```bash
sio live ls                 # sessions active in the last 60 min (default)
sio live ls -m 15           # tighten the live window to 15 minutes
sio live ls --repo ~/Documents/code/cadastre   # only sessions under a path
sio live ls --as-json       # machine-readable (pollable by an agent)
```

Each row: `agent · id · repo · branch · cwd · age · msgs · last-event`.
Row markers:

| Marker | Meaning |
|---|---|
| `⚠` | **Collision** — this session shares a live working tree with another session |
| `◆` | This session is in the **same working tree** as the one you invoked `sio live` from |
| `← you` | This is your own session (only when a session-id env var is set — see below) |

After the table, a **Collisions** section groups the offending sessions by
working tree + branch, e.g.:

```
⚠ Collisions — same working tree, live:
  ~/Documents/code/cadastre  branch=feat/single-source-recurrence  →  aee069b, a2e6a6e
  Two sessions in one checkout on the same branch WILL collide — split to a worktree or coordinate.
```

### `sio live show <id>` — snapshot the TAIL of one session

The "catch me up on this session" read. Resolves the id (bare, partial, canonical
`claude:<uuid>`, or a file path) and prints its recent events — enriched so tool
calls, tool results, and user turns are legible (not just assistant prose).

```bash
sio live show aee069b            # last 40 events (default), one-shot
sio live show aee069b -n 15      # last 15 events
sio live show aee069b --tools-only
sio live show aee069b --follow   # snapshot, then keep streaming (like sio watch)
```

### `sio live attach <id>` — attach from another session and follow (read-only)

The companion to `ls`: once you've spotted a peer session, **attach** to keep
watching it as it works — a short context tail, then a live stream. It is a pure
**read-only observer** — it never writes to the attached session. Ctrl-C detaches.

```bash
sio live attach aee069b          # 15 lines of context, then follow live
sio live attach aee069b -c 40    # 40 lines of prior context first
sio live attach aee069b --tools-only
```

## Options

| Command | Flag | Default | Description |
|---|---|---|---|
| `ls` | `-m, --minutes` | 60 | A session is "live" if its transcript changed within N minutes |
| `ls` | `--repo PATH` | — | Filter to sessions whose working tree is under PATH |
| `ls` | `--as-json` | off | Machine-readable output (agent + native_id + cwd + branch + toplevel + collision + …) |
| `show` | `-n, --tail` | 40 | Number of trailing events to print |
| `show` | `-f, --follow` | off | After the snapshot, keep streaming new events |
| `show` | `--tools-only` | off | Only surface `tool_use` events |
| `attach` | `-c, --context` | 15 | Lines of prior context before following |
| `attach` | `--tools-only` | off | Only surface `tool_use` events |

## Session id forms (for `show` / `attach`)

| Form | Example |
|---|---|
| Bare partial id (from `ls`) | `aee069b` |
| Canonical handle | `claude:aee069be-bad9-47fd-a277-bc351073a620` |
| File path | `/home/u/.claude/projects/-x/aee069be-....jsonl` |

Partial ids are resolved fuzzily for Claude sessions; an ambiguous prefix errors
and asks for more characters.

## Agent coverage

**Claude Code** is covered richly (its JSONL carries `cwd` + `gitBranch` on every
line, so repo/branch/worktree/collision resolution is exact). Other harnesses
(`goose`, `codex`, `gemini`, `aider`) are **listed best-effort** when active —
shown with whatever working-dir signal is trivially available, and without git
collision resolution. Live **follow** (`show --follow` / `attach`) currently
supports the Claude harness.

## Notes & gotchas

- **Sub-agent transcripts** (`<session-id>/subagents/agent-*.jsonl`) carry the
  *parent* session id — they are the same session, not a concurrent one. `sio
  live` skips them and dedups by session id, so one logical session is one row.
- **Branch is the live working-tree HEAD** (`git`), not the possibly-stale value
  in the transcript — because all sessions in one working tree share its branch,
  and that is what actually determines a collision.
- **`← you` self-marking** reads an optional session-id env var (`SIO_SESSION_ID`
  / `CLAUDE_SESSION_ID`); without it, your own tree is still marked `◆`.

## Follow-up

- Mine a discovered session for errors → `sio mine --session <handle>`
- Scope error browsing to it → `sio errors --session <handle>`
- Generate improvement rules from it → `sio suggest --session <handle>`
- Search its (once-indexed) history → `/sio-search`
- Single-session live tail by handle → `/sio-watch`
