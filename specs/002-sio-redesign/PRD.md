# PRD: SIO v2 — Self-Improving Organism (Redesign)

## Problem Statement

AI coding assistants (Claude Code, Cursor, Windsurf) make the same mistakes repeatedly across sessions. Users notice patterns — "it keeps failing on Read for non-existent files" — but there's no system to automatically detect these patterns, learn from them, and improve behavior.

The data to fix this **already exists**. Every Claude Code session is captured in SpecStory conversation files (`~/.specstory/history/*.md`) and Claude JSONL transcripts (`~/.claude/projects/*/*.jsonl`). Today, this data sits unused.

## Vision

SIO mines existing session data to find recurring error patterns, builds structured datasets from those patterns, and generates improvement suggestions — all without requiring user intervention during the analysis phase. When the user opens their next session, a home file of ranked suggestions is waiting for approval.

**Two operating modes:**
1. **On-demand** — User triggers: `sio mine --since "1 week"` → immediate analysis
2. **Passive** — Cron job runs daily/weekly, writes suggestions to `~/.sio/suggestions.md`, ready for review next session

## Core Principle

> **No new capture infrastructure.** The data already exists in SpecStory and Claude JSONL. SIO reads it, not duplicates it.

## Target Users

- Developers using Claude Code (primary)
- Extensible to Cursor, Windsurf, other AI coding CLIs (future)

## Data Sources

| Source | Location | Contains |
|--------|----------|----------|
| SpecStory history | `~/.specstory/history/*.md` | Full conversation history with timestamps |
| Claude JSONL transcripts | `~/.claude/projects/*/*.jsonl` | Raw session messages, tool calls, errors |
| Session metadata | `~/.claude/projects/*/session.json` | Session start/end, message counts, summaries |

## Feature Requirements

### FR-001: Session Mining
- Parse SpecStory markdown files for tool calls, errors, user corrections
- Parse Claude JSONL transcripts for structured tool call data (tool_name, input, output, error)
- Support time-window filtering: last N days, last N weeks, custom date range
- Support project filtering: mine only sessions for a specific project
- Output: structured error records with timestamp, session_id, tool_name, error_text, surrounding context

### FR-002: Error Extraction
- Identify tool call failures (non-zero exit, error messages, exception traces)
- Identify user corrections ("No, actually...", "Instead,", "That's wrong")
- Identify repeated attempts (same tool called multiple times for same intent)
- Identify undos (git checkout, git revert within 30s of a tool call)
- Each extracted error includes: the failing action, what the user wanted, what went wrong

### FR-003: Pattern Clustering
- Group errors by: same tool + similar error message + similar user intent
- Use embedding similarity (fastembed, all-MiniLM-L6-v2) for fuzzy matching
- Configurable similarity threshold (default 0.80)
- Deduplicate across sessions (same error in 5 sessions = 1 pattern, count=5)
- Rank patterns by: frequency × recency (recent frequent errors score highest)
- Output: pattern_id, description, count, affected_sessions, time_range, representative_examples

### FR-004: Dataset Construction
- For each pattern, collect positive examples (same tool succeeded) and negative examples (same tool failed)
- Structure: `{input, expected_output, actual_output, outcome, session_id, timestamp, context}`
- Store at `~/.sio/datasets/<pattern_id>.json`
- Track lineage: which sessions contributed, which time window, pattern version
- Incremental updates: new sessions append to existing datasets, no full rebuild needed
- Minimum dataset size: configurable (default 5 examples before generating suggestions)

### FR-005: Suggestion Generation
- For each pattern with sufficient data, generate a proposed fix
- Fix types: CLAUDE.md rule addition, SKILL.md update, hook configuration change
- Each suggestion includes: description, confidence score (0-100%), proposed change (diff), affected pattern
- Rank suggestions by: confidence × pattern frequency
- Write to home file: `~/.sio/suggestions.md`

### FR-006: Passive Scheduling
- Daily quick scan: mine last 24h, update clusters, flag new patterns
- Weekly deep analysis: full re-analysis, rebuild datasets, regenerate all suggestions
- Implemented via cron entries or systemd timers (user installs via `sio schedule install`)
- Log output to `~/.sio/logs/`
- No human involvement during analysis runs

### FR-007: Home File
- Location: `~/.sio/suggestions.md`
- Format: Markdown with ranked suggestions, each with approve/reject commands
- Sections: High Priority, Medium Priority, Low Priority
- Each entry: pattern description, occurrence stats, proposed change, confidence, action commands
- Updated by passive scheduler, readable by human or by session-start hook
- Stale suggestions (>30 days) auto-archived

### FR-008: Human Review
- `sio review` — interactive review of pending suggestions
- Actions per suggestion: approve / reject / defer / edit / add-note
- Two tagging modes:
  - **Human tags**: user explicitly categorizes (prompt issue, tool issue, user error, etc.)
  - **AI-assisted tags**: AI proposes categorization from positive/negative examples, user approves
