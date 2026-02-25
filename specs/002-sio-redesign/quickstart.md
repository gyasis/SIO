# Quickstart: SIO v2

## Prerequisites

- Python 3.11+
- Claude Code with SpecStory enabled (sessions at `~/.specstory/history/`)
- Git (for change commits)
- cron (for passive scheduling, optional)

## Install

```bash
cd /path/to/SIO
pip install -e ".[dev]"
```

## Basic Usage

### 1. Mine errors from recent sessions

```bash
# Mine last 3 days of sessions
sio mine --since "3 days"

# Mine last week, specific project
sio mine --since "1 week" --project SIO

# Mine last month
sio mine --since "30 days"
```

### 2. View discovered patterns

```bash
sio patterns
```

Output:
```
Pattern #1: Read tool fails on non-existent files (12 occurrences, 5 sessions)
Pattern #2: Bash timeout on long-running commands (8 occurrences, 3 sessions)
```

### 3. Review and approve suggestions

```bash
# Interactive review
sio review

# Approve a specific suggestion
sio approve 1

# Reject a suggestion
sio reject 2
```

### 4. Set up passive analysis (optional)

```bash
# Install daily + weekly cron jobs
sio schedule install

# Check scheduler status
sio schedule status
```

After installation, `~/.sio/suggestions.md` will be updated automatically.

### 5. Rollback a change

```bash
sio rollback <change_id>
```

### 6. Check overall status

```bash
sio status
```

## Configuration

Edit `~/.sio/config.toml`:

```toml
# Clustering
similarity_threshold = 0.80
min_pattern_occurrences = 3

# Datasets
min_examples = 5

# Scheduling
daily_enabled = true
weekly_enabled = true

# Embedding
embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
```

## Verification

After install, verify the pipeline works:

```bash
# Mine recent data
sio mine --since "7 days"

# Should show patterns if you have errors in recent sessions
sio patterns

# Check status
sio status
```
