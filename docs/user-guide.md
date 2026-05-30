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

## Cross-Agent Search & Session-Scoped Analysis

SIO absorbed the standalone `session-search` tool. `sio search` finds sessions across all six
coding-agent harnesses. Every analysis command (`mine`, `errors`, `suggest`, `collect-recall`)
can be scoped to a single session with `--session`, turning SIO into a targeted debugger for one
transcript. Session IDs are canonical **`agent:native_id`** URIs (e.g. `claude:<uuid>`,
`goose:<name>`); legacy bare IDs are matched transparently. The pipe idiom ties the two together:
`sio search "dbt" --files | sio errors --session -`.

---

### `sio search`

Search coding-agent session history across all harnesses. All flags pass through to the absorbed
`session-search` engine, whose `--help` lists the full flag set.

```bash
sio search "<pattern>" [--agent <harness>] [--recent <days>] [--files] [--count]
                       [--specstory] [--backups] [--all] [--format <fmt>]
                       [--case-sensitive] [--fast | --no-fast]
                       [--limit <n>] [--context <n>] [--clean]
                       [--list-agents]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `<pattern>` | (required) | Search pattern (plain text) |
| `--agent` | `claude` | Which harness to search: `claude`, `codex`, `goose`, `opencode`, `gemini`, `aider`, or `all` |
| `--recent <N>` | `0` (all time) | Only files with mtime within N days |
| `--limit <N>` | `0` (unlimited) | Cap matches per agent |
| `--files` | off | Emit unique source file paths (one per line; pipe into `--session`) |
| `--count` | off | Emit per-file match counts (`N\tpath`) |
| `--specstory` | off | Claude: search SpecStory MD only |
| `--backups` | off | Claude: include `~/.claude/backups` |
| `--all` | off | Claude: JSONL + SpecStory + backups |
| `--format` | `jsonl` | Output format for Python parsers: `jsonl` or `text` |
| `--context <N>` | `1` | Lines of context (fast/legacy modes) |
| `--case-sensitive` | off | Case-sensitive match |
| `--clean` | off | Un-escape JSON escapes in content |
| `--fast` | auto | Force ripgrep fast path (claude only) |
| `--no-fast` | — | Disable ripgrep fast path |
| `--list-agents` | — | Print inventory of agents with on-disk history, then exit |

**Cross-agent adapters:**

Claude uses a direct file adapter against `~/.claude/projects/**/*.jsonl`.  All other agents
(`codex`, `goose`, `opencode`, `gemini`, `aider`) use a *search-backed adapter* built on their
respective on-disk stores:

| Agent | Store location |
|-------|---------------|
| `codex` | `~/.codex/history.jsonl` + `~/.codex/sessions/` |
| `goose` | `~/.local/share/goose/sessions/` |
| `opencode` | `~/.local/share/opencode/opencode.db` |
| `gemini` | `~/.gemini/tmp/` |
| `aider` | per-repo `.aider.chat.history.md` files under `~/dev/` |

Pass `--agent all` to fan out across all six harnesses in one call. Use `--list-agents` to see
which harnesses have on-disk history on this machine.

**Examples:**

```bash
# Search Claude transcripts (default)
sio search "dbt compile" --recent 7

# Search all harnesses for a pattern
sio search "snowflake" --agent all --recent 30

# Emit file paths for piping
sio search "PromptChain" --files

# Emit path + hit count
sio search "playwright" --count

