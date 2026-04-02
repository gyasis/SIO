# Data Model: SIO Competitive Enhancement

**Branch**: `001-competitive-enhancement` | **Date**: 2026-04-01

## Existing Tables (unchanged, for reference)

| Table | Purpose | Records |
|-------|---------|---------|
| behavior_invocations | Central fact table for all AI actions | Core |
| optimization_runs | DSPy optimization history | Core |
| gold_standards | Verified test cases | Core |
| platform_config | Per-platform setup state | Config |
| error_records | Mined errors from sessions | Mining |
| patterns | Clustered error patterns | Clustering |
| pattern_errors | Pattern↔error join | Clustering |
| datasets | Training datasets per pattern | Datasets |
| suggestions | Generated improvement suggestions | Suggestions |
| applied_changes | History of applied rule changes | Applier |
| ground_truth | Labeled training corpus | Training |
| recall_examples | Recall query/answer pairs | Training |
| flow_events | Positive workflow sequences | Mining |
| optimized_modules | DSPy module store | Training |

## New Tables

### processed_sessions (FR-003)

Tracks which session files have been mined to prevent re-processing.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK AUTOINCREMENT | Row ID |
| file_path | TEXT | NOT NULL | Absolute path to session file |
| file_hash | TEXT | NOT NULL | SHA-256 of file contents |
| message_count | INTEGER | NOT NULL | Total messages in session |
| tool_call_count | INTEGER | NOT NULL | Total tool calls in session |
| skipped | INTEGER | NOT NULL DEFAULT 0 | 1 if filtered out (too few messages/tools) |
| mined_at | TEXT | NOT NULL | ISO-8601 timestamp of processing |

**Uniqueness**: UNIQUE(file_path, file_hash) — same file re-mined only if content changed.
**Index**: idx_ps_path ON (file_path), idx_ps_hash ON (file_hash)

### session_metrics (FR-002, FR-004)

Per-session aggregate metrics computed during mining.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK AUTOINCREMENT | Row ID |
| session_id | TEXT | NOT NULL UNIQUE | Session identifier (derived from file path + hash) |
| file_path | TEXT | NOT NULL | Source session file |
| total_input_tokens | INTEGER | NOT NULL DEFAULT 0 | Sum of input tokens |
| total_output_tokens | INTEGER | NOT NULL DEFAULT 0 | Sum of output tokens |
| total_cache_read_tokens | INTEGER | NOT NULL DEFAULT 0 | Sum of cache read tokens |
| total_cache_create_tokens | INTEGER | NOT NULL DEFAULT 0 | Sum of cache creation tokens |
| cache_hit_ratio | REAL | | cache_read / (cache_read + input_tokens) |
| total_cost_usd | REAL | NOT NULL DEFAULT 0 | Sum of costUsd across messages |
| session_duration_seconds | REAL | | Last timestamp - first timestamp |
| message_count | INTEGER | NOT NULL DEFAULT 0 | Total messages |
| tool_call_count | INTEGER | NOT NULL DEFAULT 0 | Total tool calls |
| error_count | INTEGER | NOT NULL DEFAULT 0 | Errors detected |
| correction_count | INTEGER | NOT NULL DEFAULT 0 | User corrections detected |
| positive_signal_count | INTEGER | NOT NULL DEFAULT 0 | Positive signals detected |
| sidechain_count | INTEGER | NOT NULL DEFAULT 0 | Sub-agent messages |
| stop_reason_distribution | TEXT | | JSON: {"end_turn": N, "max_tokens": M} |
| model_used | TEXT | | Primary model identifier |
| mined_at | TEXT | NOT NULL | ISO-8601 timestamp |

**Index**: idx_sm_session ON (session_id), idx_sm_mined ON (mined_at)

### positive_records (FR-007–FR-009)

Detected positive user signals, parallel to error_records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK AUTOINCREMENT | Row ID |
| session_id | TEXT | NOT NULL | Session identifier |
| timestamp | TEXT | NOT NULL | When the signal occurred |
| signal_type | TEXT | NOT NULL CHECK(IN ('confirmation','gratitude','implicit_approval','session_success')) | Classification |
| signal_text | TEXT | NOT NULL | The user's actual words |
| context_before | TEXT | | What the assistant did to earn the signal |
| tool_name | TEXT | | Tool that was executed before the signal |
| sentiment_score | REAL | | Message sentiment at signal time |
| source_file | TEXT | NOT NULL | Source session file |
| mined_at | TEXT | NOT NULL | ISO-8601 timestamp |

**Index**: idx_pr_session ON (session_id), idx_pr_type ON (signal_type), idx_pr_tool ON (tool_name)

### velocity_snapshots (FR-014–FR-016)

