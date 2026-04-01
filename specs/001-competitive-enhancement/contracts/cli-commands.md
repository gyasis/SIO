# CLI Command Contracts: SIO Competitive Enhancement

**Branch**: `001-competitive-enhancement` | **Date**: 2026-04-01

## Existing Commands (modified behavior)

### `sio mine`

**Current**: Mines JSONL/SpecStory sessions for errors.
**Enhanced**: Now also extracts token/cost metadata, positive signals, sentiment scores, approval/rejection data. Populates `session_metrics`, `positive_records`. Checks `processed_sessions` before parsing.

```
sio mine [OPTIONS]

Options:
  --exclude-sidechains    Exclude sub-agent messages from metrics
  --force                 Re-mine already-processed sessions
  --since TEXT            Only mine sessions after this date (ISO-8601)
  
Output:
  Sessions found: N
  Already processed (skipped): M
  Filtered (too small): K
  Newly mined: N-M-K
  Positive signals captured: P
  Errors captured: E
  Total cost tracked: $X.XX
```

### `sio install`

**Current**: Installs PostToolUse hook.
**Enhanced**: Additionally registers PreCompact, Stop, and UserPromptSubmit hooks.

```
sio install [OPTIONS]

Output:
  Hooks installed:
    ✓ PostToolUse
    ✓ PreCompact (NEW)
    ✓ Stop (NEW)
    ✓ UserPromptSubmit (NEW)
```

### `sio apply`

**Current**: Applies a suggestion by appending to target file.
**Enhanced**: Now checks budget before applying. Uses delta-based writing (merge if >80% similar to existing rule). Can optionally create experiment branch.

```
sio apply SUGGESTION_ID [OPTIONS]

Options:
  --experiment            Apply on experiment branch instead of main
  --force                 Skip budget check (not recommended)

Output (normal):
  Budget: 45/100 lines (CLAUDE.md)
  Action: merge (82% similar to existing rule on line 37)
  Applied suggestion #5 to CLAUDE.md

Output (over budget):
  Budget: 98/100 lines (CLAUDE.md)
  Consolidation triggered: merged 3 similar rules → 1
  New budget: 87/100 lines
  Applied suggestion #5 to CLAUDE.md

Output (blocked):
  Budget: 100/100 lines (CLAUDE.md)
  Consolidation attempted: no candidates found
  BLOCKED: Cannot apply — instruction budget exceeded
  Run 'sio dedupe' to find consolidation opportunities
```

## New Commands

### `sio velocity`

Show learning velocity trends — how error rates change after rules are applied.

```
sio velocity [OPTIONS]

Options:
  --error-type TEXT       Filter to specific error type
  --window INTEGER        Rolling window in days (default: 7)
  --format [table|json]   Output format (default: table)

Output:
  Learning Velocity Report (7-day rolling window)
  ┌─────────────────┬──────┬──────┬───────────┬──────────────┐
  │ Error Type       │ Rate │ Δ    │ Sessions  │ Rule Applied │
  ├─────────────────┼──────┼──────┼───────────┼──────────────┤
  │ unused_import    │ 0.12 │ -45% │ 12        │ #3 (7d ago)  │
  │ select_star      │ 0.08 │ -30% │ 8         │ #7 (3d ago)  │
  │ wrong_path       │ 0.25 │  +5% │ 15        │ none         │
  └─────────────────┴──────┴──────┴───────────┴──────────────┘
  
  ⚠ Rule #7 (select_star): only 3 sessions since applied — velocity uncertain
  ✗ Rule #12 (large_context): no improvement after 8 sessions — review recommended
```

### `sio budget`

Show instruction budget usage per file.

```
sio budget [OPTIONS]

Options:
  --file TEXT             Check specific file only

Output:
  Instruction Budget Report
  ┌──────────────────────┬───────┬─────┬────────┐
  │ File                  │ Lines │ Cap │ Status │
  ├──────────────────────┼───────┼─────┼────────┤
  │ CLAUDE.md             │ 87    │ 100 │ ⚠ 87%  │
  │ rules/tools/bash.md   │ 12    │ 50  │ ✓ 24%  │
  │ rules/tools/git.md    │ 48    │ 50  │ ⚠ 96%  │
  └──────────────────────┴───────┴─────┴────────┘
```

### `sio violations`

Show detected rule violations (existing rules that the assistant ignored).