# Check which agents have history installed
sio search --list-agents
```

---

### `--session <handle>` — Scope a command to one session

Every analysis command accepts `--session` to restrict processing to a single transcript.

**Handle forms accepted:**

| Form | Example | Behaviour |
|------|---------|-----------|
| Canonical URI | `claude:<uuid>` | Parsed directly |
| Other-agent URI | `goose:<name>` | Routed to that agent's adapter |
| File path | `/home/user/.claude/projects/-x/abc-123.jsonl` | Agent inferred from path; stem becomes native ID |
| Bare/partial ID | `c6428f4f` | Assumed `claude:`; fuzzy-resolved against DB (lists candidates if ambiguous) |
| `sio search --count` line | `3\t/path/to/abc.jsonl` | Path extracted from tab-separated line |
| `-` (hyphen) | `-` | Read handle from stdin (first line) |

**Commands that accept `--session`:**

| Command | Option name | Behaviour when set |
|---------|------------|-------------------|
| `sio mine` | `--session` | Mine ONE session; `--since` becomes optional |
| `sio errors` | `--session` | Filter error table to ONE session |
| `sio suggest` | `--session` | Run the full cluster → suggest pipeline on ONE session |
| `sio collect-recall` | `--session` | Distill a specific session JSONL path |

`sio mine` routes non-Claude sessions through the cross-agent adapter EXTRACT layer automatically.
`sio errors` and `sio suggest` apply fuzzy partial-ID resolution: a short bare id is expanded
against the DB and reports candidates when more than one session matches.

**Pipe idiom:**

```bash
# Find sessions, then scope errors to the first result
sio search "dbt compile error" --files | head -1 | sio errors --session -

# One-liner equivalent
sio errors --session "$(sio search 'dbt compile error' --files | head -1)"
```

---

### `sio watch`

Live-tail a session's events as they happen (Phase B; Claude-native sessions only so far).
`--session` is **required**.

```bash
sio watch --session <handle> [--from-start] [--tools-only]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--session` | (required) | Session to watch (`agent:native_id`, a file path, `-` for stdin, or a bare id) |
| `--from-start` | off | Replay existing events first, then follow new ones |
| `--tools-only` | off | Only surface `tool_use` events |

**Examples:**

```bash
# Watch the current Claude session live
sio watch --session claude:<uuid>

# Replay from the beginning, then follow
sio watch --session claude:<uuid> --from-start

# Filter to tool calls only
sio watch --session claude:<uuid> --tools-only
```

---

### `sio db backfill-sessions`

Migrate legacy bare session IDs (pre-merge rows, no colon) to the canonical `agent:<id>` form.
Idempotent and non-destructive: only rows without a colon are touched. A timestamped backup is
created before any write.

```bash
sio db backfill-sessions [--db-path <path>] [--agent <name>] [--dry-run]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--db-path` | `~/.sio/sio.db` | Path to the SIO database |
| `--agent` | `claude` | Agent namespace to prefix legacy bare IDs with |
| `--dry-run` | off | Show what would change without writing (skips the backup step) |

**Examples:**

```bash
# Preview what would be migrated
sio db backfill-sessions --dry-run

# Run the migration (auto-backup taken first)
sio db backfill-sessions

# If your legacy rows belong to a different agent
sio db backfill-sessions --agent goose
```

After the migration, `--session` handles will resolve correctly for both old (bare) and new
(canonical) rows during the transition window — the match helper matches both forms
transparently until all rows are canonical.

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

---

## Observability

### `sio changes`

List all applied CLAUDE.md changes and their current status.

```bash
sio changes
```

Displays a Rich table with columns: change ID, suggestion ID, target file, applied timestamp, status (active or rolled back), and description. Complements `sio rollback <id>` — run `sio changes` first to find the change ID to roll back.

---

### `sio briefing`

Show a concise session-start briefing of actionable SIO insights drawn from recent patterns and pending suggestions.

```bash
sio briefing [--json]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--json` | off | Output as a JSON object `{"briefing": "..."}` instead of plain text |

**Examples:**

```bash
# Human-readable briefing
sio briefing

