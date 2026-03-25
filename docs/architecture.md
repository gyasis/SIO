# Architecture

## System Overview

SIO is a closed-loop system that passively mines AI coding sessions, discovers error patterns, and proposes configuration improvements. It operates in two generations:

- **v1** — Real-time telemetry hooks that capture tool invocations, label them, and optimize prompts via DSPy
- **v2** — Batch session mining that processes existing SpecStory/JSONL files, clusters errors, and generates improvement suggestions
- **v2.1** — Positive pattern mining (flows), session distillation, topic recall, JSONL/Parquet dataset export, and DSPy training pipeline

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
| `flow_extractor.py` | Extracts tool call sequences from sessions. Applies n-gram extraction (2-5 grams) and RLE compression to collapse repeated tools. Uses success heuristics (no errors following the sequence) to score flows. |
| `flow_pipeline.py` | Flow mining pipeline that orchestrates extraction, aggregation, and storage. Provides aggregation queries (flows by frequency, by session spread, by success rate). |
| `session_distiller.py` | Distills a full session transcript into a playbook containing only the winning path. Strips failed attempts, retries, and dead-end explorations. |
| `recall.py` | Topic-filtered distillation. Filters sessions by query relevance, detects struggle-then-fix patterns, and builds Gemini polish prompt for optional cleanup. |

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

### `src/sio/export/` — Training Data Export (v2.1)

| Module | Purpose |
|--------|---------|
| `dataset_builder.py` | Builds JSONL and Parquet training datasets from mined data. Supports three task types: `routing` (error → tool mapping), `recovery` (failure → fix steps), and `flow` (tool sequence → outcome). |

### `src/sio/training/` — DSPy Training Pipeline (v2.1)

| Module | Purpose |
|--------|---------|
| `recall_trainer.py` | DSPy signatures and training loop for recall optimization. Supports BootstrapFewShot and GEPA optimizers. Works with Azure OpenAI and any litellm-compatible model. Reads labeled examples from `recall_examples` table. |

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
| `flow_events` | Per-occurrence flow records with timestamps, tool sequence, session ID, and success score (v2.1) |
| `recall_examples` | Labeled training examples for the distiller/recall DSPy module (v2.1) |

## Two-Tier Cost Model (v2.1)

SIO v2.1 explicitly separates operations by cost to make spending predictable:

| Tier | Cost | Operations | Engine |
|------|------|-----------|--------|
| **Cheap** | $0 | `mine`, `errors`, `patterns`, `flows`, `distill`, `recall` (without `--polish`), `export-dataset`, `collect-recall` | Regex, n-gram extraction, RLE compression, SQLite queries |
| **Expensive** | ~$0.02-0.05 per call | `suggest`, `recall --polish`, `train` | LLM via litellm (Azure gpt-5-mini, OpenAI, Anthropic, etc.) |

The cheap tier uses no LLM calls at all — only regex parsing, SQLite aggregation, and local embedding models (fastembed). This means you can run the full mining and flow discovery pipeline on any machine without an API key.

The expensive tier requires an LLM API key configured in `~/.sio/config.toml` under `[llm]`.

## Skills (Claude Code Slash Commands)

SIO bundles 10 slash commands for use inside Claude Code sessions:

| Skill | Description |
|-------|-------------|
| `/sio` | Main entry point — show status and available commands |
| `/sio-scan` | Mine recent sessions for errors |
| `/sio-suggest` | Generate improvement suggestions |
| `/sio-review` | Interactive review of pending suggestions |
| `/sio-apply` | Apply an approved suggestion |
| `/sio-status` | Show pipeline statistics |
| `/sio-flows` | Discover positive tool flows |
| `/sio-distill` | Distill a session to a playbook |
| `/sio-recall` | Topic-filtered recall with struggle detection |
| `/sio-export` | Export training datasets |

Install via: `bash scripts/install-skills.sh`

## File System Layout

```
~/.sio/
  sio.db                    # v2 database (SQLite WAL)
  config.toml               # Configuration (optional)
  suggestions.md            # Home file — pending suggestions
  changelog.md              # Applied change log
  datasets/
    routing.jsonl            # v2.1: Exported routing training data
    recovery.jsonl           # v2.1: Exported recovery training data
    flow.jsonl               # v2.1: Exported flow training data
  trained/
    *.json                   # v2.1: DSPy optimized module checkpoints
  claude-code/
    behavior_invocations.db # v1 telemetry database

~/.claude/
  settings.json             # Claude Code settings (hooks registered here)

~/.specstory/
  history/*.md              # SpecStory session transcripts (input)

~/.claude/projects/
  **/*.jsonl                # Claude JSONL transcripts (input)
```
