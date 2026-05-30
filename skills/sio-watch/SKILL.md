---
name: sio-watch
description: Live-tail a coding-agent session's events in real time — see tool calls and responses as they happen. Ask naturally like "watch this session live", "monitor the current session", or "/sio-watch".
user-invocable: true
requires:
  cli: "sio>=0.3.0"
  skills: [sio-search]
  hooks: []
  optional: []
---

# SIO Watch — Live Session Tail

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** `/sio-search` — use `sio search ... --files` to find the session handle to pass here
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)

Stream a session's events in real time. Prints each event as it arrives until you
press Ctrl-C. Supports `--from-start` to replay existing events before following new
ones, and `--tools-only` to suppress assistant/user messages.

## Triggers (natural language)

- "Watch this session live"
- "Monitor the current session"
- "Tail the session events"
- "Show me tool calls as they happen"
- "/sio-watch"

## Usage

```bash
# Watch a session by canonical handle
sio watch --session claude:<uuid>

# Watch using a bare (partial) id — resolved fuzzy for claude sessions
sio watch --session <partial-id>

# Replay all existing events first, then follow new ones
sio watch --session claude:<uuid> --from-start

# Show only tool_use events (suppress assistant/user turns)
sio watch --session claude:<uuid> --tools-only

# Combine: replay from start, tools only
sio watch --session claude:<uuid> --from-start --tools-only

# Pipe a session path from sio search
sio search "keyword" --files | head -1 | xargs -I{} sio watch --session {}

# Read handle from stdin
echo "claude:<uuid>" | sio watch --session -
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--session <handle>` | required | Session to watch (`agent:native_id`, a path, a bare id, or `-` for stdin) |
| `--from-start` | off | Replay existing events before following live ones |
| `--tools-only` | off | Filter output to `tool_use` events only |

## Session Handle Forms

See `/sio-search` for the full handle syntax. Short reference:

| Form | Example |
|---|---|
| Canonical | `claude:<uuid>` |
| File path | `/path/to/session.jsonl` |
| Bare partial id | `c6428f4f` |
| Stdin | `-` |

> **Note:** Live streaming currently supports the **claude** harness. Other harnesses
> (`goose`, `codex`, etc.) are accepted by the handle parser but will return a
> `NotImplementedError` if live streaming is not yet implemented for that adapter.

## Output Format

Each event prints as a single line:

```
<timestamp[:19]> <[tool_name] | role>: <content[:120]>
```

Example:

```
2026-05-29T14:23:01 [Bash]: ls -la /tmp/...
2026-05-29T14:23:02 assistant: The directory contains...
2026-05-29T14:23:03 [Edit]: /path/to/file.py
```

Press **Ctrl-C** to stop watching.

## Finding a Session Handle

If you don't know the session handle:

```bash
# List recent Claude sessions that match a keyword
sio search "keyword" --files

# Or list all agents with on-disk history
sio search --list-agents
```

## Follow-up

- After watching, mine the session for errors → `sio mine --session <handle>`
- Scope error browsing to that session → `sio errors --session <handle>`
- Generate improvement rules from that session → `sio suggest --session <handle>`
