# Cookbook

Real-world recipes for common SIO workflows. Each recipe has been tested against live Claude Code session data.

---

## Story 1: "What's my agent doing wrong?"

You suspect Claude keeps making the same mistakes. Find out in 30 seconds.

```bash
# Mine the last week of Claude sessions
sio mine --since "7 days"

# See error type breakdown
sio errors
```

**What you'll see:**

```
  Error Type Summary
┌──────────────────┬───────┐
│ Type             │ Count │
├──────────────────┼───────┤
│ repeated_attempt │  1569 │
│ tool_failure     │   459 │
│ undo             │    20 │
│ agent_admission  │    17 │
│ user_correction  │    11 │
└──────────────────┴───────┘
```

The five error types tell you different things:
- **repeated_attempt** — Agent retried the same tool 3+ times (spinning wheels)
- **tool_failure** — Tool call actually errored (permission denied, file not found, etc.)
- **user_correction** — You said "no, that's wrong" or "I meant..."
- **agent_admission** — Agent said "I missed", "I should have", "my apologies"
- **undo** — You asked to revert/undo a change

---

## Story 2: "Show me the agent's self-awareness moments"

The most valuable error type is `agent_admission` — where the AI itself admits it made a mistake. These reveal prompt/skill gaps directly.

```bash
# Browse just the admission errors
sio errors --type agent_admission
```

**What you'll see:**

```
│ agent_admission │ Agent admission: You're right, I apologize for  │ Bash │ 2026-02-24T18:26 │
│ agent_admission │ Agent admission: Same bug in the other copy. Let │      │ 2026-02-24T15:10 │
│ agent_admission │ Agent admission: Let me correct that and try     │      │ 2026-02-23T09:42 │
```

Each of these is a moment where the agent knew it did something wrong. SIO captures what the agent said, what the user was asking, and the surrounding context.

---

## Story 3: "Turn those errors into targeted CLAUDE.md rules"

This is the full pipeline. Mine, cluster, build datasets, generate targeted suggestions.

```bash
# One command does it all
sio suggest
```

**What happens behind the scenes:**

```
Step 1: Clustering 2076 errors...
  Found 136 patterns
Step 2: Persisting patterns to database...
  Persisted 136 patterns with error links
Step 3: Building datasets...
  Built 49 datasets
Step 4: Generating targeted suggestions...
  Generated 50 suggestions
```

**What a suggestion looks like (real output):**

```
## Rule: Avoid Repeated Tool Retries

**Pattern**: Same tool called 3+ consecutive times (1481 occurrences detected).

**Tools repeatedly retried**:
- `Bash`: 539 retry sequences
- `Read`: 331 retry sequences
- `Grep`: 135 retry sequences

**Prevention rules**:
- If `Bash` fails twice, stop and diagnose the root cause
  instead of retrying with minor variations.
- If `Read` fails twice, stop and diagnose the root cause
  instead of retrying with minor variations.
- After 2 failed attempts with any tool, try an alternative
  approach or ask the user for guidance.
```

Notice: this isn't generic advice. It names the exact tools, shows actual failure counts, and derives rules from what really happened.

---

## Story 4: "What keeps failing with permissions?"

Filter the pipeline to a specific error type for focused analysis.

```bash
sio suggest --type tool_failure
```

**Real output example:**

```
## Rule: Bash — Prevent Recurring Failures

**Pattern**: 41 failures detected across 24 sessions.

**Observed failures**:
- `Permission to use Bash has been denied. IMPORTANT: You *may*
   attempt to accomplish this action using other tools...`
- `Permission to use Write has been denied...`
- `Permission to use Read has been denied...`

**User intent when failures occurred**:
- Write the integration test file for US4...
- Write 4 test files for the SIO project...

**Prevention rules**:
- Before calling `Bash`, verify the target exists and inputs
  are valid (25 failures observed).
- Before calling `Write`, verify the target exists and inputs
  are valid (12 failures observed).
- Verify file permissions before `Bash` operations.
```

---

## Story 5: "Review and approve a suggestion"

Once suggestions are generated, review them interactively.

```bash
sio suggest-review
```

