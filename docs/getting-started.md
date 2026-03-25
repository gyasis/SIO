# Getting Started

## Prerequisites

- Python 3.11 or newer
- pip (or uv)
- Claude Code (or any AI coding CLI that produces SpecStory/JSONL transcripts)

## Installation

```bash
# Clone the repo
git clone https://github.com/yourusername/SIO.git
cd SIO

# Install in editable mode with all dependencies
pip install -e ".[all,dev]"

# Install Claude Code slash commands (10 skills)
bash scripts/install-skills.sh

# Verify installation
sio --version
```

The `[all]` extra includes DSPy, Parquet, and Gemini polish dependencies. If you only need the core error mining pipeline, `pip install -e ".[dev]"` is sufficient.

## First Run

### 1. Mine your recent sessions

SIO looks for session files in two default locations:

| Source | Default Path |
|--------|-------------|
| SpecStory | `~/.specstory/history/` |
| Claude JSONL | `~/.claude/projects/` |

Run your first mining pass:

```bash
sio mine --since "7 days"
```

Expected output:

```
Scanned 42 files
Found 8 errors
```

### 2. View discovered patterns

```bash
sio patterns
```

This clusters similar errors and ranks them by frequency and recency. You'll see a table like:

```
# Pattern                                              Errors Sessions Last Seen  Score
1 Edit tool path not found                                  5        3 2026-02-24  0.85
2 Bash command permission denied                            3        2 2026-02-23  0.62
```

### 3. Review suggestions

```bash
sio suggest-review
```

SIO presents each suggestion with a confidence score and proposed change. You choose:
- **a** — approve (queued for application)
- **r** — reject (dismissed)
- **d** — defer (revisit later)
- **q** — quit review

### 4. Check status

```bash
sio status
```

Shows a summary of your pipeline:

```
SIO v2 Status
------------------------------
Errors mined:      8
Patterns found:    3
Datasets built:    2
Pending reviews:   1
Applied changes:   0
```

## Setting Up Passive Analysis

Install cron jobs that run the pipeline automatically:

```bash
sio schedule install
```

This creates two cron entries:
- **Daily** at midnight — mine last 24 hours, cluster, suggest
- **Weekly** on Sunday — full 7-day analysis with dataset building

Check schedule status:

```bash
sio schedule status
```

### 5. Discover positive tool flows (v2.1)

```bash
sio flows
```

This finds recurring tool sequences that led to successful outcomes — patterns worth repeating.

### 6. Distill a session to a playbook (v2.1)

```bash
sio distill --latest
```

Extracts the winning path from your most recent session, stripping out failed attempts and dead ends.

### 7. Topic-filtered recall (v2.1)

```bash
sio recall "dbt model debugging"
```

Searches your session history for a specific topic, detects struggle-then-fix patterns, and returns a focused playbook. Add `--polish` for a Gemini-cleaned version (~$0.02).

### 8. Quick verification of v2.1 features

```bash
# Verify flows work
sio flows --min-support 1

# Verify distill works
sio distill --latest

# Verify export works
sio export-dataset --task all --dry-run
```

## What Happens Next

SIO runs passively in the background:

1. Daily cron mines yesterday's errors
2. Patterns accumulate over time
3. When a pattern reaches enough occurrences, SIO generates a suggestion
4. Suggestions appear in `~/.sio/suggestions.md` (the home file)
5. You review and approve/reject at your convenience
6. Approved changes are applied to your CLAUDE.md, hooks, or skills
7. If something goes wrong, `sio rollback <id>` reverts the change

## Verification

Run the test suite to verify everything is working:

```bash
pytest
```

All 756 tests should pass.

## Setup on a New Machine (Quick Reference)

```bash
git clone <repo>
cd SIO
pip install -e ".[all,dev]"
bash scripts/install-skills.sh
sio mine --since "7 days"
```

This gives you the full pipeline: error mining, flow discovery, session distillation, recall, dataset export, and DSPy training.

## Next Steps

- [User Guide](user-guide.md) — Full CLI reference
- [Cookbook](cookbook.md) — Recipes for common workflows
- [Configuration](configuration.md) — Customize thresholds and behavior
- [Architecture](architecture.md) — Understand the system design