- Review state persisted (can quit and resume)

### FR-009: Change Application
- Approved suggestions write to correct targets:
  - Prompt rules → `~/.claude/CLAUDE.md` (append, never overwrite existing content)
  - Skill updates → `~/.claude/skills/*/SKILL.md`
  - Hook configs → `~/.claude/settings.json` (merge with existing hooks)
- Each change git-committed with descriptive message referencing the pattern
- Change log at `~/.sio/changelog.md`
- Rollback: `sio rollback <change_id>` reverts a specific applied change

### FR-010: Regression Prevention
- Before applying a change, validate against gold standards (known-good interactions)
- Drift detection: if proposed change diverges >40% from current prompt, require explicit confirmation
- Collision detection: if two suggestions modify the same file section, warn user

## Non-Functional Requirements

- **NF-001**: Mining 1000 sessions should complete in <60 seconds
- **NF-002**: All data stays local — no external API calls for analysis (embedding model runs locally via fastembed)
- **NF-003**: Passive analysis must not interfere with active Claude Code sessions (separate process, WAL-mode SQLite)
- **NF-004**: Works offline — no internet required after initial fastembed model download

## Tech Stack

- **Language**: Python 3.11+
- **CLI**: Click + Rich (terminal UI)
- **Database**: SQLite with WAL mode for concurrent read/write
- **Embeddings**: fastembed (sentence-transformers/all-MiniLM-L6-v2, 384 dims)
- **Config**: TOML at `~/.sio/config.toml`
- **Scheduling**: cron / systemd timer entries
- **Testing**: pytest, ruff

## Project Structure

```
src/sio/
├── mining/
│   ├── specstory_parser.py    # Parse SpecStory .md files
│   ├── jsonl_parser.py        # Parse Claude JSONL transcripts
│   ├── error_extractor.py     # Extract errors, corrections, undos
│   └── time_filter.py         # Time-window filtering
├── clustering/
│   ├── pattern_clusterer.py   # Embedding-based error clustering
│   └── ranker.py              # Frequency × recency ranking
├── datasets/
│   ├── builder.py             # Build pos/neg datasets per pattern
│   └── lineage.py             # Track dataset provenance
├── suggestions/
│   ├── generator.py           # Generate fix proposals
│   ├── home_file.py           # Write/update suggestions.md
│   └── confidence.py          # Score suggestion confidence
├── review/
│   ├── reviewer.py            # Interactive review logic
│   └── tagger.py              # Human + AI-assisted tagging
├── applier/
│   ├── writer.py              # Write changes to CLAUDE.md / SKILL.md / settings.json
│   ├── rollback.py            # Revert applied changes
│   └── changelog.py           # Maintain change log
├── scheduler/
│   ├── cron.py                # Install/manage cron entries
│   └── runner.py              # Passive analysis orchestrator
├── core/
│   ├── db/                    # SQLite schema, queries (reuse from v1)
│   ├── embeddings/            # fastembed provider (reuse from v1)
│   ├── config.py              # Config loader (reuse from v1)
│   └── arena/                 # Gold standards, drift, collision (reuse from v1)
└── cli/
    └── main.py                # Click CLI: mine, review, approve, reject, rollback, schedule
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `sio mine --since "3 days"` | Mine recent sessions for errors |
| `sio mine --since "1 week" --project SIO` | Mine with project filter |
| `sio patterns` | Show current pattern clusters |
| `sio datasets` | List built datasets |
| `sio review` | Interactive review of pending suggestions |
| `sio approve <id>` | Approve a suggestion |
| `sio reject <id>` | Reject a suggestion |
| `sio rollback <change_id>` | Revert an applied change |
| `sio schedule install` | Install cron entries for passive analysis |
| `sio schedule status` | Show scheduler status |
| `sio status` | Overall SIO status: patterns, pending suggestions, applied changes |

## Success Metrics

1. User opens session → suggestions.md has relevant, actionable proposals
2. Approved changes measurably reduce error recurrence for that pattern
3. No manual capture or inline feedback required — everything mined from existing data
4. Passive analysis runs silently, no impact on active sessions

## Reuse from v1 (Branch 001)

| Component | Reuse? | Notes |
|-----------|--------|-------|
| SQLite schema + query layer | Yes | Adapt tables for patterns/datasets/suggestions |
| Config loader (config.toml) | Yes | Add new config keys for scheduling, thresholds |
| Click + Rich CLI | Yes | New commands, same framework |
| fastembed provider | Yes | Used for pattern clustering |
| Gold standards + arena | Yes | Validate changes before applying |
| Drift + collision detector | Yes | Check proposed changes |
| PostToolUse hook | **No** | Replaced by mining existing data |
| Inline ++ / -- feedback | **No** | Replaced by batch review of patterns |
| Auto-labeler | **No** | Replaced by AI-assisted tagging |
| DSPy optimizer | **No** | Replaced by suggestion generator |