Point-in-time error frequency measurements for velocity tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK AUTOINCREMENT | Row ID |
| error_type | TEXT | NOT NULL | Error type being tracked |
| session_id | TEXT | NOT NULL | Session that triggered this snapshot |
| error_rate | REAL | NOT NULL | Errors of this type / total errors in window |
| error_count_in_window | INTEGER | NOT NULL | Absolute count in rolling window |
| window_start | TEXT | NOT NULL | Rolling window start (ISO-8601) |
| window_end | TEXT | NOT NULL | Rolling window end (ISO-8601) |
| rule_applied | INTEGER | NOT NULL DEFAULT 0 | 1 if a rule targeting this type was active |
| rule_suggestion_id | INTEGER | REFERENCES suggestions(id) | Link to the applied rule |
| created_at | TEXT | NOT NULL | ISO-8601 timestamp |

**Index**: idx_vs_type ON (error_type), idx_vs_window ON (window_start, window_end)

### autoresearch_txlog (FR-044)

Append-only transaction log for autonomous optimization loop.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK AUTOINCREMENT | Row ID |
| cycle_number | INTEGER | NOT NULL | Which cycle this action belongs to |
| action | TEXT | NOT NULL CHECK(IN ('mine','cluster','grade','generate','assert','experiment_create','validate','promote','rollback','error','stop')) | Action type |
| suggestion_id | INTEGER | REFERENCES suggestions(id) | Related suggestion if applicable |
| experiment_branch | TEXT | | Git worktree/branch name |
| assertion_results | TEXT | | JSON: {"error_rate_decreased": true, ...} |
| details | TEXT | | Human-readable description of action |
| status | TEXT | NOT NULL CHECK(IN ('success','failure','skipped','pending_approval')) | Outcome |
| created_at | TEXT | NOT NULL | ISO-8601 timestamp |

**Index**: idx_tx_cycle ON (cycle_number), idx_tx_action ON (action)

## Modified Tables

### patterns (add column)

| New Column | Type | Default | Description |
|------------|------|---------|-------------|
| grade | TEXT | 'emerging' | CHECK(IN ('emerging','strong','established','declining')) — pattern lifecycle stage |

### applied_changes (add column)

| New Column | Type | Default | Description |
|------------|------|---------|-------------|
| delta_type | TEXT | 'append' | CHECK(IN ('append','merge')) — how the rule was written |

## Entity Relationships

```
processed_sessions ──1:1──> session_metrics (via file_path + hash → session_id)
session_metrics ──1:N──> positive_records (via session_id)
session_metrics ──1:N──> error_records (via session_id, existing table)
error_records ──N:1──> patterns (via pattern_errors, existing join)
patterns.grade ──triggers──> suggestions (when grade = 'strong', auto-generate)
suggestions ──1:1──> autoresearch_txlog (via suggestion_id)
suggestions ──1:N──> applied_changes (existing relationship)
velocity_snapshots ──N:1──> suggestions (via rule_suggestion_id)
```

## State Transitions

### Pattern Grade Lifecycle

```
[new pattern] → emerging (2+ occurrences, 2+ sessions)
                    │
                    ▼ (3+ occurrences, 3+ sessions)
                  strong ──auto-generates──> suggestion
                    │
                    ▼ (5+ occurrences, 7+ days consistent)
                established
                    │
                    ▼ (confidence decays below 0.5)
                declining
```

### Experiment Lifecycle

```
suggestion (status='pending')
    │
    ▼ [user or autoresearch creates experiment]
experiment_create (txlog action)
    │
    ▼ [5 sessions pass]
validate (run assertions)
    │
    ├──pass──> pending_approval (txlog status)
    │              │
    │              ▼ [human approves]
    │          promote → suggestion.status='applied', merge worktree
    │
    └──fail──> rollback → suggestion.status='failed_experiment', delete worktree
```

### Autonomous Loop Cycle

```
[cycle start] → mine → cluster → grade → generate
                                            │
                                            ▼
                              [safety check: <3 active experiments, budget OK]
                                            │
                                     ┌──pass─┘
                                     │
                              experiment_create → [wait for 5 sessions]
                                     │
                              validate (assertions)
                                     │
                              ┌──pass─┼──fail──┐
                              │               │
                       pending_approval    rollback
                              │
                       [human approves]
                              │
                           promote
```

## Validation Rules

- `session_metrics.cache_hit_ratio` must be between 0.0 and 1.0 (inclusive) or NULL
- `session_metrics.total_cost_usd` must be >= 0
- `positive_records.sentiment_score` must be between -1.0 and 1.0
- `velocity_snapshots.error_rate` must be between 0.0 and 1.0
- `patterns.grade` transitions are one-directional (emerging→strong→established) except declining which can come from any grade when confidence drops
- `autoresearch_txlog` is append-only — rows are never updated or deleted
- Max 3 rows in autoresearch_txlog with action='experiment_create' AND status='success' that don't have a corresponding 'promote' or 'rollback' entry (enforces 3 concurrent experiment limit)