For each suggestion you see the description, confidence score, target file, and full proposed change. Choose:
- **a** — approve (adds rule to CLAUDE.md)
- **r** — reject (mark as rejected with a note)
- **d** — defer (come back to it later)
- **q** — quit

```bash
# Or approve/reject directly by ID
sio approve 42 --note "prevents repeated Bash retries"
sio reject 43 --note "too generic, need more data"
```

---

## Story 6: "Focus on a specific MCP tool's failures"

SIO catches errors from all MCP tools — Graphiti, Atlassian, DuckDB, Tableau, etc.

```bash
# See patterns for all errors, then look for MCP tools
sio patterns

# Example patterns you'll see:
#  mcp__duckdb-local__query: 23 retry sequences
#  mcp__atlassian-remote__getConfluencePage: 5 retry sequences
#  mcp__graphiti__add_memory: repeated failures
```

The suggestion for MCP tools is specific:

```
## Rule: Avoid Repeated Tool Retries

**Tools repeatedly retried**:
- `mcp__duckdb-local__query`: 23 retry sequences
- `mcp__atlassian-remote__getConfluencePage`: 5 retry sequences

**Prevention rules**:
- If `mcp__duckdb-local__query` fails twice, stop and diagnose
  the root cause instead of retrying with minor variations.
```

---

## Story 7: "Agent admits mistakes — derive prevention rules"

Filter specifically for agent self-admissions.

```bash
sio suggest --type agent_admission --min-examples 2
```

**Real output:**

```
## Rule: Agent Self-Identified Mistakes

**Pattern**: Agent admitted errors 3 times with similar root causes.

**What the agent said**:
- "Same bug in the other copy. Let me fix that one too."
- "Let me fix that:"
- "Let me correct that and try again with proper syntax:"

**What the user was asking for**:
- YES!!!!
- Search the SIO project for all existing documentation files...

**Prevention rules** (derived from agent's own words):
- When self-correcting, state what was wrong and why —
  this helps prevent the same mistake in future sessions.
```

---

## Story 8: "See what users kept correcting"

```bash
sio errors --type user_correction -n 10
```

Shows the exact phrases where users corrected the agent: "no, that's wrong", "I meant X", "not what I wanted", etc. These surface misunderstandings between user intent and agent behavior.

---

## Story 9: "Undo tracking — what changes should never have been made"

```bash
sio errors --type undo
```

Every time a user said "undo that", "revert that", "git checkout", or "roll back" — SIO captured it. The suggestion engine turns these into rules like:

```
## Rule: Reduce Undo/Revert Requests

**Prevention rules**:
- Before making significant changes, describe the planned
  modifications and get user confirmation.
- Prefer small, incremental edits over large rewrites.
- Always create a git commit before making risky changes.
```

---

## Story 10: "Sibling tool call cascade — the Claude Code bug"

A known Claude Code bug: when parallel tool calls are dispatched and one fails, all siblings are cancelled. SIO detects these automatically.

```bash
sio patterns
# Look for: "<tool_use_error>Sibling tool call errored</tool_use_error>"
```

**Real suggestion generated:**

```
## Rule: Bash — Prevent Recurring Failures

**Pattern**: 91 failures detected across 44 sessions.

**Observed failures**:
- `<tool_use_error>Sibling tool call errored</tool_use_error>`

**Prevention rules**:
- Before calling `Bash`, verify the target exists...
- Never mix MCP tools with standard tools in the same
  parallel batch — MCP timeouts cascade-kill fast tools.
```

---

## Story 11: "Repo development — live feedback while coding"

This is the power play. Run SIO against the project you're actively developing to discover what structural changes are needed — not after the fact, but while you're building.

**Scenario**: You're building a new feature with Claude Code. After a few hours of coding, you want to know what's going wrong in the agent's interactions with your codebase.

```bash
# Step 1: Mine just this project's recent sessions
sio mine --since "4h" --project SIO

# Step 2: See what error patterns are emerging
sio errors --type tool_failure -n 20
```

**What you learn from the errors:**

