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
| `~/.sio/<platform>/behavior_invocations.db` | Per-platform hook telemetry (e.g. `~/.sio/claude-code/behavior_invocations.db`) |
| `~/.specstory/history/` | SpecStory session files (input) |
| `~/.claude/projects/` | Claude JSONL transcripts (input) |
| `~/.claude/settings.json` | Claude Code settings (hook registration) |

## Environment

SIO requires:

- **Python 3.11+**
- **crontab** available (for `sio schedule install`)
- **Write access** to `~/.sio/` directory

## Environment Variables

All `SIO_*` variables are optional overrides. None are required for basic use — the defaults shown here are what SIO uses when the variable is unset.

### Paths

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `SIO_HOME` | path | `~/.sio` | Root data directory. Controls where SIO looks for `config.toml`, `sio.db`, subdirectories, and hook state. Useful for isolated test environments or alternate installs. |
| `SIO_DB_PATH` | path | `~/.sio/sio.db` | Full path to the canonical SQLite database. Overrides the default location for all read/write operations. |
| `SIO_PLATFORM_DB_PATH` | path | `~/.sio/<platform>/behavior_invocations.db` | Full path to the per-platform hook telemetry DB (Claude Code only). Defaults to `~/.sio/claude-code/behavior_invocations.db` for the claude-code harness. Used mainly in tests. |

### Model Selection

SIO uses two LM roles: a **task LM** for forward passes (cheap, fast, cached) and a **reflection LM** for GEPA critique passes (stronger, uncached).

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `SIO_TASK_LM` | string | `gemini/gemini-flash-latest` | DSPy model string for the task LM (`get_task_lm()`). Resolution order: this env var → `[llm.task]` in config.toml → hard default. Example: `openai/gpt-4o-mini`. |
| `SIO_REFLECTION_LM` | string | `gemini/gemini-pro-latest` | DSPy model string for the GEPA reflection LM (`get_reflection_lm()`). Resolution order: this env var → `[llm.reflection]` in config.toml → hard default. Never defaults to gpt-5; must be opt-in. |
| `SIO_FORCE_ADAPTER` | string | _(auto-detected)_ | Force DSPy adapter type: `"json"` or `"chat"`. Overrides provider-based auto-detection. Mainly for debugging adapter issues. |
| `SIO_FORCE_NATIVE_FC` | string | _(auto-detected)_ | Force native function-calling flag: `"0"` to disable, `"1"` to enable. Overrides the adapter's default for the detected provider. |
| `SIO_NO_JSON_SHIM` | string | _(off)_ | Set to `"1"` to disable the json.dumps compatibility shim for DSPy + litellm Pydantic response objects. Only needed if the shim causes conflicts with other libraries. |
| `OPENAI_API_KEY` | string | _(none)_ | Standard OpenAI API key. Used by `create_lm()` legacy path (triggers `openai/gpt-4o-mini`) and by `sio suggest --refiner openai`. |
| `ANTHROPIC_API_KEY` | string | _(none)_ | Standard Anthropic API key. Used by `create_lm()` legacy path (triggers `anthropic/claude-sonnet-4-20250514`) and by `sio suggest --refiner anthropic`. |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | string | _(none)_ | Google Gemini API key. Used by `sio suggest --refiner gemini` and as a fallback by `SIO_GEMINI_API_KEY`. |
| `SIO_GEMINI_API_KEY` | string | _(falls back to `GEMINI_API_KEY`)_ | SIO-specific Gemini key. Used by `sio amplify` and the classifier. Takes precedence over `GEMINI_API_KEY`; falls back to it if unset. |

### Optimizer and Cost

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `SIO_GEPA_BUDGET` | string | `"light"` | GEPA optimization budget tier: `"light"` (~$5–8, 30–60 min), `"medium"` (~$15–25, 90–150 min), `"heavy"` (~$40–80, 3–5 hours). Set before `sio optimize --optimizer gepa`. |
| `SIO_GEPA_THREADS` | int | `8` | Number of parallel threads for GEPA evaluation. Higher values are faster but use more API quota. |
| `SIO_BUDGET_OVERRIDE` | float | `0` | Override the rolling 24 h spend cap (USD). When set to a float > 0, it replaces the `[budget].rolling_24h_usd` config value for the current run. Equivalent to `--budget-override`. Example: `SIO_BUDGET_OVERRIDE=20` allows up to $20 spend. |

### Flow Confidence Thresholds

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `SIO_FLOW_CONFIDENCE_HIGH` | string | `"20,40.0"` | Comma-separated `count,rate` threshold for HIGH-confidence flow classification. A flow needs at least `count` observations and at least `rate`% success rate. |
| `SIO_FLOW_CONFIDENCE_MEDIUM` | string | `"10,20.0"` | Same format as above for MEDIUM-confidence flows. Everything below this falls into LOW. |

### Background / Hooks

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `SIO_BACKGROUND_MODE` | string | _(off)_ | Set to `"1"` when running from cron, systemd, or any non-interactive caller. Suppresses ANSI codes and interactive prompts, and enables implicit `--yes` on all gates. Cron installs set this automatically via the job command. |
| `SIO_APPLY_EXTRA_ROOTS` | string | _(empty)_ | Colon-separated list of additional root directories that `sio apply` is allowed to write to. By default only `~/.claude/` is in the allowlist. Example: `SIO_APPLY_EXTRA_ROOTS=/home/user/.cursor:/home/user/.windsurf`. |

### Quick-start example

```bash
# Use a specific Gemini model for task LM, light GEPA budget
export SIO_TASK_LM=gemini/gemini-flash-latest
export SIO_GEPA_BUDGET=light
export GEMINI_API_KEY=your-key-here
sio optimize --optimizer gepa

# Run from cron (non-interactive, suppress prompts)
SIO_BACKGROUND_MODE=1 sio schedule run --mode daily

# Redirect all data to a test directory
SIO_HOME=/tmp/sio-test SIO_DB_PATH=/tmp/sio-test/sio.db sio status
```
