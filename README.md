# SIO — Self-Improving Organism

**A closed-loop system that learns from your AI coding sessions and makes your agent better over time.**

SIO watches how you use AI coding tools (Claude Code, Cursor, etc.), mines your session transcripts for error patterns, clusters them into actionable insights, generates improvement suggestions, and applies approved changes to your configuration — all with human oversight.

Your AI agent makes the same mistakes repeatedly. SIO fixes that.

---

## The Problem

AI coding assistants are powerful but repetitive in their failures:
- They read files that don't exist
- They run commands that fail the same way each time
- They ignore corrections you've given before
- They use the wrong tool for the job

These patterns are buried in session transcripts. SIO extracts them, finds the signal, and proposes targeted fixes (CLAUDE.md rules, hook configs, skill updates) that prevent recurrence.

## How It Works

```
Session Transcripts        Error Patterns           Suggestions            Config Changes
(SpecStory / JSONL)  --->  (clustered by      --->  (ranked by       --->  (CLAUDE.md rules,
                            embedding                confidence)           hooks, skills)
                            similarity)                  |
                                                  Human Review
                                                (approve / reject)

v2.1 additions:

Session Transcripts  --->  Tool Flows    --->  Distilled       --->  Training Data
(same input)               (n-gram +           Playbooks              (JSONL / Parquet)
                            RLE compress)      (winning path           |
                                                extraction)      DSPy Training
                                                    |            (BootstrapFewShot / GEPA)
                                              Recall Queries
                                              (topic filter +
                                               Gemini polish)
```

**The full pipeline:**

| Stage | What Happens | CLI Command |
|-------|-------------|-------------|
| **Mine** | Parse SpecStory `.md` and Claude `.jsonl` transcripts for tool failures, user corrections, repeated attempts, agent admissions | `sio mine --since "7 days"` |
| **Cluster** | Group similar errors using fastembed embeddings + cosine similarity | (automatic) |
| **Rank** | Score patterns by frequency x recency x session spread | `sio patterns` |
| **Dataset** | Build labeled positive/negative training datasets per pattern | `sio datasets collect` |
| **Suggest** | Generate improvement proposals via DSPy modules or template engine | `sio suggest` |
| **Review** | Human approves, rejects, or defers each suggestion | `sio suggest-review` |
| **Apply** | Write approved changes to target files with full rollback support | `sio apply <id>` |
| **Schedule** | Passive daily/weekly cron runs keep suggestions fresh | `sio schedule install` |
| **Flows** | Discover recurring positive tool sequences (n-gram + RLE, no LLM) | `sio flows` |
| **Distill** | Extract the winning path from a long session (removes failures/retries) | `sio distill --latest` |
| **Recall** | Topic-filtered distill with struggle-fix detection + optional Gemini polish | `sio recall "query"` |
| **Export** | Export JSONL/Parquet training data (routing, recovery, flow) | `sio export-dataset --task all` |
| **Collect** | Store labeled recall examples for DSPy training | `sio collect-recall "query"` |
| **Train** | DSPy BootstrapFewShot/GEPA optimization on exported datasets | `sio train --task all` |

## Quick Start

### Prerequisites

- Python 3.11+
- An AI coding tool that produces session transcripts (Claude Code, Cursor, etc.)

### Install

```bash
# Clone and install in editable mode
git clone https://github.com/gyasisutton/SIO.git
cd SIO
pip install -e ".[all,dev]"

# Install Claude Code slash commands (10 skills)
bash scripts/install-skills.sh

# Verify
sio --version
```

### First Run (5 minutes)

```bash
# 1. Install SIO hooks into Claude Code
sio install

# 2. Mine errors from your recent sessions
sio mine --since "7 days"

# 3. See what patterns were found
sio patterns

# 4. Generate improvement suggestions
sio suggest

# 5. Review and approve/reject
sio suggest-review

# 6. Apply an approved suggestion
sio apply <suggestion-id>

# 7. (Optional) Set up daily passive analysis
sio schedule install

# 8. Check overall pipeline status
sio status
```

## CLI Reference

### Core Pipeline Commands

