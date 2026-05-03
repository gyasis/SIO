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
Per-Platform DB                    Canonical DB                    DSPy Training Layer
~/.sio/<platform>/          sync   ~/.sio/sio.db               optimizer selection
behavior_invocations.db  -------> (error_records, patterns, -----> GEPA (default)
 (hook writes here)                processed_sessions,              MIPROv2
                                   gold_standards,                  BootstrapFewShot
                                   optimized_modules)                    |
                                          |                        Optimized Module
                                          v                        (.json artifact)
Session Transcripts        Error Patterns           Suggestions            Config Changes
(SpecStory / JSONL)  --->  (clustered by      --->  (ranked by       --->  (CLAUDE.md rules,
                            fastembed +              confidence +          hooks, skills)
                            cosine sim)              DSPy Assert)               |
                                  |                       |             Atomic write +
                            Dedup: within-         Human Review         Backup (keep=10)
                            type only              (approve/reject)
                            (FR-020)

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

### Pipeline Integrity (v004 — 2026-04)

The v004 remediation hardened every stage of the pipeline:

- **Per-platform DB sync**: Hook writes land in `~/.sio/<platform>/behavior_invocations.db`; `sio sync` idempotently copies to canonical `~/.sio/sio.db` using `INSERT OR IGNORE` dedup on `(platform, session_id, timestamp, tool_name)`.
- **DSPy-first optimization**: Three optimizers wired (`GEPA` default, `MIPROv2`, `BootstrapFewShot`). Run via `sio optimize --optimizer gepa`.
- **File-size guard**: Files > 1 GB skipped with WARNING in `_file_hash` (FR-027).
- **Within-type dedup**: `_dedup_by_error_type_priority` now groups by `(session_id, user_message, error_type)` so `tool_failure` and `user_correction` rows are preserved side-by-side (FR-020).
- **Atomic writes**: All config-file writes use `os.replace` + timestamped backup, keep-last-10 retention.

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
- An AI coding tool that produces session transcripts. Claude Code is supported
  out of the box; Cursor / Windsurf / OpenCode adapters are stubs (PRs welcome).

### Install

The package name on pypi is `self-improving-organism`; the CLI binary stays
`sio` for ergonomics. (Pypi publish hasn't happened yet — install from the
GitHub release for now.)

```bash
# Recommended: pin to a release tag (latest = v0.1.1)
pip install git+https://github.com/gyasisutton/SIO.git@v0.1.1

# Or grab the wheel directly from the release page (no build step)
pip install https://github.com/gyasisutton/SIO/releases/download/v0.1.1/self_improving_organism-0.1.1-py3-none-any.whl

# Or from source (editable, for tinkering)
git clone https://github.com/gyasisutton/SIO.git
cd SIO
pip install -e ".[all,dev]"

# Verify
sio --version          # → 0.1.1
```

### Bootstrap your harness (`sio init`)

> **You must run `sio init` after `pip install`.** `pip install` only puts the
> Python package in place; it does not touch `~/.claude/` or `~/.sio/`.
> `sio init` is the step that actually wires SIO into your agent.

`sio init` does two things:

1. **Creates `~/.sio/`** with subdirs (`datasets/`, `previews/`, `backups/`,
   `ground_truth/`, `optimized/`) and seeds `~/.sio/config.toml` from a
   template if it doesn't already exist. Existing config.toml is **never**
   overwritten — even on subsequent runs.
2. **Stages SIO's bundled skills and tool rules** into your AI agent's
   config directory. For Claude Code that's `~/.claude/skills/sio-*/` and
   `~/.claude/rules/tools/sio.md`. Idempotent, manifest-tracked, preserves
   anything you've edited.

```bash
# Auto-detect harness, install (creates ~/.sio/ + ~/.claude/skills/sio-*/)
sio init

# Preview without writing
sio init --dry-run

# See what's currently installed and whether anything has drifted
sio init --status

# Force re-sync (overwrite user-edited files; originals get backed up
# to ~/.sio/backups/<timestamp>/)
sio init --force

# Cleanly remove SIO-managed files (user-modified files are left in place)
sio init --uninstall

# Force a specific harness even if the auto-detect missed it
sio init --harness claude-code
```

After `sio init`, your tree should look like:

```
~/.sio/
  config.toml          ← uncomment one [llm] provider before `sio suggest`
  datasets/
  previews/
  backups/
  ground_truth/
  optimized/
~/.claude/
  skills/sio-*/        ← 19 SIO skill folders
  rules/tools/sio.md   ← canonical SIO usage rule
  .sio-managed.json    ← manifest tracking what SIO installed
```

### Upgrade

```bash
pip install --upgrade git+https://github.com/gyasisutton/SIO.git@v0.1.1
sio init                    # safe — never clobbers user-edited files
sio init --status           # confirm what shipped vs what's drifted
```

### First run (5 minutes)