# Machine-readable for agent injection
sio briefing --json
```

---

### `sio trend`

Show growth or decline of error pattern clusters over time using bucketed counts and trend arrows.

```bash
sio trend [--daily|--weekly|--monthly] [--top <n>] [--windows <n>] [--grep <terms>] [--pattern <id>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--weekly` | (default) | Weekly time buckets |
| `--daily` | — | Daily time buckets |
| `--monthly` | — | Monthly time buckets |
| `--top` | `10` | Show top-N patterns by total error count |
| `--windows` | `6` | Number of time buckets to include, counted backwards from now |
| `--grep` | None | Filter patterns by substring match on description (comma-separated OR) |
| `--pattern` | None | Filter to a single pattern by numeric id or slug |

The last column shows a trend arrow: `↑` growing, `↓` shrinking, `→` stable — based on a comparison of the two most recent buckets.

**Examples:**

```bash
# Default: weekly, top 10, last 6 weeks
sio trend

# Daily buckets, last 14 days, show only top 5
sio trend --daily --top 5 --windows 14

# Focus on patterns mentioning zeno or cdia
sio trend --grep "zeno,cdia"

# Single pattern by slug
sio trend --pattern user-correction-this
```

---

### `sio gepa-status`

Show live GEPA (or MIPROv2) optimizer progress for the most recently started `sio optimize` run.

```bash
sio gepa-status [--watch]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--watch` | off | Re-print every 5 seconds until the process finishes |

Reads the latest runlog JSON from `~/.sio/runs/` and surfaces: active optimizer name, current iteration / trial, best validation score so far, per-iteration score history (last 10), idle time, parse error / truncation counters, and any GEPA/MIPRO warning tiers that have fired.

**Examples:**

```bash
# One-shot status check
sio gepa-status

# Live dashboard while optimize is running
sio gepa-status --watch
```

---

### `sio rule-outcomes`

Show per-rule outcome metrics — how error counts changed before and after each CLAUDE.md rule was first seen.

```bash
sio rule-outcomes [RULE_ID] [--window <days>] [--since <date>] [--format text|json]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `RULE_ID` | (optional) | Rule identifier in `tools/foo.md#<sha[:12]>` format. Omit to list all rules with data. |
| `--window` | `7` | Pre/post window in days around each rule's first-seen date |
| `--since` | None | Only consider error records on or after this date (ISO-8601 or `"N days"`) |
| `--format` | `text` | Output format: `text` (Rich panels) or `json` |

**Examples:**

```bash
# List all rules with outcome data
sio rule-outcomes

# Drill into a specific rule
sio rule-outcomes "tools/retry.md#abc123def456"

# Wider window, JSON output
sio rule-outcomes --window 14 --format json
```

---

### `sio rule-audit`

Audit a single rule with concrete before/after error samples; optionally run an LLM judge to score applicability.

```bash
sio rule-audit RULE_ID [--samples <n>] [--window <days>] [--judge] [--yes] [--write-report] [--format text|json]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `RULE_ID` | (required) | Rule identifier in `tools/foo.md#<sha[:12]>` format |
| `--samples` | `10` | Number of representative errors to display from each side (before / after) |
| `--window` | `7` | Pre/post window in days around rule first-seen |
| `--judge` | off | Run LLM-as-judge on AFTER-window samples (PAID — prompts for confirmation) |
| `--yes` | off | Skip the cost-confirmation prompt (only used with `--judge`) |
| `--write-report` | off | Write the audit output to `~/.sio/audits/<rule_hash>_<ts>.md` |
| `--format` | `text` | Output format: `text` or `json` |

**Examples:**

```bash
# Review 10 samples each side, text output
sio rule-audit "tools/retry.md#abc123def456"

# Wider sample, save to file
sio rule-audit "tools/retry.md#abc123def456" --samples 20 --write-report

# Full LLM-judge pass (paid)
sio rule-audit "tools/retry.md#abc123def456" --judge --yes
```

---

### `sio analyze same-error`

Find error signatures that recur across multiple sessions without the agent learning from them.

```bash
sio analyze same-error [--min-count <n>] [--since <time>] [--limit <n>] [--with-context]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--min-count` | `3` | Minimum repetition count to surface a signature |
| `--since` | None | Time window, e.g. `"30 days"` or `"1 week"` |
| `--limit` | `30` | Maximum number of findings to display |
| `--with-context` | off | Include up to 3 `context_before` snippets per finding |

The unit of analysis is the normalized `error_text` signature hash — the same hash space used by the clustering classifier.

**Examples:**

```bash
# Find errors seen 3+ times
sio analyze same-error

# Tighter filter, last week only
sio analyze same-error --min-count 5 --since "7 days"

# Include agent-intent context snippets
sio analyze same-error --with-context
```

---

## Ground Truth & Training

### `sio curate`

Produce a filtered, curated JSONL training dataset from the mined error corpus — the primary input for `sio amplify` and `sio optimize --trainset-file`.

```bash
sio curate [--since <time>] [--emphasis] [--classified] [--pattern <slug>]
           [--pattern-prefix <prefix>] [--error-type <type>]...
           [--exclude-corrections/--include-corrections]
           [--exclude-cascade/--include-cascade]
           [--has-positive-recovery] [--recovery-window-seconds <n>]
           [--limit <n>] [-o <path>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--since` | `7 days` | Time window: `"7 days"`, `"30 days"`, or an ISO date |
| `--emphasis` | off | Require `!!` or `??` in user message (frustration markers) |
| `--classified` | off | Require `pattern_id NOT NULL` (skip unclassified records) |
| `--pattern` | None | Exact `pattern_id` slug to filter on |
| `--pattern-prefix` | None | LIKE prefix for `pattern_id` (e.g. `tool_failure__`) |
| `--error-type` | — | Restrict to one or more error types; repeat flag for multiple |
| `--exclude-corrections` | on | Drop `user_correction` rows |
| `--exclude-cascade` | on | Drop cascade-failure rows |
| `--has-positive-recovery` | off | Require a `positive_records` event within `--recovery-window-seconds` |
| `--recovery-window-seconds` | `600` | Recovery window when using `--has-positive-recovery` |
| `--limit` | None | Max rows to emit (newest first) |
| `-o` / `--output` | `~/.sio/curated/<ts>.jsonl` | Output JSONL path |

Outputs a JSONL of canonical `PatternToRule` DSPy example shapes plus a Markdown preview with row count, category distribution, and 10 sample rows. The dataset is auto-registered in the `trainsets` table.

**Examples:**

```bash
# Basic curate, last 7 days
sio curate

# Wider window, only frustration-marked errors
sio curate --since "30 days" --emphasis

# Only errors that had a recovery within 10 minutes
sio curate --has-positive-recovery --recovery-window-seconds 600

# Filter to a specific pattern family
sio curate --pattern-prefix "tool_failure__"
```

---

### `sio amplify`

Synthesize N variants per curated row using an LLM, then filter out low-quality or duplicate variants. Paid command — see cost callout in output.

```bash
sio amplify -i <curated.jsonl> [-o <output.jsonl>] [-n <n-per-row>]
            [--min-judge-score <score>] [--max-workers <n>]
            [--task-mode work|cheap|free|personal|personal-strong]
            [--budget-override <usd>]
            [--no-diversity-filter] [--diversity-threshold <threshold>]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `-i` / `--input` | (required) | Input JSONL from `sio curate` |
| `-o` / `--output` | `~/.sio/amplified/<input>_amplified.jsonl` | Output JSONL path |
| `-n` / `--n-per-row` | `10` | Synthetic variants to generate per input row — **the primary knob** |
| `--min-judge-score` | `0.6` | Drop variants whose LLM-judge score falls below this |
| `--max-workers` | `8` | Thread-pool parallelism for LLM calls |
| `--task-mode` | config default | LM tier: `cheap` (Flash, recommended), `work` (Pro), `free` (Ollama), `personal` (gpt-4o-mini), `personal-strong` (gpt-5) |
| `--budget-override` | None | Override the 24h spend cap for this run |
| `--no-diversity-filter` | off | Disable cosine-similarity deduplication of variants |
| `--diversity-threshold` | `0.95` | Cosine similarity above which variants are deduplicated (lower = more aggressive) |

**Cost callout:** Amplification calls the configured LLM for every input row × `--n-per-row`. Start with `--task-mode cheap` and a small curated set. Budget check fires automatically before the first call.

For a full conceptual guide and cost strategy see [`docs/AMPLIFY_GUIDE.md`](AMPLIFY_GUIDE.md).

**Examples:**

```bash
# Amplify with 5 variants per row (recommended starting point)
sio amplify -i ~/.sio/curated/curated_20260520_120000.jsonl -n 5

# Use cheapest tier, budget override to $2
sio amplify -i curated.jsonl -n 10 --task-mode cheap --budget-override 2.0

# Tighter diversity filter
sio amplify -i curated.jsonl -n 10 --diversity-threshold 0.85
```

---

### `sio promote-positives`

Promote positive session signals (confirmations, gratitude, session-success events) from the `positive_records` table into the ground truth review queue.

```bash
sio promote-positives [--since <time>] [--min-confidence <score>] [--dry-run]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--since` | `7 days` | Time window of `positive_records` to consider |
| `--min-confidence` | `0.0` | Drop positives whose `sentiment_score` is below this |
| `--dry-run` | off | Show what would be promoted without writing |

Inserted rows land in `ground_truth` with `label='pending'`. Run `sio suggest-review` or `sio approve <id>` afterward to move them to `label='positive'` and into the next `sio optimize` trainset.

**Examples:**

```bash
# Promote last week's positive signals
sio promote-positives

# Wider window, only high-confidence signals
sio promote-positives --since "30 days" --min-confidence 0.5

# Preview without writing
sio promote-positives --dry-run
```

---

### `sio promote-to-gold`

Promote `behavior_invocations` rows that the user rated as both satisfied and correct into the `gold_standards` table for DSPy training.

```bash
sio promote-to-gold [INVOCATION_ID] [--all-eligible] [--dry-run]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `INVOCATION_ID` | — | Promote a single invocation by its numeric ID |
| `--all-eligible` | off | Bulk-promote all invocations with `user_satisfied=1` AND `correct_outcome=1` |
| `--dry-run` | off | Show what would be promoted without writing |

A row is eligible when both `user_satisfied=1` AND `correct_outcome=1`. Use `sio review` to label invocations before running this command.

**Examples:**

```bash
# Promote one invocation
sio promote-to-gold 42

# Bulk promote all eligible
sio promote-to-gold --all-eligible

# Preview bulk promotion
sio promote-to-gold --all-eligible --dry-run
```

---

### `sio promote-flow`

Promote a specific flow pattern (identified by its hash from `sio flows`) into a Claude Code skill Markdown file.

```bash
sio promote-flow <FLOW_HASH>
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `FLOW_HASH` | Flow hash from the `sio flows` output table |

Generates a skill Markdown file in `~/.claude/skills/` based on the observed tool sequence and success heuristics.

**Examples:**

```bash
# First find the hash
sio flows

# Then promote
sio promote-flow abc123def456
```

---

### `sio promote-rule`

Convert a frequently violated CLAUDE.md rule into a runtime PreToolUse hook that warns or blocks the offending tool call.

```bash
sio promote-rule <RULE_INDEX> [--mode warn|block] [--since <date>] [--write]
```

**Arguments & Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `RULE_INDEX` | (required) | 1-based index from the `sio violations` report |
| `--mode` | `warn` | `warn`: print rule text + continue. `block`: prevent the tool call entirely. |
| `--since` | None | Only count violations after this ISO-8601 date |
| `--write` | off | Actually write the hook script and register it in `~/.claude/settings.json`; without this flag the command is a preview only |

Typical workflow: start with `warn` mode until the violation count is decisively shrinking, then graduate to `block`.

**Examples:**

```bash
# See what's violating rules most
sio violations

# Preview what would be promoted for rule #1
sio promote-rule 1

# Write a warn-mode hook
sio promote-rule 1 --mode warn --write

# Graduate to block after soak
sio promote-rule 1 --mode block --write
```

---

### `sio differential-flows`

Find twin flows — the same tool sequence that appears with both success and failure outcomes — and export paired training examples. Requires no LLM.

```bash
sio differential-flows [--min-success <n>] [--min-failure <n>] [--per-cohort <n>]
                       [--max-hashes <n>] [-o <path>] [--positives-for-builder]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--min-success` | `3` | Minimum successful events per flow hash to qualify as a twin |
| `--min-failure` | `3` | Minimum failed events per flow hash to qualify as a twin |
| `--per-cohort` | `5` | Samples drawn from each cohort (success / failure) per twin |
| `--max-hashes` | None | Cap the number of twin hashes processed (debug) |
| `-o` / `--output` | `~/.sio/differential/differential_<ts>.jsonl` | Output JSONL path |
| `--positives-for-builder` | off | Emit flat positive examples in `PatternToRule` shape (for `dataset_builder`) instead of paired cohort rows |

The output JSONL is automatically registered in the `trainsets` table and can be fed directly to `sio optimize --trainset-file`.

**Examples:**

```bash
# Default: paired success/failure cohorts
sio differential-flows

# Higher bar, more samples per cohort
sio differential-flows --min-success 5 --per-cohort 10

# Emit only the successful side for training
sio differential-flows --positives-for-builder
```