| Command | Description |
|---------|-------------|
| `sio mine --since "3 days"` | Mine errors from recent session transcripts |
| `sio mine --source specstory` | Mine only SpecStory markdown files |
| `sio mine --source jsonl` | Mine only Claude JSONL transcripts |
| `sio patterns` | Show discovered error patterns, ranked by importance |
| `sio errors` | List individual error records with filtering |
| `sio errors --grep "FileNotFound"` | Search errors by text |

### Dataset Management

| Command | Description |
|---------|-------------|
| `sio datasets` | List all built datasets |
| `sio datasets collect` | Build datasets from mined patterns |
| `sio datasets inspect <id>` | Inspect a specific dataset |

### Suggestions & Review

| Command | Description |
|---------|-------------|
| `sio suggest` | Generate suggestions from patterns (supports `--mode auto\|hitl`) |
| `sio suggest-review` | Interactive review of pending suggestions |
| `sio approve <id>` | Approve a suggestion (with optional `--note`) |
| `sio reject <id>` | Reject a suggestion (with optional `--note`) |
| `sio apply <id>` | Apply an approved suggestion to its target file |
| `sio rollback <id>` | Revert an applied change |

### Ground Truth Management

| Command | Description |
|---------|-------------|
| `sio ground-truth seed` | Seed initial ground truth corpus |
| `sio ground-truth generate` | Generate new ground truth examples |
| `sio ground-truth review` | Review and label ground truth entries |

### Configuration & Setup

| Command | Description |
|---------|-------------|
| `sio install` | Install SIO hooks and skills into Claude Code |
| `sio config show` | Display current configuration |
| `sio config test` | Test configuration validity |
| `sio schedule install` | Install cron jobs for passive analysis |
| `sio schedule status` | Check scheduler status |
| `sio status` | Show full pipeline statistics |

### Positive Pattern Mining & Recall (v2.1)

| Command | Description |
|---------|-------------|
| `sio flows` | Discover recurring positive tool sequences (n-gram + RLE compression) |
| `sio flows --min-support 3` | Only show flows that appear in 3+ sessions |
| `sio distill --latest` | Extract winning path from the most recent session |
| `sio distill --session <id>` | Distill a specific session |
| `sio recall "cube query optimization"` | Topic-filtered distill with struggle-fix detection |
| `sio recall "query" --polish` | Same as above + Gemini polish pass (~$0.02-0.05) |

### Training Data & DSPy (v2.1)

| Command | Description |
|---------|-------------|
| `sio export-dataset --task routing` | Export routing task training data (JSONL) |
| `sio export-dataset --task recovery` | Export recovery task training data |
| `sio export-dataset --task flow` | Export flow task training data |
| `sio export-dataset --task all` | Export all tasks (JSONL + Parquet) |
| `sio collect-recall "query"` | Store labeled recall examples for DSPy training |
| `sio train --task all` | Run DSPy BootstrapFewShot/GEPA on exported datasets |
| `sio train --task recall --optimizer gepa` | Train recall model with GEPA optimizer |

### Telemetry & Maintenance

| Command | Description |
|---------|-------------|
| `sio health` | Show per-skill health metrics |
| `sio review` | Batch-review unlabeled invocations |
| `sio optimize <skill>` | Run DSPy prompt optimization |
| `sio purge --days 90` | Purge old telemetry records |
| `sio export --format json` | Export telemetry data |

## Architecture

