# Cookbook

Recipes for common SIO workflows.

## Quick Scan — "What went wrong this week?"

```bash
sio mine --since "7 days"
sio patterns
```

This gives you a ranked table of your most common AI coding errors from the past week.

## Daily Review Workflow

```bash
# Mine yesterday's sessions
sio mine --since "yesterday"

# Check for new patterns
sio patterns

# Review any suggestions
sio suggest-review
```

## Focus on a Specific Project

```bash
# Mine only files related to "my-api"
sio mine --since "2 weeks" --project my-api

# View patterns (these show all patterns; filter by reading descriptions)
sio patterns
```

## Deep Analysis — Monthly Report

```bash
# Mine the full month
sio mine --since "1 month"

# Build datasets from the accumulated errors
sio datasets collect --since "1 month"

# Review what SIO suggests
sio suggest-review

# Check overall stats
sio status
```

## Apply and Verify a Suggestion

```bash
# Review and approve
sio approve 42 --note "prevents Edit tool path errors"

# The change is applied to the target file (e.g., CLAUDE.md)
# Verify the change looks correct
cat ~/.claude/CLAUDE.md | tail -5

# If something is wrong, rollback
sio rollback 7
```

## Set Up Fully Passive Mode

```bash
# Install cron jobs — daily and weekly
sio schedule install

# Verify they're active
sio schedule status

# From now on:
# - Daily at midnight: mines last 24h, clusters, generates suggestions
# - Weekly on Sunday: full 7-day analysis with dataset building
# - You just review ~/.sio/suggestions.md whenever convenient
```

## Recover from a Bad Change

```bash
# Check status to see applied changes
sio status

# Rollback the problematic change
sio rollback <change_id>

# The target file is restored to its pre-change state
```

## Mine Only SpecStory Files

```bash
sio mine --since "3 days" --source specstory
```

## Mine Only JSONL Transcripts

```bash
sio mine --since "3 days" --source jsonl
```

## Using Time Shorthands

```bash
# All of these work:
sio mine --since "3d"          # 3 days
sio mine --since "1w"          # 1 week
sio mine --since "2mo"         # 2 months
sio mine --since "6h"          # 6 hours
sio mine --since "30min"       # 30 minutes
sio mine --since "1y"          # 1 year
sio mine --since "yesterday"   # start of yesterday
sio mine --since "last week"   # 7 days ago
sio mine --since "3 days ago"  # same as "3 days"
sio mine --since "2026-01-15"  # absolute date
```

## v1 — Install Telemetry Hooks

```bash
# Set up real-time telemetry for Claude Code
sio install

# Check skill health
sio health

# Review unlabeled invocations
sio review

# Optimize a skill's prompts
sio optimize my-skill --dry-run
sio optimize my-skill
```

## v1 — Data Maintenance

```bash
# Purge records older than 90 days
sio purge --days 90

# Preview what would be purged
sio purge --days 90 --dry-run

# Export all telemetry data
sio export -o telemetry.json
sio export --format csv -o telemetry.csv
```

## Integration with Claude Code

SIO integrates with Claude Code through:

1. **Hooks** — A PostToolUse hook captures tool invocations (v1 telemetry)
2. **Session files** — SIO reads existing SpecStory and JSONL files (v2 mining)
3. **Config changes** — Approved suggestions modify CLAUDE.md rules

The hook is registered in `~/.claude/settings.json` by `sio install`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "python3 -m sio.adapters.claude_code.hooks.post_tool_use"
      }
    ]
  }
}
```

Session files are read from their default locations:

| Source | Path |
|--------|------|
| SpecStory | `~/.specstory/history/` |
| Claude JSONL | `~/.claude/projects/` |

No additional configuration is needed — SIO discovers these automatically.
