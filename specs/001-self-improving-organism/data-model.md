# Data Model: SIO

**Source**: spec.md Key Entities + PRD Section 5

## Entity: BehaviorInvocation

The central fact table. One row per AI action observed.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | INTEGER | PK, AUTOINCREMENT | Unique record ID |
| session_id | TEXT | NOT NULL | UUID identifying the CLI session |
| timestamp | TEXT | NOT NULL, ISO-8601 | When the action occurred |
| platform | TEXT | NOT NULL | 'claude-code' \| 'gemini-cli' \| 'opencode' \| 'codex-cli' \| 'goose' |
| user_message | TEXT | NOT NULL, secret-scrubbed | What the user said (redacted secrets) |
| behavior_type | TEXT | NOT NULL, CHECK | 'skill' \| 'mcp_tool' \| 'preference' \| 'instructions_rule' (closed enum — new types require schema migration) |
| actual_action | TEXT | NULLABLE | Tool/skill that actually fired |
| expected_action | TEXT | NULLABLE | Agent-inferred correct action |
| activated | INTEGER | 0 or 1 | Did anything fire? |
| correct_action | INTEGER | 0 or 1 | Did the RIGHT thing fire? |
| correct_outcome | INTEGER | 0 or 1 | Did it produce the right result? |
| user_satisfied | INTEGER | 0 or 1, NULLABLE | User's binary label |
| user_note | TEXT | NULLABLE | Optional free-text note |
| passive_signal | TEXT | NULLABLE | 'undo' \| 'correction' \| 're_invocation' \| NULL |
| history_file | TEXT | NULLABLE | Path to conversation history file |
| line_start | INTEGER | NULLABLE | Start line in history file |
| line_end | INTEGER | NULLABLE | End line in history file |
| token_count | INTEGER | NULLABLE | Tokens consumed (V0.1: NULL — platform hooks don't provide this; reserved for future platform support) |
| latency_ms | INTEGER | NULLABLE | Wall clock tool start→end in ms (V0.1: NULL — platform hooks don't provide timing; reserved for future platform support) |
| labeled_by | TEXT | NULLABLE | 'inline' \| 'batch_review' \| 'passive' \| NULL |
| labeled_at | TEXT | NULLABLE, ISO-8601 | When the label was applied |

**Identity**: `id` is the primary key. `(session_id, timestamp, actual_action)` forms a natural unique key.

**Deduplication**: Application-level check in `log_invocation()` — NOT a SQL UNIQUE constraint. The natural key can legitimately repeat if the same tool fires twice in the same second with different inputs. Dedup checks for an existing row matching `(session_id, timestamp, actual_action)` within a 1-second window and skips if found.

**Lifecycle**: Created on tool completion → optionally labeled by user → optionally flagged by passive detection → aggregated into health → consumed by optimizer → purged after 90 days (unless gold standard).

**V0.1 `user_message` fallback**: When `user_message` is `[UNAVAILABLE]`, the record is valid for auto-labeling, health aggregation, and retention purge. It is EXCLUDED from: passive signal detection (requires message text), optimizer training sets (filter out in `get_labeled_for_optimizer()`), and corpus mining.

**Indexes**:
- `idx_session` on `(session_id)`
- `idx_platform_behavior` on `(platform, behavior_type)`
- `idx_satisfaction` on `(user_satisfied)` — for optimizer queries
- `idx_timestamp` on `(timestamp)` — for retention purge

**PRAGMAs**:
- `PRAGMA journal_mode=WAL` — concurrent reads + writes
- `PRAGMA busy_timeout=1000` — 1s wait on lock before failing (hooks must complete <2s)
- `PRAGMA auto_vacuum=INCREMENTAL` — reclaim space after purge

**Corruption Recovery**: On DB open, if `PRAGMA integrity_check` fails: rename corrupt file to `behavior_invocations.db.corrupt.{timestamp}`, create fresh DB with schema, log to error.log. Corrupt file preserved for forensics.

## Entity: SkillHealth

Materialized aggregate view. Derived from BehaviorInvocation.

| Field | Type | Description |
|-------|------|-------------|
| platform | TEXT | Platform identifier |
| skill_name | TEXT | Skill or tool name |
| total_invocations | INTEGER | Count of all invocations |
| satisfied_count | INTEGER | Count where user_satisfied = 1 |
| unsatisfied_count | INTEGER | Count where user_satisfied = 0 |
| unlabeled_count | INTEGER | Count where user_satisfied IS NULL |
| false_trigger_count | INTEGER | Activated but wrong action |
| missed_trigger_count | INTEGER | Not activated when should have |
| satisfaction_rate | REAL | satisfied / (satisfied + unsatisfied) |
| last_optimization | TEXT | ISO-8601 date of last optimization run |

**Identity**: `(platform, skill_name)` is the composite key.

**Lifecycle**: Recomputed on demand (not a persistent table — a SQL view or cached query result).

## Entity: OptimizationRun

Record of each prompt optimization attempt.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | INTEGER | PK, AUTOINCREMENT | Unique run ID |
| platform | TEXT | NOT NULL | Target platform |
| skill_name | TEXT | NOT NULL | Target skill/tool |
| optimizer | TEXT | NOT NULL | 'gepa' \| 'miprov2' \| 'bootstrap_fewshot' |
| example_count | INTEGER | NOT NULL | Number of labeled examples used |
| before_satisfaction | REAL | NOT NULL | Satisfaction rate before |
| after_satisfaction | REAL | NULLABLE | Satisfaction rate after (measured post-deploy) |
| proposed_diff | TEXT | NOT NULL | The proposed prompt changes |
| status | TEXT | NOT NULL | 'pending' \| 'approved' \| 'rejected' \| 'rolled_back' \| 'deployed' |
| arena_passed | INTEGER | 0 or 1 | Did regression tests pass? |
| drift_score | REAL | NULLABLE | Semantic drift from original (0.0-1.0) |
| created_at | TEXT | NOT NULL, ISO-8601 | When the run was created |
| deployed_at | TEXT | NULLABLE, ISO-8601 | When approved and deployed |
| commit_sha | TEXT | NULLABLE | Git commit SHA of deployed change |

**Identity**: `id` is the primary key.

**Lifecycle**: Created → arena tested → approved/rejected by user → deployed (or rolled back on failure) → after_satisfaction measured over next 20 invocations.

**State Transitions**:
```
pending → [arena fails] → rejected
pending → [arena passes] → approved → deployed
pending → [arena passes] → approved → rolled_back (optimizer crash)
deployed → [measured] → (after_satisfaction updated)
```

## Entity: GoldStandard

Verified-good interactions that must never break.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | INTEGER | PK, AUTOINCREMENT | Unique ID |
| invocation_id | INTEGER | FK → BehaviorInvocation.id | Source invocation |
| platform | TEXT | NOT NULL | Platform |
| skill_name | TEXT | NOT NULL | Skill/tool being tested |
| user_message | TEXT | NOT NULL | The user input |
| expected_action | TEXT | NOT NULL | The correct tool/skill |
| expected_outcome | TEXT | NULLABLE | Expected result pattern |
| created_at | TEXT | NOT NULL, ISO-8601 | When promoted to gold |
| exempt_from_purge | INTEGER | DEFAULT 1 | Always 1 — exempt from 90-day purge |

**Identity**: `id` is the primary key. `invocation_id` links back to the source interaction.

**Lifecycle**: Created when a satisfied invocation is promoted → used in every arena regression test → never purged.

## Entity: PlatformConfig

Per-platform installation metadata.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| platform | TEXT | PK | Platform identifier |
| db_path | TEXT | NOT NULL | Path to behavior_invocations.db |
| hooks_installed | INTEGER | 0 or 1 | Are hooks registered? |
| skills_installed | INTEGER | 0 or 1 | Are skills/extensions installed? |
| config_updated | INTEGER | 0 or 1 | Is instructions file updated? |
| capability_tier | INTEGER | 1, 2, or 3 | Platform capability level |
| installed_at | TEXT | NOT NULL, ISO-8601 | When SIO was installed |
| last_verified | TEXT | NULLABLE, ISO-8601 | Last smoke test date |

**Identity**: `platform` is the primary key (one config per platform).

## Relationships

```
BehaviorInvocation ──1:N──> GoldStandard (one invocation can become one gold standard)
BehaviorInvocation ──N:1──> SkillHealth (many invocations aggregate into one health record)
OptimizationRun ──N:1──> SkillHealth (many runs target one skill)
GoldStandard ──N:1──> OptimizationRun (many gold standards tested per run)
PlatformConfig ──1:N──> BehaviorInvocation (one platform has many invocations)
```

## Data Volume Estimates

- ~100-500 invocations/day per platform
- 90-day window: ~9,000-45,000 rows per platform
- ~50-200 gold standards per platform (grows slowly)
- ~1-5 optimization runs per week
- SQLite file size: <50MB per platform at steady state