```
src/sio/
├── cli/                          # Click CLI entry point (25+ commands)
│   └── main.py
│
├── mining/                       # Stage 1: Session transcript parsing
│   ├── specstory_parser.py       #   SpecStory markdown → structured records
│   ├── jsonl_parser.py           #   Claude JSONL transcripts → structured records (v3: +tokens, cost, cache, model)
│   ├── error_extractor.py        #   Classify errors into 5 types
│   ├── positive_extractor.py     #   v3: Detect confirmations, gratitude, implicit approval, session success
│   ├── approval_detector.py      #   v3: Tool approval/rejection rates per tool type
│   ├── sentiment_scorer.py       #   v3: Per-message sentiment (-1 to +1) + frustration escalation
│   ├── violation_detector.py     #   v3: Detect CLAUDE.md rule violations in mined errors
│   ├── facet_extractor.py        #   v3: Qualitative session facets (mastery, satisfaction, complexity)
│   ├── time_filter.py            #   Flexible date/time filtering (dateutil)
│   ├── pipeline.py               #   Orchestrates mine → store (v3: +dedup, metrics, positive signals)
│   ├── flow_extractor.py         #   Tool sequence extraction, RLE compression, success heuristics
│   ├── flow_pipeline.py          #   Flow mining pipeline + aggregation queries
│   ├── session_distiller.py      #   Session → playbook distillation (winning path extraction)
│   └── recall.py                 #   Topic filtering, struggle detection, Gemini polish prompt builder
│
├── clustering/                   # Stage 2: Pattern discovery
│   ├── pattern_clusterer.py      #   fastembed embeddings + greedy cosine clustering
│   ├── ranker.py                 #   Frequency × recency × spread scoring
│   └── grader.py                 #   v3: Pattern lifecycle grading (emerging → strong → established)
│
├── datasets/                     # Stage 3: Training data
│   ├── builder.py                #   Build pos/neg datasets per pattern
│   ├── accumulator.py            #   Auto-accumulate new errors into datasets
│   └── lineage.py                #   Track which sessions contributed
│
├── suggestions/                  # Stage 4: Improvement proposals
│   ├── generator.py              #   Template-based suggestion generation
│   ├── dspy_generator.py         #   DSPy-powered suggestion generation
│   ├── confidence.py             #   Score suggestion quality (v3: +temporal decay)
│   ├── refiner.py                #   Second-pass refinement for specificity
│   └── home_file.py              #   Write ~/.sio/suggestions.md summary
│
├── review/                       # Stage 5: Human-in-the-loop
│   ├── reviewer.py               #   Approve / reject / defer
│   └── tagger.py                 #   AI + human tagging
│
├── applier/                      # Stage 6: Change application
│   ├── writer.py                 #   Apply changes to config files (v3: +delta merge, budget check)
│   ├── budget.py                 #   v3: Instruction budget enforcement + auto-consolidation
│   ├── deduplicator.py           #   v3: Semantic rule deduplication across files
│   ├── rollback.py               #   Revert applied changes (with safety checks)
│   └── changelog.py              #   Log all changes
│
├── scheduler/                    # Stage 7: Passive automation
│   ├── runner.py                 #   Orchestrate full pipeline
│   └── cron.py                   #   Install/manage cron jobs
│
├── ground_truth/                 # Training corpus management
│   ├── corpus.py                 #   Ground truth corpus operations
│   ├── seeder.py                 #   Seed initial examples
│   ├── generator.py              #   Generate new examples
│   └── reviewer.py               #   Review/label ground truth
│
├── core/                         # Shared infrastructure
│   ├── db/                       #   SQLite schema, queries, retention (WAL mode, FK enforced)
│   ├── config.py                 #   TOML configuration loader
│   ├── embeddings/               #   fastembed local + API backends with caching
│   ├── dspy/                     #   DSPy modules, signatures, optimizer, LM factory
│   ├── metrics/                  #   v3: Learning velocity tracking
│   │   └── velocity.py           #     Rolling-window error rate, decay rate, adaptation speed
│   ├── arena/                    #   Drift detection, regression testing, collision checks
│   │   ├── assertions.py         #     v3: Binary pass/fail gates for rule validation
│   │   ├── anomaly.py            #     v3: MAD-based anomalous session detection
│   │   ├── txlog.py              #     v3: Append-only transaction log for autoresearch
│   │   ├── experiment.py         #     v3: Git worktree-based rule experiments
│   │   └── autoresearch.py       #     v3: Autonomous mine→grade→experiment→validate loop
│   ├── telemetry/                #   PostToolUse hook capture, secret scrubbing
│   ├── feedback/                 #   Batch review, labeling, pattern flagging
│   └── health/                   #   Per-skill health aggregation
│
├── export/                       # v2.1: Training data export
│   └── dataset_builder.py       #   JSONL/Parquet dataset builders (routing, recovery, flow)
│
├── training/                    # v2.1: DSPy training pipeline
│   └── recall_trainer.py        #   DSPy signatures, training loop, Azure OpenAI support
│
├── reports/                      # v3: Visual reporting
│   └── html_report.py            #   Standalone HTML report with Chart.js
│
└── adapters/
    └── claude_code/              # Claude Code integration
        ├── installer.py          #   One-command hook + skill setup (v3: 4 hooks)
        ├── hooks/                #   v3: PostToolUse, PreCompact, Stop, UserPromptSubmit
        └── skills/               #   10 bundled slash commands
            ├── sio/              #     Main entry point
            ├── sio-scan/         #     Mine recent sessions
            ├── sio-suggest/      #     Generate suggestions
            ├── sio-review/       #     Interactive review
            ├── sio-apply/        #     Apply a suggestion
            ├── sio-status/       #     Pipeline status
            ├── sio-flows/        #     Discover positive tool flows
            ├── sio-distill/      #     Distill session to playbook
            ├── sio-recall/       #     Topic-filtered recall
            └── sio-export/       #     Export training data
```

