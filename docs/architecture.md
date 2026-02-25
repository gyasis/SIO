# Architecture

## System Overview

SIO is a closed-loop system that passively mines AI coding sessions, discovers error patterns, and proposes configuration improvements. It operates in two generations:

- **v1** — Real-time telemetry hooks that capture tool invocations, label them, and optimize prompts via DSPy
- **v2** — Batch session mining that processes existing SpecStory/JSONL files, clusters errors, and generates improvement suggestions

Both share the same CLI entry point (`sio`) and database infrastructure.

## Data Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                        SOURCE FILES                              │
│  ~/.specstory/history/*.md    ~/.claude/projects/**/*.jsonl       │
└──────────────┬───────────────────────────────┬───────────────────┘
               │                               │
               ▼                               ▼
        ┌─────────────┐                ┌──────────────┐
        │ SpecStory   │                │ JSONL        │
        │ Parser      │                │ Parser       │
        └──────┬──────┘                └──────┬───────┘
               │                               │
               ▼                               ▼
        ┌──────────────────────────────────────────┐
        │         Error Extractor                   │
        │  Classifies: tool_failure,                │
        │  user_correction, repeated_attempt, undo  │
        └──────────────────┬───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │         SQLite (error_records)            │
        └──────────────────┬───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │      Pattern Clusterer (fastembed)        │
        │  Greedy clustering, cosine similarity     │
        │  Threshold: 0.80                          │
        └──────────────────┬───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │           Ranker                          │
        │  Score = frequency × recency weight       │
        └──────────────────┬───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │       Dataset Builder                     │
        │  Positive/negative examples per pattern   │
        └──────────────────┬───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │     Suggestion Generator                  │
        │  Proposes CLAUDE.md rules, hooks, skills  │
        │  Confidence = 0.4×count + 0.3×quality     │
        │               + 0.3×rank                  │
        └──────────────────┬───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │       Home File Writer                    │
        │  ~/.sio/suggestions.md                    │
        └──────────────────┬───────────────────────┘
                           │
                    Human Review
                    (approve/reject/defer)
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │         Change Applier                    │
        │  Appends to target files                  │
        │  Stores diff_before/diff_after            │
        └──────────────────┬───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │         Rollback Engine                   │
        │  Restores pre-change state               │
        │  Changelog at ~/.sio/changelog.md         │
        └──────────────────────────────────────────┘
