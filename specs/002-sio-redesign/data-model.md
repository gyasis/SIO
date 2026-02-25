# Data Model: SIO v2

## New Entities (added alongside v1 tables)

### ErrorRecord

A single extracted error from a session.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER PK | Auto-increment |
| session_id | TEXT NOT NULL | Source session identifier |
| timestamp | TEXT NOT NULL | ISO 8601 timestamp of the error |
| source_type | TEXT NOT NULL | 'specstory' or 'jsonl' |
| source_file | TEXT NOT NULL | Path to the source file |
| tool_name | TEXT | Tool that errored (Read, Bash, Edit, etc.) |
| error_text | TEXT NOT NULL | The error message or failure description |
| user_message | TEXT | What the user asked for |
| context_before | TEXT | 2-3 lines before the error |
| context_after | TEXT | 2-3 lines after the error |
| error_type | TEXT | 'tool_failure', 'user_correction', 'repeated_attempt', 'undo' |
| mined_at | TEXT NOT NULL | When this record was extracted |

### Pattern

A cluster of similar errors.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER PK | Auto-increment |
| pattern_id | TEXT UNIQUE | Human-readable slug (e.g., 'read-file-not-found') |
| description | TEXT NOT NULL | Summary of the pattern |
| tool_name | TEXT | Primary tool involved |
| error_count | INTEGER NOT NULL | Total occurrences |
| session_count | INTEGER NOT NULL | Distinct sessions affected |
| first_seen | TEXT NOT NULL | Earliest error timestamp |
| last_seen | TEXT NOT NULL | Most recent error timestamp |
| rank_score | REAL NOT NULL | Frequency × recency score |
| centroid_embedding | BLOB | Average embedding vector for the cluster |
| created_at | TEXT NOT NULL | When pattern was first created |
| updated_at | TEXT NOT NULL | Last update timestamp |

### PatternError (join table)

Links errors to their pattern cluster.

| Field | Type | Description |
|-------|------|-------------|
| pattern_id | INTEGER FK | → Pattern.id |
| error_id | INTEGER FK | → ErrorRecord.id |

### Dataset

Positive/negative examples for a pattern.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER PK | Auto-increment |
| pattern_id | INTEGER FK | → Pattern.id |
| file_path | TEXT NOT NULL | Path to dataset JSON file |
| positive_count | INTEGER NOT NULL | Number of positive examples |
| negative_count | INTEGER NOT NULL | Number of negative examples |
| min_threshold | INTEGER NOT NULL DEFAULT 5 | Minimum examples required |
| lineage_sessions | TEXT | JSON array of contributing session IDs |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

### Suggestion

A proposed improvement generated from a pattern + dataset.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER PK | Auto-increment |
| pattern_id | INTEGER FK | → Pattern.id |
| dataset_id | INTEGER FK | → Dataset.id |
| description | TEXT NOT NULL | What the suggestion proposes |
| confidence | REAL NOT NULL | 0.0 to 1.0 confidence score |
| proposed_change | TEXT NOT NULL | The actual diff or rule text |
| target_file | TEXT NOT NULL | Where the change would be applied |
| change_type | TEXT NOT NULL | 'claude_md_rule', 'skill_md_update', 'hook_config' |
| status | TEXT NOT NULL DEFAULT 'pending' | pending/approved/rejected/applied/rolled_back |
| ai_explanation | TEXT | AI-generated explanation of the pattern |
| user_note | TEXT | User's note during review |
| created_at | TEXT NOT NULL | |
| reviewed_at | TEXT | |

### AppliedChange

A deployed suggestion.

| Field | Type | Description |
|-------|------|-------------|
| id | INTEGER PK | Auto-increment |
| suggestion_id | INTEGER FK | → Suggestion.id |
| target_file | TEXT NOT NULL | File that was modified |
| diff_before | TEXT NOT NULL | Content before change |
| diff_after | TEXT NOT NULL | Content after change |
| commit_sha | TEXT | Git commit SHA |
| applied_at | TEXT NOT NULL | |
| rolled_back_at | TEXT | Null if still active |

## Relationships

```
ErrorRecord ←→ PatternError ←→ Pattern
                                  ↓
                               Dataset
                                  ↓
                              Suggestion
                                  ↓
                            AppliedChange
```

## Existing v1 Tables (preserved)

- `behavior_invocations` — v1 telemetry data (not modified by v2)
- `optimization_runs` — v1 DSPy optimization records
- `gold_standards` — v1 arena validation data
- `corpus_index` — v1 corpus indexer data

## Storage

- **SQLite database**: `~/.sio/sio.db` (WAL mode, busy_timeout=1000) — new v2 tables added
- **Dataset JSON files**: `~/.sio/datasets/<pattern_id>.json`
- **Suggestions home file**: `~/.sio/suggestions.md`
- **Change log**: `~/.sio/changelog.md`
- **Scheduler logs**: `~/.sio/logs/`