## Design Philosophy: SIO Produces, Agent Harness Consumes

SIO is the **data and intelligence layer** — it does not enforce behavior at runtime. Instead, it generates artifacts (rules, skills, metrics) that the agent harness (Claude Code) consumes:

```
          SIO (Analysis Engine)                    Agent Harness (Claude Code)
┌─────────────────────────────────┐      ┌──────────────────────────────────┐
│ Mine sessions for errors +      │      │ Reads CLAUDE.md rules            │
│   positive signals              │      │ Loads ~/.claude/skills/          │
│ Cluster patterns by embedding   │ ───> │ Fires hooks (PostToolUse, etc.)  │
│ Grade lifecycle (emerging →     │      │ Agent follows learned rules      │
│   strong → established)         │      │ Fewer errors next session        │
│ DSPy-optimize suggestions       │      │                                  │
│ Track velocity (did it work?)   │ <─── │ Session transcripts (JSONL/MD)   │
│ Auto-experiment with rollback   │      │                                  │
└─────────────────────────────────┘      └──────────────────────────────────┘
```

**SIO teaches; the agent harness learns.** SIO never blocks or intercepts the agent at runtime. It writes better instructions, and the agent follows them next session. Velocity tracking closes the loop by measuring whether the rules actually reduced errors.

This separation means SIO works with any agent harness that reads configuration files — Claude Code today, Cursor/Gemini CLI/others tomorrow.

---

## Competitive Enhancement (v3.0)

v3.0 imports the best features from 10+ self-improving agent tools (claude-reflect, GuideMode, AutoResearch Loop, /insights, Claude Diary, and more):

| Feature | What It Does | CLI Command |
|---------|-------------|-------------|
| **Enhanced Extraction** | Extracts tokens, costs, cache efficiency, sub-agent data from JSONL (>90% field coverage, up from ~35%) | `sio mine` |
| **Positive Signals** | Captures confirmations, gratitude, implicit approvals — not just errors | `sio mine` |
| **Sentiment Scoring** | Per-message sentiment (-1 to +1) with frustration escalation detection | `sio mine` |
| **Learning Velocity** | Measures how fast error rates decrease after rules are applied | `sio velocity` |
| **Confidence Decay** | Patterns not seen in 14+ days lose confidence (floor 0.3) | automatic |
| **Pattern Grading** | Lifecycle: emerging → strong → established → declining | automatic |
| **Instruction Budget** | Line caps on CLAUDE.md/rules files with auto-consolidation | `sio budget` |
| **Rule Deduplication** | Semantic similarity detection (>85%) with merge proposals | `sio dedupe` |
| **Delta Writing** | Merge similar rules in-place instead of always appending | `sio apply` |
| **Violation Detection** | Detects when the agent ignores rules already in CLAUDE.md | `sio violations` |
| **Lifecycle Hooks** | PreCompact, Stop, UserPromptSubmit capture data in real-time | `sio install` |
| **Binary Assertions** | Pass/fail gates: error_rate_decreased, no_regressions, etc. | arena |
| **Git Experiments** | Test rules on isolated worktree branches before promoting | `sio apply --experiment` |
| **Anomaly Detection** | MAD-based flagging of unusual sessions (>3 deviations) | arena |
| **AutoResearch Loop** | Autonomous mine→cluster→grade→generate→experiment→validate cycle | `sio autoresearch start` |
| **HTML Report** | Standalone visual report with charts, patterns, copy-ready suggestions | `sio report --html` |