```

## Module Map

### `src/sio/mining/` — Session File Parsing

| Module | Purpose |
|--------|---------|
| `specstory_parser.py` | Parses SpecStory markdown files. Recognizes `**Human:**`/`**Assistant:**` role prefixes with `---` line separators. Extracts `[Tool call:]` and `[Tool error:]` annotations. |
| `jsonl_parser.py` | Parses Claude JSONL transcript files. Each line is a JSON message with role, content, and tool metadata. |
| `error_extractor.py` | Classifies messages into 4 error types: `tool_failure` (tool returned error), `user_correction` (user corrected AI), `repeated_attempt` (same action retried), `undo` (user reverted action). |
| `time_filter.py` | Flexible date filtering using `python-dateutil`. Supports relative durations, natural language, shorthands, and absolute dates. SpecStory files use filename-encoded timestamps; others use mtime. |
| `pipeline.py` | Orchestrates the full mining flow: collect files → filter by time → filter by project → parse → extract errors → insert into DB. |

### `src/sio/clustering/` — Pattern Discovery

| Module | Purpose |
|--------|---------|
| `pattern_clusterer.py` | Uses fastembed (`all-MiniLM-L6-v2`, 384 dimensions) to embed error messages, then greedy clustering with cosine similarity threshold 0.80. |
| `ranker.py` | Scores patterns by `frequency × recency_weight`. Recent errors score higher than old ones. |

### `src/sio/datasets/` — Training Data

| Module | Purpose |
|--------|---------|
| `builder.py` | Builds positive/negative example datasets per pattern. Positive = errors matching the pattern, negative = non-matching errors from the same sessions. |
| `accumulator.py` | Auto-accumulates new errors into existing datasets as they're mined. |
| `lineage.py` | Tracks which session files contributed to each dataset. |

### `src/sio/suggestions/` — Improvement Proposals

| Module | Purpose |
|--------|---------|
| `generator.py` | Generates improvement suggestions from patterns. Proposes CLAUDE.md rules, hooks, or skill adjustments. |
| `confidence.py` | Scores suggestion quality: `0.4 × error_count + 0.3 × dataset_quality + 0.3 × rank_score`. |
| `home_file.py` | Writes `~/.sio/suggestions.md` — a ranked markdown file showing all pending suggestions with approve/reject commands. |

### `src/sio/review/` — Human-in-the-Loop

| Module | Purpose |
|--------|---------|
| `reviewer.py` | Review workflow: `review_pending()`, `approve()`, `reject()`, `defer()`. Tracks review timestamps and notes. |
| `tagger.py` | AI tagging (heuristic explanation) and human tagging (category labels). |

### `src/sio/applier/` — Change Application

| Module | Purpose |
|--------|---------|
| `writer.py` | Applies approved suggestions to target files. Appends content (never overwrites). Stores before/after diffs. |
| `rollback.py` | Reverts applied changes by restoring pre-change content. |
| `changelog.py` | Appends entries to `~/.sio/changelog.md` with timestamps, IDs, and target files. |

### `src/sio/scheduler/` — Passive Automation

| Module | Purpose |
|--------|---------|
| `runner.py` | Orchestrates the full pipeline: mine → cluster → dataset → suggest → home file. |
| `cron.py` | Manages cron entries marked with `# SIO passive analysis`. Daily at midnight, weekly on Sunday. |

### `src/sio/core/` — Shared Infrastructure

| Module | Purpose |
|--------|---------|
| `db/schema.py` | SQLite schema initialization (WAL mode). Tables: `error_records`, `patterns`, `datasets`, `suggestions`, `applied_changes`, plus v1 tables. |
| `db/queries.py` | Insert/query functions for all tables. |
| `config.py` | Reads `~/.sio/config.toml` with sensible defaults. |
| `embeddings/` | fastembed and API embedding backends. |
| `arena/` | Drift and collision detection for v1 optimization. |
| `telemetry/` | v1 hook-based invocation capture. |

### `src/sio/adapters/claude_code/` — Platform Integration

| Module | Purpose |
|--------|---------|
| `installer.py` | One-command setup: creates DB, registers PostToolUse hook in `~/.claude/settings.json`. |
| `hooks/` | PostToolUse hook that captures tool invocations for v1 telemetry. |
| `skills/` | Claude Code skills: sio-feedback, sio-health, etc. |

## Database Schema

SIO uses SQLite with WAL mode. The v2 database lives at `~/.sio/sio.db`.

**Key tables:**

| Table | Purpose |
|-------|---------|
| `error_records` | Raw errors extracted from sessions (type, message, source file, timestamp) |
| `patterns` | Clustered error patterns with descriptions and scores |
| `datasets` | Positive/negative training examples per pattern |
| `suggestions` | Generated improvement proposals with status tracking |
| `applied_changes` | Applied changes with before/after diffs for rollback |

## File System Layout

```
~/.sio/
  sio.db                    # v2 database (SQLite WAL)
  config.toml               # Configuration (optional)
  suggestions.md            # Home file — pending suggestions
  changelog.md              # Applied change log
  claude-code/
    behavior_invocations.db # v1 telemetry database

~/.claude/
  settings.json             # Claude Code settings (hooks registered here)

~/.specstory/
  history/*.md              # SpecStory session transcripts (input)

~/.claude/projects/
  **/*.jsonl                # Claude JSONL transcripts (input)
```
