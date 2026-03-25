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

## v2.1 Commands — Positive Pattern Mining, Recall & Training

### `sio flows`

Discover recurring positive tool sequences using n-gram extraction and RLE compression. No LLM required ($0).

```bash
sio flows [--min-support <n>] [--since <time>] [--top <n>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--min-support` | `2` | Minimum number of sessions a flow must appear in |
| `--since` | `7 days` | Time window for session analysis |
| `--top` | `20` | Maximum number of flows to display |

**How it works:**

1. Parses session transcripts for tool call sequences
2. Extracts n-grams (2-5 tool sequences)
3. Applies RLE compression to collapse repeated tool calls
4. Scores flows using success heuristics (no errors after the sequence)
5. Stores results in the `flow_events` table

**Examples:**

```bash
# Show flows from the last 7 days
sio flows

# Only show flows appearing in 3+ sessions
sio flows --min-support 3

# Analyze the last month
sio flows --since "1 month"
```

---

### `sio distill`

Extract the winning path from a session, removing failed attempts, retries, and dead ends. No LLM required ($0).

```bash
sio distill [--latest] [--session <id>] [--output <path>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--latest` | — | Distill the most recent session |
| `--session` | — | Distill a specific session by ID |
| `--output` | stdout | Write the playbook to a file |

**What it produces:**

A focused playbook containing only the steps that led to a successful outcome. Failed tool calls, repeated attempts, and exploratory dead ends are stripped out.

**Examples:**

```bash
# Distill the latest session
sio distill --latest

# Distill a specific session and save to file
sio distill --session abc123 --output playbook.md
```

---

### `sio recall`

Topic-filtered distillation with struggle-then-fix detection and optional Gemini polish.

```bash
sio recall "<query>" [--polish] [--since <time>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `<query>` | (required) | Topic to search for across sessions |
| `--polish` | off | Apply Gemini polish pass (~$0.02-0.05) |
| `--since` | `30 days` | Time window to search |

**How it works:**

1. Filters sessions by topic relevance to the query
2. Detects struggle-then-fix patterns (repeated failures followed by a success)
3. Extracts the fix as the key insight
4. Optionally sends to Gemini for polishing into a clean runbook

**Cost:** $0 without `--polish`, ~$0.02-0.05 with `--polish`.

**Examples:**

```bash
# Find how you solved dbt model issues
sio recall "dbt model debugging"

# Same but with Gemini cleanup
sio recall "dbt model debugging" --polish

# Search a wider window
sio recall "Snowflake permissions" --since "3 months"
```

---

### `sio export-dataset`

Export JSONL and Parquet training datasets for DSPy optimization.

```bash
sio export-dataset --task <task> [--output-dir <path>] [--format <fmt>] [--dry-run]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--task` | (required) | Task type: `routing`, `recovery`, `flow`, or `all` |
| `--output-dir` | `~/.sio/datasets/` | Output directory |
| `--format` | `jsonl` | Output format: `jsonl` or `parquet` |
| `--dry-run` | off | Show what would be exported without writing files |

**Task types:**

| Task | Training Data | Use Case |
|------|--------------|----------|
| `routing` | Error → correct tool mapping | Teach the agent which tool to use |
| `recovery` | Failed attempt → recovery steps | Teach error recovery patterns |
| `flow` | Tool sequence → outcome | Teach positive workflows |

**Examples:**

```bash
# Export all tasks as JSONL
sio export-dataset --task all

# Export only flow data as Parquet
sio export-dataset --task flow --format parquet

# Preview what would be exported
sio export-dataset --task all --dry-run
```

---

### `sio collect-recall`

Store labeled recall examples for DSPy training. These examples serve as ground truth for optimizing the recall module.

```bash
sio collect-recall "<query>" [--label <label>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `<query>` | (required) | The recall query to store |
| `--label` | — | Quality label for the example |

**Examples:**

```bash
# Collect an example from a topic search
sio collect-recall "fixing Playwright timeouts"

# Collect with an explicit quality label
sio collect-recall "Cube API connection" --label good
```

Stored examples go into the `recall_examples` DB table and are used by `sio train --task recall`.

---

### `sio train`

Run DSPy BootstrapFewShot or GEPA optimization on exported datasets. Requires an LLM API key (~$0.02-0.05 per run).

```bash
sio train --task <task> [--optimizer <opt>] [--model <model>] [--max-demos <n>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--task` | (required) | Task: `routing`, `recovery`, `flow`, `recall`, or `all` |
| `--optimizer` | `bootstrap` | Optimizer: `bootstrap` (BootstrapFewShot) or `gepa` (GEPA) |
| `--model` | config default | LLM model (any litellm-compatible model or Azure OpenAI) |
| `--max-demos` | `8` | Maximum few-shot examples |

**Full training pipeline:**

```bash
# Step 1: Collect data ($0)
sio mine --since "30 days"
sio flows
sio export-dataset --task all

# Step 2: Label recall examples ($0)
sio collect-recall "common topic"

# Step 3: Polish with Gemini (optional, ~$0.02)
sio recall "common topic" --polish

# Step 4: Train DSPy modules (~$0.05)
sio train --task all

# Step 5: Use the trained model ($0)
sio recall "new query"
```

**Examples:**

```bash
# Train all tasks with default optimizer
sio train --task all

# Train recall with GEPA optimizer
sio train --task recall --optimizer gepa

# Train with a specific model
sio train --task routing --model azure/gpt-5-mini
```

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
