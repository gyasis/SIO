# Quickstart: SIO — Self-Improving Organism

## Prerequisites

- Python 3.11+
- uv (package manager)
- Deno runtime (for RLM corpus mining sandbox): `curl -fsSL https://deno.land/install.sh | sh`
- At least one supported AI CLI platform installed

## Install

```bash
# Clone the repo
git clone <repo-url> && cd SIO

# Install SIO with default embedding backend (fastembed, ~250MB)
uv pip install -e .

# Or with OpenAI embeddings support
uv pip install -e ".[openai]"
```

## Setup for Claude Code (V0.1)

```bash
# Install SIO adapter for Claude Code
sio install --platform claude-code

# This will:
# 1. Create ~/.sio/claude-code/behavior_invocations.db
# 2. Register PostToolUse hook for telemetry
# 3. Install SIO skills (sio-feedback, sio-health, sio-optimize, sio-review)
# 4. Update ~/.claude/CLAUDE.md with SIO instructions
# 5. Run a smoke test
```

## Basic Usage

### 1. Telemetry (automatic)

Once installed, every tool call in Claude Code is automatically
recorded. No action needed — just use Claude Code normally.

### 2. Rate interactions

```
++                    # Mark last action as satisfactory
-- wrong tool used    # Mark as unsatisfactory with a note
```

### 3. Batch review

```bash
sio review                  # Review unlabeled invocations
sio review --limit 10       # Review last 10 unlabeled
```

### 4. Check health

```bash
sio health                  # Show all skill metrics
sio health --skill Read     # Show metrics for one skill
sio health --format json    # JSON output for scripting
```

### 5. Optimize a skill

```bash
# Requires 10+ labeled examples
sio optimize Read --dry-run   # Preview changes
sio optimize Read             # Generate and review optimization
```

### 6. Maintenance

```bash
sio purge --dry-run           # Preview 90-day retention purge
sio export --format csv       # Export all data for analysis
```

## Verify Installation

```bash
# Check SIO is working
sio health --platform claude-code

# Should show: empty table (no data yet) with column headers
# After a Claude Code session: should show invocation counts
```

## Configuration

SIO config lives at `~/.sio/config.toml`:

```toml
[embedding]
backend = "fastembed"                              # or "api" (external provider)
model = "all-MiniLM-L6-v2"

[embedding.api]                                    # only if backend = "api"
endpoint = "https://api.openai.com/v1/embeddings"
api_key_env = "OPENAI_API_KEY"                     # reads from env var, never stored
model = "text-embedding-3-small"
dimension = 384                                    # must match local model for compat

[retention]
days = 90

[optimization]
min_examples = 10
min_failures = 5
min_improvement = 0.05
drift_threshold = 0.40
collision_threshold = 0.85
recency_half_life_days = 30
pattern_threshold = 3                              # recurring sessions before optimization

[llm]
provider = "azure"                                 # or "openai", "anthropic", "ollama"
deployment = "gpt-5.2"                             # root LM for optimizer strategy
sub_deployment = "gpt-4.1-mini"                    # cheap sub-LM for RLM llm_query() calls

# Alternative: free local Ollama sub-LM with n-pass voting
# sub_provider = "ollama"
# sub_deployment = "llama3.3:70b"
# sub_n = 3                                        # n-pass voting for quality
```