### New CLI Commands (v3.0)

| Command | Description |
|---------|-------------|
| `sio velocity` | Show learning velocity trends (error rate changes after rules applied) |
| `sio budget` | Show per-file instruction budget usage (lines / cap / status) |
| `sio dedupe` | Find and consolidate semantically duplicate rules |
| `sio violations` | Show CLAUDE.md rule violations (agent ignored its own rules) |
| `sio autoresearch start` | Start autonomous optimization loop (human gate on promotion) |
| `sio autoresearch stop` | Stop the autonomous loop |
| `sio autoresearch status` | Show cycle count, active experiments, promotions |
| `sio report --html` | Generate standalone HTML report with charts and copy-ready suggestions |

### New Database Tables (v3.0)

| Table | Purpose |
|-------|---------|
| `processed_sessions` | Dedup tracking — prevents re-mining same session file |
| `session_metrics` | Per-session aggregates: tokens, cost, cache, errors, positive signals |
| `positive_records` | Detected positive user signals (confirmation, gratitude, approval) |
| `velocity_snapshots` | Rolling-window error rate measurements for velocity tracking |
| `autoresearch_txlog` | Append-only transaction log for autonomous loop actions |

---

## Error Types

SIO classifies errors into four categories:

| Type | What It Catches | Example |
|------|----------------|---------|
| **tool_failure** | Tool calls that return errors | `FileNotFoundError`, `PermissionError`, command timeouts |
| **user_correction** | User telling the agent it did the wrong thing | "That's not what I asked", "Wrong file" |
| **repeated_attempt** | Same tool called 3+ times with similar input | Agent retrying a failing command |
| **agent_admission** | Agent acknowledging its own mistake | "I should have read the file first" |

## Suggestion Targets

Generated suggestions can target multiple configuration surfaces:

| Target | File | What Changes |
|--------|------|-------------|
| `claude_md_rule` | `CLAUDE.md` | Add behavioral rules the agent follows |
| `hook_config` | `.claude/settings.json` | Modify hook behavior |
| `skill_update` | `skills/*/SKILL.md` | Update skill instructions |
| `mcp_config` | MCP server config | Adjust MCP tool settings |
| `settings_config` | `.claude/settings.json` | Modify Claude Code settings |
| `project_config` | Project files | Update project-level config |

## DSPy Integration