```bash
# 1. Stage SIO skills + rules into your harness
sio init

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

### LLM configuration

SIO's DSPy suggestion engine needs an LLM. `sio init` already created
`~/.sio/config.toml` for you — open it and **uncomment one** `[llm]`
provider block, then set the matching API key in your shell environment.

The shipped template:

```toml
# SIO configuration — ~/.sio/config.toml
#
# Quick start: uncomment ONE of the [llm] provider blocks below and set
# the matching API key in your shell environment. `sio suggest` will fail
# loudly until a provider is configured.

[llm]

# --- OpenAI ---
# model = "openai/gpt-4o"
# api_key_env = "OPENAI_API_KEY"

# --- Anthropic ---
# model = "anthropic/claude-sonnet-4-20250514"
# api_key_env = "ANTHROPIC_API_KEY"

# --- Azure OpenAI ---
# model = "azure/<deployment-name>"
# api_key_env = "AZURE_OPENAI_API_KEY"
# api_base_env = "AZURE_OPENAI_ENDPOINT"

# --- Local Ollama (free, private; needs `ollama` running on this host) ---
# model = "ollama/qwen3-coder:30b"
# api_base_env = "OLLAMA_HOST"

temperature = 0.7
max_tokens = 16000
```

Every provider block is commented out by default — `sio suggest` will fail
loudly with "no LM available" until you opt in. This is intentional: a
fresh install should never silently dispatch to the wrong provider.

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
| `sio suggest` | Generate suggestions from patterns (supports `--mode auto\|hitl`). See **Multi-Hop Targeted Search** below for `--refine` / `--strategy` / `--within`. |
| `sio suggest-review` | Interactive review of pending suggestions |
| `sio approve <id>` | Approve a suggestion (with optional `--note`) |
| `sio reject <id>` | Reject a suggestion (with optional `--note`) |
| `sio apply <id>` | Apply an approved suggestion to its target file |
| `sio rollback <id>` | Revert an applied change |
| `sio trend` | Pattern growth over time (weekly / daily / monthly buckets, top-N, grep/pattern filters, trend arrows) |

#### Multi-Hop Targeted Search (v3.1 — 2026-04-24)

Single-pass grep in `sio suggest` forces an impossible trade-off: wide grep returns noisy unrelated suggestions, tight grep starves DSPy of training data. **Hop-2** narrows Hop-1's result set with a second predicate so rare-but-high-signal clusters are preserved.

| Flag | Purpose |
|---|---|
| `--refine <terms>` | Second AND-filter (comma-separated OR within). Applied per `--strategy`. |
| `--strategy filter` (default) | Pre-cluster narrowing. Fast, shallow. |
| `--strategy recluster` | No pre-filter; re-cluster Hop-1, then select sub-clusters matching `--refine`. Slower, finds sub-structure. |
| `--strategy hybrid` | `filter` + `recluster`. Balance. |
| `--within <csv>` | Feed a previously exported `errors_preview.csv` (from `--preview`) into Hop-2. Skips DB load + Hop-1 filters. |
| `--use-cache` | Shortcut for `--within ~/.sio/previews/errors_preview.csv`. |
| `--cache-ttl <hours>` | Warning threshold for stale cache. Default 24h. |

Example — narrowing 73 matched errors → 21 targeted → 2 real DSPy suggestions instead of 9 noisy ones:
```bash
sio suggest --grep 'hhdev,zeno,patch,ZENO_DIR,zombie' --type agent_admission \
  --refine 'ZENO_DIR,zombie,cwd,BAS-2' --strategy filter --auto
```

See `~/dev/prd/sio_multi_hop_search_2026-04-24.md` for the design rationale and follow-up work.

### Ground Truth Management

| Command | Description |
|---------|-------------|
| `sio ground-truth seed` | Seed initial ground truth corpus |
| `sio ground-truth generate` | Generate new ground truth examples |
| `sio ground-truth review` | Review and label ground truth entries |

### Configuration & Setup

| Command | Description |
|---------|-------------|
| `sio init` | Stage SIO skills + rules into your AI agent's config dir + seed `~/.sio/` |
| `sio init --status` | Show what's installed where; report drift |
| `sio init --link-path` | Append a managed `export PATH=...` block to your shell rc |
| `sio init --uninstall` | Surgically remove SIO-managed files |
| `sio doctor` | 7-check diagnostic with one-line fix commands for any problem |
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
        ├── hooks/                #   Lifecycle hook scripts (PreCompact, Stop, ...)
        ├── hooks/                #   v3: PostToolUse, PreCompact, Stop, UserPromptSubmit
  _bootstrap/
    skills/                       # 19 bundled slash commands (staged by `sio init`)
    rules/tools/sio.md            #   canonical SIO usage rule
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
| **Lifecycle Hooks** | PreCompact, Stop, UserPromptSubmit capture data in real-time | `sio init` |
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
sio init                         # Stage 19 slash commands + tool rule into ~/.claude/
sio doctor                       # Verify everything resolved correctly
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