```
sio violations [OPTIONS]

Options:
  --since TEXT            Filter by date (ISO-8601)
  --format [table|json]   Output format (default: table)

Output:
  Rule Violation Report (last 30 days)
  ┌────┬──────────────────────────┬───────┬──────────┬──────────┐
  │ #  │ Rule                      │ Count │ Last     │ Sessions │
  ├────┼──────────────────────────┼───────┼──────────┼──────────┤
  │ 1  │ Never use SELECT *        │ 5     │ 2d ago   │ 3        │
  │ 2  │ Always use absolute paths │ 3     │ 5d ago   │ 2        │
  └────┴──────────────────────────┴───────┴──────────┴──────────┘
  
  No violations: 14 other rules fully complied with
```

### `sio dedupe`

Find and consolidate semantically duplicate rules.

```
sio dedupe [OPTIONS]

Options:
  --threshold FLOAT       Similarity threshold (default: 0.85)
  --dry-run              Show proposals without applying
  --auto                 Apply all proposals without confirmation

Output:
  Duplicate Rule Analysis (threshold: 0.85)
  
  Pair 1 (similarity: 0.92):
    A: "Never use SELECT * in SQL queries" (CLAUDE.md:45)
    B: "Always use explicit column lists, avoid SELECT *" (rules/sql.md:12)
    Proposed merge: "Never use SELECT * — always list columns explicitly"
    [Apply? y/n]
```

### `sio autoresearch`

Autonomous optimization loop.

```
sio autoresearch start [OPTIONS]
sio autoresearch stop
sio autoresearch status

Options (start):
  --interval INTEGER      Minutes between cycles (default: 30)
  --max-cycles INTEGER    Stop after N cycles (default: unlimited)
  --max-experiments INT   Max concurrent experiments (default: 3)
  --dry-run              Run pipeline but don't create experiments

Output (start):
  AutoResearch Loop started (interval: 30m, max experiments: 3)
  Cycle 1: mine(12 sessions) → cluster(3 patterns) → grade(1 strong) → generate(1 suggestion)
  → experiment created: experiment/sug-15-20260401T1430
  Waiting 30 minutes for next cycle...

Output (status):
  AutoResearch Status
  ├── Cycles completed: 7
  ├── Active experiments: 2/3
  │   ├── sug-15: 3/5 sessions validated
  │   └── sug-18: 1/5 sessions validated
  ├── Promoted: 1 (sug-12)
  ├── Rolled back: 1 (sug-14)
  └── Next cycle in: 14 minutes

Output (stop):
  AutoResearch Loop stopped after 7 cycles.
```

### `sio report --html`

Generate interactive HTML report.

```
sio report [OPTIONS]

Options:
  --html                 Generate HTML report (default: terminal summary)
  --output TEXT          Output path (default: ~/.sio/reports/report-YYYYMMDD.html)
  --days INTEGER         Lookback period (default: 30)
  --open                 Open in browser after generation

Output:
  Generating HTML report (30-day window)...
  Sessions analyzed: 45
  Report saved: ~/.sio/reports/report-20260401.html
  Opening in browser...
```

## Hook Contracts

All hooks follow the Claude Code command hook protocol.

### PreCompact Hook

**Event**: `PreCompact`
**Type**: command
**Timeout**: 5000ms
**Input** (stdin JSON): `{ "session_id": "...", "transcript_path": "..." }`
**Output** (stdout): `{ "action": "allow" }`
**Side effects**: Writes session snapshot to session_metrics, captures recent positive signals.
**Failure behavior**: Retry once silently. On second failure, log to `~/.sio/hook_errors.log`, output `{"action": "allow"}`.

### Stop Hook

**Event**: `Stop`
**Type**: command
**Timeout**: 10000ms
**Input** (stdin JSON): `{ "session_id": "...", "transcript_path": "..." }`
**Output** (stdout): `{ "action": "allow" }`
**Side effects**: Finalizes session_metrics, runs lightweight pattern detection, auto-saves patterns with confidence >0.8 to `~/.claude/skills/learned/`.
**Failure behavior**: Retry once silently. On second failure, log and exit 0.

### UserPromptSubmit Hook

**Event**: `UserPromptSubmit`
**Type**: command
**Timeout**: 2000ms
**Input** (stdin JSON): `{ "session_id": "...", "user_message": "..." }`
**Output** (stdout): `{ "action": "allow" }`
**Side effects**: Detects corrections/undos, increments session correction counter, logs frustration warnings.
**Failure behavior**: Retry once silently. On second failure, log and output `{"action": "allow"}`.
