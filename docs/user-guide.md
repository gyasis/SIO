# User Guide

Complete CLI reference for SIO.

## v2 Commands — Session Mining & Suggestions

### `sio mine`

Mine recent AI coding sessions for errors and failures.

```bash
sio mine --since <time-expression> [--project <name>] [--source <type>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--since` | (required) | Time window — see formats below |
| `--project` | None | Filter files by project name substring |
| `--source` | `both` | Source type: `specstory`, `jsonl`, or `both` |

**Time expressions** (all case-insensitive):

| Format | Example |
|--------|---------|
| Relative duration | `"3 days"`, `"2 weeks"`, `"1 month"`, `"6 hours"`, `"30 minutes"` |
| Shorthand | `"3d"`, `"1w"`, `"2mo"`, `"6h"`, `"30min"`, `"1y"` |
| Natural language | `"yesterday"`, `"last week"`, `"last month"`, `"3 days ago"` |
| Absolute date | `"2026-01-15"`, `"Jan 15 2026"`, `"2026-01-15T10:30:00Z"` |

**Examples:**

```bash
# Mine last 3 days from all sources
sio mine --since "3 days"

# Mine last week, SpecStory files only
sio mine --since "1 week" --source specstory

# Mine since yesterday for a specific project
sio mine --since "yesterday" --project my-api

# Mine using shorthand
sio mine --since "6h"
```

**Error types detected:**

| Type | Description |
|------|-------------|
| `tool_failure` | Tool call returned an error |
| `user_correction` | User corrected the AI's output |
| `repeated_attempt` | Same action attempted multiple times |
| `undo` | User undid/reverted an AI action |

---

### `sio patterns`

Show discovered error patterns ranked by importance.

```bash
sio patterns
```

Displays a Rich table with columns: rank, pattern description, error count, session count, last seen date, and importance score.

The score combines frequency (how often) and recency (how recently) to prioritize patterns that are both common and current.

---

### `sio datasets`

List all built datasets.

```bash
sio datasets
```

Shows dataset ID, pattern ID, file path, and positive/negative example counts.

### `sio datasets collect`

Build a targeted dataset from specific criteria.

```bash
sio datasets collect [--since <time>] [--error-type <type>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--since` | None | Time range filter |
| `--error-type` | None | Filter by error type |

---

### `sio suggest-review`

Interactive review of pending suggestions.

```bash
sio suggest-review
```

For each pending suggestion, displays:
- Description of the proposed change
- Confidence score (0-100%)
- Target file (e.g., CLAUDE.md)
- Change type (rule, hook, skill)
- The proposed change content

**Review actions:**
- `a` — Approve (mark for application)
- `r` — Reject (dismiss permanently)
- `d` — Defer (skip for now, review later)
- `q` — Quit the review session

Both approve and reject prompt for an optional note.

---

### `sio approve <id>`

Approve a specific suggestion by ID.

```bash
sio approve 42 --note "looks good"
```

---

### `sio reject <id>`

Reject a specific suggestion by ID.

```bash
sio reject 42 --note "too aggressive"
```

---

### `sio rollback <id>`

Revert an applied change by its change ID.

```bash
sio rollback 7
```

Restores the target file to its pre-change state and marks the change as rolled back in the database.

---

### `sio schedule install`

Install daily and weekly cron jobs.

```bash
sio schedule install
```

Creates two cron entries identified by `# SIO passive analysis`:
- `@daily` — runs `sio schedule run --mode daily`
- `@weekly` — runs `sio schedule run --mode weekly`

Idempotent — safe to run multiple times.

### `sio schedule status`

Check whether cron jobs are installed.

```bash
sio schedule status
```

---

### `sio status`

Show pipeline statistics.

```bash
sio status
```

Reports: errors mined, patterns found, datasets built, pending reviews, applied changes.

---

## v1 Commands — Telemetry & Optimization

### `sio install`

Install SIO hooks for Claude Code.

```bash
sio install [--platform claude-code] [--auto]
```

This:
1. Creates the SIO database at `~/.sio/claude-code/behavior_invocations.db`
2. Registers a PostToolUse hook in `~/.claude/settings.json`
3. Saves platform configuration

### `sio health`

Show per-skill health metrics.

```bash
sio health [--platform claude-code] [--skill <name>] [--format table|json]
```

Displays satisfaction rates, invocation counts, and flags for skills that need attention.

### `sio review`

Batch-review unlabeled telemetry invocations.

```bash
sio review [--platform claude-code] [--session <id>] [--limit 20]
```

For each unlabeled invocation, choose:
- `++` — satisfied
- `--` — unsatisfied
- `s` — skip
- `q` — quit

### `sio optimize <skill>`

Run DSPy prompt optimization for a skill.

```bash
sio optimize my-skill [--optimizer gepa|miprov2|bootstrap] [--dry-run]
```

### `sio purge`

Remove old telemetry records.

```bash
sio purge [--platform claude-code] [--days 90] [--dry-run]
```

### `sio export`

Export telemetry data.

```bash
sio export [--platform claude-code] [--format json|csv] [-o output.json]
```