The errors tell you about your code structure:
- **Repeated `Read` failures** on certain paths → your file organization is confusing the agent
- **`Edit` failures** with "old_string not unique" → your code has duplicated blocks that need refactoring
- **Bash test failures** happening 3+ times → your test setup has a gap
- **Agent admissions** like "I missed the import" → your module structure has implicit dependencies

```bash
# Step 3: Generate suggestions targeted at your repo
sio suggest --type tool_failure --min-examples 2
```

**Real development feedback you get:**

```
## Rule: Read — Prevent Recurring Failures

**Observed failures**:
- `File does not exist. Note: your current working directory is
   /home/user/my-project`

**User intent when failures occurred**:
- Implement the authentication middleware in src/middleware/auth.py

**Prevention rules**:
- Before calling `Read`, verify the target exists (6 failures observed).
```

This tells you: the agent keeps looking for `src/middleware/auth.py` but it doesn't exist yet. You need to create the directory structure first, or your plan.md has the wrong paths.

```bash
# Step 4: Check for repeated attempts (agent spinning wheels)
sio errors --type repeated_attempt -n 10
```

If you see the agent retrying `Edit` on the same file 3+ times, that file probably needs refactoring — the agent can't find unique strings to match. If `Bash` keeps failing on tests, your test fixtures or environment setup is broken.

```bash
# Step 5: Check agent admissions for code structure insights
sio errors --type agent_admission
```

When the agent says "I should have read the file first" or "I missed that this module imports from X" — those are signals about:
- **Missing docstrings** on modules the agent needs to understand
- **Circular dependencies** the agent keeps tripping over
- **Implicit conventions** that should be explicit in CLAUDE.md

**The development feedback loop:**

```
Write code with Claude → SIO mines the session →
Patterns reveal structural issues → Fix the code structure →
Generate CLAUDE.md rules for your repo → Agent works better →
Write more code → repeat
```

This turns SIO from a post-hoc analysis tool into a **live development companion** that tells you where your codebase is hard for an AI agent to work with.

---

## Story 12: "Track improvements over time"

After applying suggestions and fixing code structure, verify things are actually getting better.

```bash
# Check overall status
sio status

# Mine fresh data after your changes
sio mine --since "1d"

# Compare error counts — are they going down?
sio errors
```

The error type summary table is your scorecard. After applying targeted CLAUDE.md rules:
- **repeated_attempt** should decrease (agent stops spinning)
- **tool_failure** should decrease (preconditions are checked)
- **user_correction** should decrease (agent understands intent better)
- **agent_admission** may increase initially (agent becomes more self-aware with better prompts) then decrease as root causes are fixed

---

## Story 13: "Find all Databricks SQL failures across every project"

This is the cross-project search story. You've been working with Databricks SQL across multiple projects — jira issues, pipelines, data work — and you want to gather every SQL failure the agent has ever hit, regardless of which project folder it lived in.

**Step 1: Mine everything**

```bash
# Mine the full year across ALL SpecStory + JSONL files
sio mine --since "1y"
# Scanned 1264 files
# Found 8125 errors
```

This crawls `~/.claude/projects/` (all 1,262 JSONL files) and `~/.specstory/history/` recursively. Every project — jira issues, pipelines, SIO itself — gets mined.

**Step 2: Search for Databricks content**

```bash
# Search ALL mined errors for "databricks" keyword
sio errors --grep databricks
```

Output:

```
Error Type Summary (matching 'databricks': 564 hits)
┌──────────────────┬───────┐
│ Type             │ Count │
├──────────────────┼───────┤
│ repeated_attempt │   428 │
│ tool_failure     │   126 │
│ user_correction  │     6 │
│ agent_admission  │     2 │
│ undo             │     2 │
└──────────────────┴───────┘
```

564 errors across all your projects that mention Databricks. The `--grep` flag searches error text, user messages, surrounding context, AND source file paths — so it catches everything.

**Step 3: Drill into the SQL failures**

```bash
# Just the actual tool failures related to Databricks
sio errors --grep databricks --type tool_failure -n 20
```

This shows you every time the agent hit a Databricks-related error — SQL syntax errors, connection timeouts, permission denials, schema mismatches.

