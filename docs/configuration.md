# Configuration

SIO reads configuration from `~/.sio/config.toml`. All settings have sensible defaults — the config file is optional.

## Config File Location

```
~/.sio/config.toml
```

If the file doesn't exist, SIO uses built-in defaults.

## All Options

Create `~/.sio/config.toml` and override any of these:

```toml
# --- Embedding ---
# Backend for computing text embeddings
# Options: "fastembed" (local, default), "api" (remote endpoint)
embedding_backend = "fastembed"

# Model name for fastembed backend
embedding_model = "sentence-transformers/all-MiniLM-L6-v2"

# Remote embedding API (only used when embedding_backend = "api")
# embedding_api_url = "https://your-endpoint/embeddings"
# embedding_api_key = "your-api-key"

# --- Clustering ---
# Cosine similarity threshold for grouping errors into patterns
# Higher = stricter (fewer, tighter clusters)
# Lower = looser (more, broader clusters)
similarity_threshold = 0.80

# Minimum number of errors before a cluster becomes a "pattern"
min_pattern_occurrences = 3

# --- Datasets ---
# Minimum positive+negative examples needed to build a dataset
min_dataset_examples = 5

# --- v1 Telemetry ---
# Data retention: purge records older than this
retention_days = 90

# Minimum labeled examples before optimization is available
min_examples = 10

# Minimum failure count before flagging a skill
min_failures = 5

# Minimum unique sessions before flagging
min_sessions = 3

# Error count threshold for pattern detection (v1)
pattern_threshold = 3

# DSPy optimizer choice
# Options: "gepa", "miprov2", "bootstrap"
optimizer = "gepa"

# --- Arena (v1) ---
# Drift detection threshold (0.0 - 1.0)
drift_threshold = 0.40

# Collision detection threshold (0.0 - 1.0)
collision_threshold = 0.85

# --- Scheduler ---
# Enable/disable daily cron job
daily_enabled = true

# Enable/disable weekly cron job
weekly_enabled = true

# Days before a pattern is considered stale
stale_days = 30
```

## Option Reference

### Embedding Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `embedding_backend` | string | `"fastembed"` | `"fastembed"` for local, `"api"` for remote |
| `embedding_model` | string | `"sentence-transformers/all-MiniLM-L6-v2"` | Model name (384 dimensions) |
| `embedding_api_url` | string | None | URL for remote embedding API |
| `embedding_api_key` | string | None | API key for remote embeddings |

### Clustering Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `similarity_threshold` | float | `0.80` | Cosine similarity cutoff for clustering |
| `min_pattern_occurrences` | int | `3` | Errors needed to form a pattern |

### Dataset Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `min_dataset_examples` | int | `5` | Minimum examples to build a dataset |

### Scheduler Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `daily_enabled` | bool | `true` | Run daily cron job |
| `weekly_enabled` | bool | `true` | Run weekly cron job |
| `stale_days` | int | `30` | Days until a pattern is stale |

### v1 Telemetry Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `retention_days` | int | `90` | Purge data older than this |
| `min_examples` | int | `10` | Labels needed for optimization |
| `min_failures` | int | `5` | Failures to flag a skill |
| `min_sessions` | int | `3` | Sessions to flag a skill |
| `pattern_threshold` | int | `3` | Error count for v1 patterns |
| `optimizer` | string | `"gepa"` | DSPy optimizer algorithm |
| `drift_threshold` | float | `0.40` | Drift detection sensitivity |
| `collision_threshold` | float | `0.85` | Collision detection sensitivity |

## File System Paths

These are not configurable via config.toml — they are hardcoded conventions:

| Path | Purpose |
|------|---------|
| `~/.sio/sio.db` | v2 database |
| `~/.sio/config.toml` | Configuration file |
| `~/.sio/suggestions.md` | Home file with pending suggestions |
| `~/.sio/changelog.md` | Applied change log |
| `~/.sio/claude-code/behavior_invocations.db` | v1 telemetry database |
| `~/.specstory/history/` | SpecStory session files (input) |
| `~/.claude/projects/` | Claude JSONL transcripts (input) |
| `~/.claude/settings.json` | Claude Code settings (hook registration) |

## Environment

SIO requires:

- **Python 3.11+**
- **crontab** available (for `sio schedule install`)
- **Write access** to `~/.sio/` directory