SIO optionally uses [DSPy](https://github.com/stanfordnlp/dspy) for smarter suggestion generation:

- **Modules**: `SuggestionModule` generates structured suggestions from error patterns
- **Signatures**: Typed input/output schemas for LLM calls
- **Optimization**: Bootstrap few-shot optimization with ground truth corpus
- **Fallback**: Template engine works without DSPy or any LLM API key

Configure via `~/.sio/config.toml`:

```toml
[llm]
provider = "openai"           # or "anthropic"
model = "gpt-4o-mini"
max_tokens = 2000

[dspy]
enabled = true
optimizer = "bootstrap"
```

## Data Storage

All data is stored locally in SQLite (WAL mode, FK enforced):

```
~/.sio/
├── sio.db                    # Main database (error_records, patterns, suggestions, etc.)
├── config.toml               # User configuration
├── datasets/                 # Built training datasets (JSON)
├── ground_truth/             # Ground truth corpus
├── optimized/                # DSPy optimized modules
└── suggestions.md            # Human-readable suggestion summary
```

**Key tables**: `error_records`, `patterns`, `pattern_errors`, `datasets`, `suggestions`, `applied_changes`, `ground_truth`, `optimized_modules`, `behavior_invocations`, `flow_events`, `recall_examples`, `processed_sessions`, `session_metrics`, `positive_records`, `velocity_snapshots`, `autoresearch_txlog`

## Two-Tier Cost Model (v2.1)

SIO v2.1 separates commands into two cost tiers:

| Tier | Cost | Commands | Engine |
|------|------|----------|--------|
| **Cheap** | $0 | `mine`, `errors`, `flows`, `distill`, `recall` (no `--polish`), `export-dataset` | Regex + SQLite only |
| **Expensive** | ~$0.02-0.05 | `suggest`, `recall --polish`, `train` | LLM (Azure gpt-5-mini or any litellm model) |

## DSPy Training Pipeline (v2.1)

The full training loop from raw sessions to optimized models:

```
sio mine → sio flows → sio export-dataset    (collect data — $0)
sio collect-recall "query"                     (label examples — $0)
gemini polish → save polished runbook          (clean the data — ~$0.02)
sio train --task all                           (train DSPy modules — ~$0.05)
sio recall "query"                             (uses trained model — $0)
```

## Security

- **Path validation**: All file writes are restricted to `~/.sio/`, `~/.claude/`, and the current working directory. Arbitrary path traversal is blocked.
- **Secret scrubbing**: API keys (OpenAI, Anthropic, GitHub, AWS), SSH/PEM keys, JWTs, and bearer tokens are scrubbed before any LLM processing.
- **Rollback safety**: Applied changes check for manual edits before overwriting. Use `--force` to override.
- **No data exfiltration**: All processing is local. LLM calls (when DSPy is enabled) only send scrubbed error summaries, never raw session content.

## Development

### Setup

```bash
git clone https://github.com/gyasisutton/SIO.git
cd SIO
pip install -e ".[all,dev]"
bash scripts/install-skills.sh   # Install 10 Claude Code slash commands
```

### Tests

```bash
# Run the full suite (1360+ tests)
pytest

# Run with coverage
pytest --cov=sio

# Run specific test categories
pytest tests/unit/              # Fast unit tests
pytest tests/integration/       # Integration tests (slower, uses embeddings)

# Lint
ruff check src/ tests/
```

### Test Coverage

| Module | Tests | Coverage |
|--------|-------|---------|
| Mining (parsers, extractors, pipeline) | 180+ | Core parsing, error classification, time filtering |
| Clustering (embeddings, ranker) | 90+ | Cosine similarity, pattern ranking, edge cases |
| Datasets (builder, accumulator, lineage) | 60+ | Positive/negative generation, lineage tracking |
| Suggestions (generator, DSPy, confidence) | 120+ | Template + DSPy generation, confidence scoring |
| Review (approve, reject, defer) | 40+ | State transitions, persistence |
| Applier (writer, rollback) | 50+ | Path validation, safety checks, rollback |
| Core (DB, config, embeddings, arena) | 200+ | Schema, queries, FK enforcement, drift detection |
| CLI (commands, flags, output) | 150+ | All 25+ commands with Click test runner |
| Integration (full pipeline) | 50+ | End-to-end mine → suggest → apply |

## Configuration

Create `~/.sio/config.toml`:

```toml
# Mining
[mining]
source_dirs = ["~/.claude/projects"]   # Where to find session transcripts
since = "7 days"                        # Default look-back window
source_type = "both"                    # "specstory", "jsonl", or "both"

# Clustering
[clustering]
similarity_threshold = 0.70             # Cosine similarity for grouping
min_cluster_size = 2                    # Minimum errors per pattern

# Suggestions
[suggestions]
auto_approve_threshold = 0.95           # Auto-approve above this confidence
mode = "hitl"                           # "hitl" (human review) or "auto"

# LLM (optional, for DSPy-powered suggestions)
[llm]
provider = "openai"
model = "gpt-4o-mini"
max_tokens = 2000

# Scheduling
[schedule]
daily = true
weekly_report = true
```

## Roadmap

- [ ] Multi-platform support (Cursor, Windsurf, Copilot)
- [ ] Web dashboard for suggestion review
- [ ] Team-level pattern sharing
- [ ] Custom embedding model fine-tuning
- [ ] VS Code extension

## License

[MIT](LICENSE)

## Author

Gyasi Sutton — [@gyasisutton](https://github.com/gyasisutton)