**Step 4: Generate targeted Databricks rules**

```bash
# Full pipeline filtered to Databricks content
sio suggest --grep databricks --min-examples 2
```

```
Step 1: Clustering 564 errors matching 'databricks'...
  Found 40 patterns
Step 2: Persisting patterns to database...
Step 3: Building datasets...
  Built 18 datasets
Step 4: Generating targeted suggestions...
  Generated 18 suggestions
```

**What you get — real suggestions from real data:**

```
## Rule: Avoid Repeated Tool Retries

**Tools repeatedly retried**:
- `mcp__superset__superset_sqllab_execute_query`: 9 retry sequences
- `mcp__superset__superset_chart_get_by_id`: 8 retry sequences

**Prevention rules**:
- If `mcp__superset__superset_sqllab_execute_query` fails twice,
  stop and diagnose the root cause instead of retrying.
```

**Step 5: Same thing for Snowflake**

The `--grep` flag works with any keyword:

```bash
sio errors --grep snowflake
sio errors --grep "SQL syntax"
sio errors --grep "connection refused"
sio suggest --grep snowflake --min-examples 2
```

**The power here**: You're not limited to project folders. Any keyword that appears anywhere in your Claude session history — error messages, user requests, agent responses, file paths — gets surfaced.

---

## Story 14: "Cross-project intelligence — what tools fail the most?"

Use `--grep` with tool-specific keywords to see failure patterns across all your work:

```bash
# MCP tool failures
sio errors --grep "mcp__" --type tool_failure -n 30

# Specific MCP servers
sio errors --grep "atlassian" --type tool_failure
sio errors --grep "graphiti" --type tool_failure
sio errors --grep "superset" --type tool_failure
sio errors --grep "duckdb" --type tool_failure

# Permission denials across all projects
sio errors --grep "Permission" --type tool_failure -n 20

# Sibling cascade errors
sio errors --grep "Sibling tool call"

# Connection/timeout issues
sio errors --grep "timeout"
sio errors --grep "connection"
```

---

## Quick Reference

```bash
# Mine errors
sio mine --since "7 days"          # last week
sio mine --since "1d"              # last 24 hours
sio mine --source specstory        # only SpecStory files
sio mine --source jsonl            # only JSONL transcripts
sio mine --project my-api          # filter by project name

# Browse errors
sio errors                         # all errors with type summary
sio errors --type agent_admission  # filter by type
sio errors --type tool_failure -n 50  # more results

# Cluster into patterns
sio patterns                       # ranked by importance
sio patterns --type tool_failure   # filter first, then cluster

# Full pipeline: cluster → dataset → suggest
sio suggest                        # all error types
sio suggest --type agent_admission # focused analysis
sio suggest --min-examples 2       # lower threshold for small datasets

# Review suggestions
sio suggest-review                 # interactive review
sio approve 42 --note "good rule"  # approve directly
sio reject 43                      # reject directly

# Manage applied changes
sio rollback <change_id>           # undo an applied change

# Status
sio status                         # overall pipeline counts

# Passive mode (cron)
sio schedule install               # daily + weekly auto-analysis
sio schedule status                # check cron status
```

## Time Shorthands

```bash
sio mine --since "3d"           # 3 days
sio mine --since "1w"           # 1 week
sio mine --since "2mo"          # 2 months
sio mine --since "6h"           # 6 hours
sio mine --since "30min"        # 30 minutes
sio mine --since "yesterday"    # start of yesterday
sio mine --since "last week"    # 7 days ago
sio mine --since "2026-01-15"   # absolute date
```

## Integration with Claude Code

SIO reads your existing session files — no additional setup needed:

| Source | Path | Format |
|--------|------|--------|
| SpecStory | `~/.specstory/history/` | Markdown conversations |
| Claude JSONL | `~/.claude/projects/` | Raw wire-format transcripts |

Approved suggestions modify your `CLAUDE.md` rules, which Claude Code reads on every session start. The feedback loop is:

```
Claude makes mistakes → SIO mines them → clusters into patterns →
generates targeted rules → you approve → CLAUDE.md updated →
Claude reads new rules → fewer mistakes
```
