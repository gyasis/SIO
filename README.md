# SIO — Self-Improving Organism

A closed-loop system that mines your AI coding sessions for error patterns, clusters them, builds datasets, generates improvement suggestions, and applies approved changes — all passively.

SIO watches how you use AI coding tools (Claude Code, etc.), learns from your mistakes and friction points, and proposes config/rule changes that prevent them from recurring.

## How It Works

```
Session Files          Error Patterns         Suggestions           Config Changes
(SpecStory/JSONL) ---> (clustered by    ---> (ranked by       ---> (CLAUDE.md rules,
                        similarity)          confidence)           hooks, skills)
                                                  |
                                           Human Review
                                         (approve/reject)
```

**The pipeline:**

1. **Mine** — Parse SpecStory markdown and Claude JSONL transcripts for tool failures, user corrections, repeated attempts, and undos
2. **Cluster** — Group similar errors using embedding similarity (fastembed, cosine distance)
3. **Dataset** — Build labeled pos/neg datasets from each pattern
4. **Suggest** — Generate improvement proposals with confidence scores
5. **Review** — Human approves/rejects via interactive CLI or home file
6. **Apply** — Write approved changes to config files with full rollback support
7. **Schedule** — Passive daily/weekly cron runs keep suggestions fresh

## Quick Install

```bash
pip install -e ".[dev]"
```

## Quick Start (5 minutes)

```bash
# 1. Mine errors from your recent sessions
sio mine --since "7 days"

# 2. See what patterns were found
sio patterns

# 3. Review and approve suggestions
sio suggest-review

# 4. (Optional) Set up daily passive analysis
sio schedule install

# 5. Check overall status
sio status
```

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, first run, verification |
| [User Guide](docs/user-guide.md) | Complete CLI reference with examples |
| [Architecture](docs/architecture.md) | System design, module map, data flow |
| [Cookbook](docs/cookbook.md) | Recipes for common workflows |
| [Configuration](docs/configuration.md) | All config options explained |
| [v1 Guide](specs/001-self-improving-organism/quickstart.md) | v1 telemetry/optimization features |
| [v2 Guide](specs/002-sio-redesign/quickstart.md) | v2 mining/suggestion features |

## CLI Commands

### v2 — Session Mining & Suggestions
| Command | Description |
|---------|-------------|
| `sio mine --since "3 days"` | Mine errors from recent sessions |
| `sio patterns` | Show discovered error patterns ranked |
| `sio datasets` | List built datasets |
| `sio datasets collect` | Collect dataset from criteria |
| `sio suggest-review` | Interactive suggestion review |
| `sio approve <id>` | Approve a suggestion |
| `sio reject <id>` | Reject a suggestion |
| `sio rollback <id>` | Rollback an applied change |
| `sio schedule install` | Install daily/weekly cron jobs |
| `sio schedule status` | Check scheduler status |
| `sio status` | Show pipeline stats |

### v1 — Telemetry & Optimization
| Command | Description |
|---------|-------------|
| `sio install` | Install SIO hooks for Claude Code |
| `sio health` | Show per-skill health metrics |
| `sio review` | Batch-review unlabeled invocations |
| `sio optimize <skill>` | Run DSPy prompt optimization |
| `sio purge` | Purge old telemetry records |
| `sio export` | Export telemetry data |

## Project Structure

```
src/sio/
  cli/main.py              # Click CLI entry point
  mining/                   # Session file parsers + error extraction
    specstory_parser.py     # SpecStory markdown parser
    jsonl_parser.py         # Claude JSONL transcript parser
    error_extractor.py      # Classify errors (4 types)
    time_filter.py          # Flexible date filtering (dateutil)
    pipeline.py             # Orchestrates mine → store
  clustering/               # Error pattern discovery
    pattern_clusterer.py    # fastembed + greedy clustering
    ranker.py               # Frequency * recency scoring
  datasets/                 # Training data management
    builder.py              # Build pos/neg datasets per pattern
    accumulator.py          # Auto-accumulate errors into datasets
    lineage.py              # Track contributing sessions
  suggestions/              # Improvement proposals
    generator.py            # Generate suggestions from patterns
    confidence.py           # Score suggestion quality
    home_file.py            # Write ~/.sio/suggestions.md
  review/                   # Human-in-the-loop
    reviewer.py             # Approve/reject/defer
    tagger.py               # AI + human tagging
  applier/                  # Change application
    writer.py               # Apply changes to config files
    rollback.py             # Revert applied changes
    changelog.py            # Log all changes
  scheduler/                # Passive automation
    runner.py               # Orchestrate full pipeline
    cron.py                 # Install/manage cron jobs
  core/                     # Shared infrastructure (v1)
    db/                     # SQLite schema, queries, retention
    config.py               # Configuration management
    embeddings/             # fastembed + API backends
    arena/                  # Drift/collision detection
    telemetry/              # v1 hook-based capture
    feedback/               # v1 labeling system
    health/                 # v1 health aggregator
    dspy/                   # v1 DSPy optimization
  adapters/claude_code/     # Claude Code integration
    hooks/                  # PostToolUse hook
    skills/                 # sio-feedback, sio-health, etc.
    installer.py            # One-command setup
```

## Requirements

- Python 3.11+
- Dependencies: click, rich, fastembed, numpy, python-dateutil, dspy

## Tests

```bash
pytest                    # 756 tests
ruff check src/ tests/    # Linting
```

## License

MIT
