# CLI Command Contracts: SIO

**Interface**: `sio` CLI via Click framework
**Install**: `pip install sio` → `sio` command available

## Commands

### `sio install`

Install SIO for a specific AI CLI platform.

```
sio install --platform <platform> [--db-path <path>]
sio install --auto  # auto-detect installed platforms
```

| Arg | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| --platform | choice | Yes (or --auto) | N/A | claude-code \| gemini-cli \| opencode \| codex-cli \| goose |
| --auto | flag | No | false | Auto-detect all installed platforms |
| --db-path | path | No | ~/.sio/<platform>/ | Override database directory |

**Exit codes**: 0 = success, 1 = platform not found, 2 = permission error

**Stdout**: Installation summary with smoke test result.

### `sio health`

Display per-skill health metrics.

```
sio health [--platform <platform>] [--skill <name>] [--format json|table]
```

| Arg | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| --platform | choice | No | all installed | Filter by platform |
| --skill | text | No | all skills | Filter by skill name |
| --format | choice | No | table | Output format |

**Stdout**: Table or JSON with per-skill metrics (invocation count, satisfaction rate, false/missed trigger rates, last optimization date). Skills below 50% satisfaction highlighted.

### `sio review`

Batch review unlabeled invocations.

```
sio review [--platform <platform>] [--session <session_id>] [--limit <n>]
```

| Arg | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| --platform | choice | No | all | Filter by platform |
| --session | text | No | latest | Filter by session ID |
| --limit | int | No | 50 | Max invocations to review |

**Interaction**: Sequential presentation. For each invocation: show user_message, actual_action, behavior_type. Prompt: `[++/--/s(kip)/q(uit)]`. Updates DB immediately per response.

### `sio optimize`

Trigger prompt optimization for a skill.

```
sio optimize <skill_name> [--platform <platform>] [--optimizer <type>] [--dry-run]
```

| Arg | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| skill_name | text | Yes | N/A | Target skill/tool to optimize |
| --platform | choice | No | claude-code | Target platform |
| --optimizer | choice | No | gepa | gepa \| miprov2 \| bootstrap_fewshot |
| --dry-run | flag | No | false | Show proposed diff without applying |

**Quality gates** (enforced before optimization):
- Minimum 10 labeled examples for the skill
- Minimum 5 failure examples
- Arena regression suite must pass post-optimization
- Drift >40% requires manual approval

**Stdout**: Proposed diff. Prompt: `[a(pprove)/r(eject)/d(etails)]`.

**On approve**: Write to platform config, commit to git, update OptimizationRun record.

**On failure**: Atomic rollback, log error with full context.

### `sio purge`

Manually trigger retention purge.

```
sio purge [--platform <platform>] [--older-than <days>] [--dry-run]
```

| Arg | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| --platform | choice | No | all | Target platform |
| --older-than | int | No | 90 | Days threshold |
| --dry-run | flag | No | false | Show count without deleting |

**Stdout**: Count of records to purge / purged. Gold standards always exempt.

### `sio export`

Export labeled data for external analysis.

```
sio export [--platform <platform>] [--format csv|json] [--output <path>]
```

**Stdout/File**: All invocations with labels in requested format.
