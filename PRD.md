# SIO: Self-Improving Organism
## Product Requirements Document

**Version:** 0.2.0
**Date:** 2026-02-17
**Author:** Gyasi Sutton + Claude + Gemini
**Status:** Draft

---

## 1. Problem Statement

AI coding CLIs are static between sessions. When they make a mistake — wrong tool, ignored preference, missed skill trigger — the user corrects it manually, but that correction is lost. The same mistake can happen again tomorrow. There is no automated feedback loop that turns bad interactions into permanent system improvements.

This problem exists across **every** AI CLI platform:
- **Claude Code** — Claude uses WebSearch instead of `gemini_research`
- **Gemini CLI** — Gemini ignores GEMINI.md rules or picks wrong tools
- **OpenCode** — The LLM forgets user coding standards between sessions
- **Codex CLI** — Codex routes to wrong tools, no way to correct permanently
- **Goose** — Extensions fire incorrectly, no feedback mechanism

Each of these is a **learnable failure** that the system should fix itself — regardless of which platform the user prefers.

**Specific failure modes today:**
- User says "gemini research X" → AI uses WebSearch instead of `gemini_research`
- Instructions file says "always search Graphiti first" → AI skips it
- User's file naming preference is documented → AI renames files anyway
- A skill should trigger but doesn't → user has to invoke it manually
- A skill triggers but gives wrong output → user has to redo the work

---

## 2. Vision

SIO transforms any AI coding CLI from a static tool into a **self-improving organism**. Every interaction is a data point. Every failure is a learning opportunity. Through a closed-loop pipeline of observation, labeling, optimization, and deployment, the AI evolves to match the specific user's mental model, codebase patterns, and preferences.

**Multi-platform by design:** SIO doesn't abstract platforms away — it meets each platform where it lives. Claude Code's SIO uses hooks and skills. Gemini CLI's SIO uses extensions and hooks. Goose's SIO uses MCP extensions and recipes. Each platform gets a **native** implementation that leverages that platform's unique strengths.

**The organism analogy:**
- **Nervous system** — Platform-native telemetry captures every action
- **Pain signal** — User's binary satisfaction label (0 = pain, 1 = good)
- **Memory** — Conversation corpus (SpecStory, Gemini history, etc.) + knowledge graphs
- **Brain** — DSPy optimizers evolve better behavior (shared across platforms)
- **Immune system** — Skill Arena prevents regressions
- **Growth** — New skills/extensions emerge from clustered missed triggers

---

## 3. Platform Compatibility

### 3.1 Supported Platforms

| Platform | Extension Model | Hooks | Instructions File | Tool Blocking | Input Modification |
|----------|----------------|-------|-------------------|---------------|-------------------|
| **Claude Code** | Skills (SKILL.md) + Hooks + MCP | 14 events, 3 types (command/prompt/agent) | CLAUDE.md (hierarchical) | Yes (PreToolUse) | Yes (PreToolUse) |
| **Gemini CLI** | Extensions + Hooks + MCP | 11 events, shell commands | GEMINI.md (hierarchical, @import) | Yes (BeforeTool deny) | No |
| **OpenCode** | TypeScript/Bun plugins + MCP | Plugin event hooks | `instructions[]` array (glob patterns) | Yes (throw in before) | Yes (modify ctx) |
| **Codex CLI** | AGENTS.md + Agent Skills + MCP | `notify` callback only | AGENTS.md (hierarchical, 32KiB) | No | No |
| **Goose** | MCP extensions (everything is MCP) + Recipes | None | .goosehints + Recipe instructions | No | No |

### 3.2 Capability Tiers

**Tier 1 — Full (Guaranteed telemetry + blocking + modification):**
- Claude Code: 14 lifecycle hooks, PreToolUse can modify tool inputs, SKILL.md trigger system, LLM subagent hooks

**Tier 2 — Strong (Guaranteed telemetry + blocking, no modification):**
- Gemini CLI: 11 lifecycle hooks, BeforeTool can deny execution, extension bundles
- OpenCode: TypeScript plugin intercept, `system.transform` for dynamic system prompt injection, compaction state preservation

**Tier 3 — Degraded (Post-hoc telemetry only):**
- Codex CLI: `notify` callback fires after tool use (observation only, no interception)
- Goose: No hooks — relies on instruction-injected self-reporting (AI told to call SIO tools after each action)

### 3.3 Per-Platform Extension Model Details

#### Claude Code
- **Skills**: `SKILL.md` files with YAML frontmatter define trigger descriptions, allowed tools, model overrides, and context isolation. Skills can be user-invocable (`/skill-name`) or auto-triggered by Claude. SIO's telemetry, feedback, optimization, and arena are all skills.
- **Hooks**: 14 lifecycle events. Three handler types: `command` (shell script), `prompt` (single LLM call), `agent` (spawns subagent with tools). Hooks read JSON from stdin, respond via stdout JSON + exit codes. `PreToolUse` is the most powerful — it can allow, deny, or modify tool input before execution.
- **CLAUDE.md**: Hierarchical loading from `~/.claude/CLAUDE.md` (global) down through project directory tree. Content injected as system-level context every turn.

#### Gemini CLI
- **Extensions**: Bundles of MCP servers + GEMINI.md context + excluded tools + custom slash commands. Installed via `gemini extension install`.
- **Hooks**: 11 lifecycle events, shell command handlers only. `BeforeTool` can deny with `{"decision": "deny"}` but cannot modify input. `AfterTool` captures results for telemetry. `BeforeModel` can swap models or mock responses.
- **GEMINI.md**: Hierarchical loading (up to git root + into subdirs). Supports `@import` syntax for modularization. Configurable filename (`context.fileName` in settings).

#### OpenCode
- **Plugins**: TypeScript/Bun modules in `.opencode/plugin/`. Full programmatic API: `tool.execute.before` (intercept/block), `tool.execute.after` (post-process), `experimental.chat.system.transform` (inject into system prompt dynamically), `experimental.session.compacting` (preserve state during compaction).
- **Custom Tools**: Defined in TypeScript with schema validation. Full access to Bun shell API, session state, structured logging.
- **No native skill trigger system** — users invoke via slash commands configured in plugin.

#### Codex CLI
- **AGENTS.md**: Hierarchical root-to-CWD loading, `AGENTS.override.md` for precedence, 32KiB limit. Content injected as user-role messages.
- **Agent Skills**: Supports the Agent Skills open standard (`skills/` directory) — same format as Claude Code's SKILL.md.
- **Notify**: `notify` config runs external program on events. Observation only — cannot intercept, block, or modify.
- **No hook system**: Cannot intervene in tool execution.

#### Goose
- **MCP Extensions**: Everything is an MCP server. Six extension types: stdio, streamableHttp, builtin, platform, inlinePython, frontend. All tools auto-prefixed with `{extension_name}__`.
- **Recipes**: YAML files bundling instructions, required extensions, parameters, and retry logic. The primary way to package SIO for Goose.
- **.goosehints**: Plain text instructions file read by the `developer` builtin extension. Non-hierarchical.
- **MCP Sampling**: Unique capability — MCP tools can request LLM completions back from Goose via `sampling/createMessage`. Enables the optimization pipeline to run sub-LLM queries through MCP.
- **No hooks**: No lifecycle event interception. Telemetry relies on instructions telling the AI to self-report.

---

## 4. Architecture Overview

### 4.1 The Three-Layer Design

```
┌─────────────────────────────────────────────────────────┐
│  LAYER 3: PLATFORM-NATIVE ADAPTERS                       │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────┐ ┌─────┐│
│  │  Claude   │ │  Gemini  │ │OpenCode│ │Codex │ │Goose││
│  │  Code     │ │  CLI     │ │        │ │ CLI  │ │     ││
│  │          │ │          │ │        │ │      │ │     ││
│  │ hooks +  │ │ hooks +  │ │TS      │ │AGENTS│ │MCP  ││
│  │ skills + │ │ extension│ │plugins │ │.md + │ │ext +││
│  │ CLAUDE.md│ │ +GEMINI  │ │+config │ │skills│ │recip││
│  └────┬─────┘ └────┬─────┘ └───┬────┘ └──┬───┘ └──┬──┘│
│       │            │           │         │        │    │
│       ▼            ▼           ▼         ▼        ▼    │
│  ┌─────────────────────────────────────────────────────┐│
│  │  Each adapter has its OWN behavior_invocations.db   ││
│  │  (separate DB per platform — different models =     ││
│  │   different behavior data)                          ││
│  └─────────────────────────────────────────────────────┘│
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────┐
│  LAYER 2: SHARED PYTHON CORE (platform-agnostic)         │
│                          │                               │
│  ┌──────────┐  ┌────────┴──────┐  ┌──────────────────┐ │
│  │  core/db  │  │core/telemetry │  │  core/mining      │ │
│  │  Schema + │  │Logger +       │  │  Corpus indexer + │ │
│  │  Queries  │  │Auto-labeler + │  │  New skill        │ │
│  │           │  │Passive signals│  │  detector          │ │
│  └──────────┘  └───────────────┘  └──────────────────┘ │
│                                                          │
│  ┌──────────────────┐  ┌──────────────────────────────┐ │
│  │  core/dspy        │  │  core/arena                   │ │
│  │  GEPA + MIPROv2 + │  │  Gold standards +             │ │
│  │  BootstrapFewShot │  │  Regression testing +         │ │
│  │  + RLM miner      │  │  Drift monitor +              │ │
│  │                    │  │  Collision detector            │ │
│  └──────────────────┘  └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────┐
│  LAYER 1: THE CLOSED LOOP (per platform)                 │
│                          │                               │
│  ┌──────────┐     ┌─────┴─────────┐     ┌────────────┐ │
│  │  AI       │────▶│  Telemetry    │────▶│  behavior_  │ │
│  │  Actions  │     │  (native)     │     │  invocations│ │
│  └──────────┘     └───────────────┘     │  .db        │ │
│       ▲                                  └──────┬─────┘ │
│       │                                         │       │
│       │           ┌─────────────────┐           │       │
│       │           │  Feedback       │◀── USER   │       │
│       │           │  (++ / --)      │ satisfied? │       │
│       │           └────────┬────────┘           │       │
│       │                    │                    │       │
│       │                    ▼                    ▼       │
│       │           ┌─────────────────────────────┐       │
│       │           │  DSPy Optimization          │       │
│       │           │  (shared core, per-platform │       │
│       │           │   data)                     │       │
│       │           └──────────────┬──────────────┘       │
│       │                          │                      │
│       │                          ▼                      │
│       │           ┌─────────────────────────────┐       │
│       │           │  Artifact Updater            │       │
│       │           │  SKILL.md | CLAUDE.md |      │       │
│       │           │  GEMINI.md | AGENTS.md |     │       │
│       │           │  plugins | recipes | etc.    │       │
│       │           └──────────────┬──────────────┘       │
│       │                          │                      │
│       └──────────────────────────┘                      │
│                                                          │
│  Each cycle: AI gets better → generates better data     │
│  → optimizes further → recursive self-improvement       │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Why Separate DBs Per Platform

Each platform attaches to different LLMs (Claude, Gemini, GPT, open models). The same user prompt produces fundamentally different tool routing, response patterns, and failure modes depending on the model. Cross-platform behavior comparison is not meaningful — optimizing Claude Code's tool routing doesn't help Gemini CLI's tool routing.

Each adapter writes to its own DB:
```
~/.sio/claude-code/behavior_invocations.db
~/.sio/gemini-cli/behavior_invocations.db
~/.sio/opencode/behavior_invocations.db
~/.sio/codex-cli/behavior_invocations.db
~/.sio/goose/behavior_invocations.db
```

The shared core provides the schema and query library; each adapter instantiates its own connection.

---

## 5. Data Model

### 5.1 Core Table: `behavior_invocations`

The central fact table. Binary labels + pointers. No text blobs.

```sql
CREATE TABLE behavior_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    platform TEXT NOT NULL,           -- 'claude-code' | 'gemini-cli' | 'opencode' | 'codex-cli' | 'goose'

    -- What the user said
    user_message TEXT NOT NULL,

    -- What category of behavior
    behavior_type TEXT NOT NULL,      -- 'skill' | 'mcp_tool' | 'preference' | 'instructions_rule'

    -- What happened (agent auto-labels these)
    actual_action TEXT,               -- tool/skill that actually fired
    expected_action TEXT,             -- inferred correct action (agent-labeled)

    -- Binary signals (agent auto-labels)
    activated INTEGER,                -- did anything fire? 0/1
    correct_action INTEGER,           -- did the RIGHT thing fire? 0/1
    correct_outcome INTEGER,          -- did it produce right result? 0/1

    -- Human signal (USER labels ONLY these)
    user_satisfied INTEGER,           -- 0 or 1. THE user's only job.
    user_note TEXT,                   -- optional natural language note

    -- Passive signals (agent auto-detects)
    passive_signal TEXT,              -- 'undo' | 'correction' | 'negative_rating' | NULL

    -- Pointer to full context in conversation history
    history_file TEXT,                -- SpecStory file, Gemini history, etc.
    line_start INTEGER,
    line_end INTEGER,

    -- Metadata
    token_count INTEGER,
    latency_ms INTEGER,
    labeled_by TEXT,                  -- 'inline' | 'batch_review' | 'passive' | NULL
    labeled_at TEXT
);
```

**Note:** `behavior_type` uses `instructions_rule` (generic) instead of `claude_md_rule` to be platform-agnostic. Each platform maps: CLAUDE.md rules → `instructions_rule`, GEMINI.md rules → `instructions_rule`, AGENTS.md rules → `instructions_rule`, etc.

### 5.2 Skill Health Aggregate: `skill_health`

Materialized view of per-skill/tool performance.

```sql
CREATE TABLE skill_health (
    behavior_id TEXT PRIMARY KEY,     -- skill name or tool name
    behavior_type TEXT,
    platform TEXT,                    -- which platform this health is for
    total_invocations INTEGER,
    satisfied_count INTEGER,
    unsatisfied_count INTEGER,
    unlabeled_count INTEGER,
    false_trigger_count INTEGER,
    missed_trigger_count INTEGER,
    avg_followup_turns REAL,
    satisfaction_rate REAL,
    last_optimized TEXT,
    last_invoked TEXT
);
```

### 5.3 Optimization History: `optimization_runs`

Tracks every DSPy optimization for auditability.

```sql
CREATE TABLE optimization_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    behavior_id TEXT NOT NULL,
    platform TEXT NOT NULL,           -- which platform's data was optimized
    optimizer_used TEXT,              -- 'GEPA' | 'MIPROv2' | 'BootstrapFewShot'
    examples_used INTEGER,
    pre_satisfaction_rate REAL,
    post_satisfaction_rate REAL,
    changes_applied TEXT,             -- JSON of file edits
    git_commit_hash TEXT,
    regression_test_passed INTEGER    -- 0/1
);
```

---

## 6. Modular Folder Structure

```
SIO/
├── PRD.md                                  # This document
│
├── core/                                   # LAYER 2: Shared Python — zero platform deps
│   ├── __init__.py
│   ├── db/
│   │   ├── schema.sql                      # CREATE TABLE statements
│   │   ├── migrations/
│   │   │   └── 001_initial.sql
│   │   ├── queries.py                      # Named query functions (insert, select, aggregate)
│   │   └── skill_health.py                # skill_health materialized view refresh
│   ├── telemetry/
│   │   ├── __init__.py
│   │   ├── logger.py                       # write_invocation() → SQLite
│   │   ├── passive_signals.py              # Detect undo, "No actually", corrections
│   │   └── labeler.py                      # Auto-label activated/correct_action/correct_outcome
│   ├── dspy/
│   │   ├── __init__.py
│   │   ├── signatures.py                   # dspy.Signature classes
│   │   ├── modules.py                      # dspy.Module classes (SIORouter)
│   │   ├── optimizers.py                   # GEPA / MIPROv2 / BootstrapFewShot wrappers
│   │   ├── rlm_miner.py                   # dspy.RLM for conversation corpus mining
│   │   ├── metrics.py                      # satisfaction_metric() binary metric
│   │   └── extraction.py                  # compiled JSON → platform-native artifact diffs
│   ├── mining/
│   │   ├── __init__.py
│   │   ├── corpus.py                       # Abstract corpus interface (SpecStory, Gemini history, etc.)
│   │   ├── corpus_indexer.py              # BM25 + fastembed index for RLM
│   │   └── new_skill_detector.py          # Cluster missed triggers → propose new skills
│   └── arena/
│       ├── __init__.py
│       ├── gold_standards.py              # Manage verified satisfied=1 anchors
│       ├── regression.py                  # Replay gold standards against new prompts
│       ├── drift_monitor.py              # Cosine similarity original vs optimized
│       └── collision_detector.py         # Embedding distance between skill descriptions
│
├── adapters/                               # LAYER 3: Platform-native implementations
│   ├── claude-code/                        # Tier 1 — FULL capability
│   │   ├── README.md
│   │   ├── install.sh                      # Symlinks hooks, registers skills, injects CLAUDE.md
│   │   ├── hooks/
│   │   │   ├── post-tool-telemetry.sh     # PostToolUse → log to claude-code DB
│   │   │   ├── pre-tool-blocker.py        # PreToolUse → block/modify known-bad patterns
│   │   │   ├── user-prompt-passive.py     # UserPromptSubmit → passive signal detection
│   │   │   └── stop-feedback-prompt.sh    # Stop → prompt "Rate with ++ or --"
│   │   ├── skills/
│   │   │   ├── skill-telemetry/SKILL.md   # View current session telemetry
│   │   │   ├── skill-feedback/SKILL.md    # ++ / -- rating interface
│   │   │   ├── optimize-skill/SKILL.md    # Trigger DSPy optimization
│   │   │   └── skill-arena/SKILL.md       # Run regression tests
│   │   └── claude-md.inject               # SIO rules appended to user's CLAUDE.md
│   │
│   ├── gemini-cli/                         # Tier 2 — STRONG capability
│   │   ├── README.md
│   │   ├── install.sh                      # Registers hooks, installs extension, injects GEMINI.md
│   │   ├── hooks/
│   │   │   ├── after-tool.sh              # AfterTool → log to gemini-cli DB
│   │   │   ├── before-tool-deny.sh        # BeforeTool → deny known regression patterns
│   │   │   └── after-agent.sh             # AfterAgent → passive signal scan
│   │   ├── extension/
│   │   │   └── sio.gemini.json            # Extension manifest: MCP ref + slash cmds
│   │   └── gemini-md.inject               # SIO rules appended to user's GEMINI.md
│   │
│   ├── opencode/                           # Tier 2 — STRONG (programmatic)
│   │   ├── README.md
│   │   ├── install.sh                      # Installs plugin, configures opencode.json
│   │   └── plugin/
│   │       ├── sio-plugin.ts              # Main plugin: tool intercept + system.transform
│   │       ├── telemetry-interceptor.ts   # tool.execute.before → log to opencode DB
│   │       ├── compaction-state.ts        # session.compacting → preserve SIO state
│   │       └── package.json
│   │
│   ├── codex-cli/                          # Tier 3 — DEGRADED (post-hoc only)
│   │   ├── README.md
│   │   ├── install.sh                      # Installs skills, injects AGENTS.md, configures notify
│   │   ├── notify-wrapper.js              # notify callback → log to codex-cli DB
│   │   ├── skills/
│   │   │   ├── skill-feedback/SKILL.md    # Agent Skills standard
│   │   │   └── optimize-skill/SKILL.md
│   │   └── agents-md.inject               # SIO rules appended to user's AGENTS.md
│   │
│   └── goose/                              # Tier 3 — DEGRADED (self-reporting)
│       ├── README.md
│       ├── install.sh                      # Installs extension, registers recipe
│       ├── sio-extension/                 # MCP server for Goose (Python/Node)
│       │   ├── server.py                  # Tools: sio_log, sio_rate, sio_health, sio_optimize
│       │   └── pyproject.toml
│       ├── sio.recipe.yaml               # Recipe: extension + instructions bundle
│       └── goosehints.inject             # SIO rules appended to user's .goosehints
│
├── install.sh                              # Top-level installer with platform flags
├── pyproject.toml                          # Root Python project (core deps)
└── README.md
```

---

## 7. Platform Adapter Specifications

### 7.1 Claude Code Adapter (Tier 1 — Full)

**Telemetry capture:** `PostToolUse` hook fires after every tool execution. The hook reads JSON from stdin containing `tool_name`, `tool_input`, `tool_response`, `session_id`. Calls `core.telemetry.logger.write_invocation()` with all fields. **Guaranteed** — hooks are system-level, not prompt-dependent.

**Tool blocking/modification:** `PreToolUse` hook queries `skill_health` table. If a tool has satisfaction_rate < 30% with >10 invocations, the hook can:
- **Deny** with reason: `{"permissionDecision": "deny", "permissionDecisionReason": "SIO: low satisfaction tool"}`
- **Modify input**: `{"permissionDecision": "allow", "updatedInput": {modified params}}`
- **Escalate**: `{"permissionDecision": "ask"}` — user decides

**Passive signal detection:** `UserPromptSubmit` hook scans the user's next message for correction patterns ("No," / "Actually," / "Instead,"), undo signals, and re-statements of prior intent.

**Feedback UX:**
- `++` / `--` triggers the `skill-feedback` skill which calls `core.db.queries.label_invocation()`
- `Stop` hook injects "Rate with ++ or --" reminder
- `/skill-feedback review` for batch labeling

**Optimization trigger:** `/optimize-skill <behavior_id>` skill invokes `core.dspy.optimizers` with the platform's labeled data.

**Artifact output:** Optimized prompts written as SKILL.md diffs, CLAUDE.md rule updates, or Graphiti memory pointers.

### 7.2 Gemini CLI Adapter (Tier 2 — Strong)

**Telemetry capture:** `AfterTool` hook fires after every tool execution. Shell script reads JSON from stdin, calls Python wrapper for `core.telemetry.logger.write_invocation()`. **Guaranteed** — hooks are system-level.

**Tool blocking:** `BeforeTool` hook can deny execution with `{"decision": "deny"}` for known regression patterns. **Cannot modify** tool input — only allow or deny.

**Passive signal detection:** `AfterAgent` hook runs at end of agent loop, scans conversation for correction patterns.

**Feedback UX:** Slash commands registered via extension:
- `/sio-rate 1` or `/sio-rate 0` for feedback
- `/sio-stats` for skill health dashboard

**Limitations vs Claude Code:**
- No input modification (deny only)
- Shell hooks only (no LLM subagent hooks)
- Coarser event granularity (AfterAgent vs per-turn Stop)

**Artifact output:** Optimized prompts written as GEMINI.md updates, extension configuration changes.

### 7.3 OpenCode Adapter (Tier 2 — Strong Programmatic)

**Telemetry capture:** `tool.execute.after` TypeScript plugin hook fires after every tool use. Directly calls core Python via subprocess or IPC. **Guaranteed** — plugin hooks are system-level.

**Tool blocking:** `tool.execute.before` — throw an Error to block. Can also modify `ctx` before calling `next(ctx)`, enabling input modification (same as Claude Code's PreToolUse).

**Unique strengths:**
- `experimental.chat.system.transform` — dynamically injects current SIO health metrics (satisfaction rate, pending labels) into EVERY system prompt. This is more powerful than Claude Code's static CLAUDE.md for real-time state.
- `experimental.session.compacting` — preserves SIO session state during context compaction, preventing telemetry loss.

**Feedback UX:** Custom slash command `/feedback` defined in plugin. No `++`/`--` trigger mechanism.

**Artifact output:** Optimized prompts written as opencode.json config updates, plugin parameter changes.

### 7.4 Codex CLI Adapter (Tier 3 — Degraded)

**Telemetry capture:** `notify` callback fires after events. Post-hoc observation only — cannot intercept or prevent. Calls Python wrapper for `core.telemetry.logger.write_invocation()`. **Degraded** — notify is fire-and-forget, may miss events.

**Tool blocking:** Not available. No hook system.

**Passive signal detection:** Not available natively. Best effort: the AGENTS.md injection instructs Codex to self-report correction signals.

**Feedback UX:** User invokes `/skill-feedback` Agent Skill explicitly. No `++`/`--` shortcut (no trigger mechanism).

**Limitations vs full tier:**
- No real-time blocking or prevention
- No input modification
- No passive signal detection
- User must explicitly invoke feedback (higher friction)

**Artifact output:** Optimized prompts written as AGENTS.md updates, Agent Skills file changes.

### 7.5 Goose Adapter (Tier 3 — Degraded)

**Telemetry capture:** No hooks. SIO provides an MCP extension with `sio_log_invocation` tool. The recipe's instruction block tells the AI: "After EVERY tool use, call `sio_log_invocation`." **Unreliable** — depends on the AI following instructions.

**Tool blocking:** Not available. No hook system.

**Unique strengths:**
- **MCP Sampling**: The SIO MCP extension can request LLM completions from Goose via `sampling/createMessage`. This enables the optimization pipeline to run DSPy sub-queries directly through MCP without a separate API key.
- **Recipes**: YAML bundles make SIO installation trivial: `goose run --recipe sio.recipe.yaml`.

**Feedback UX:** Instruction-injected: if user says "++", "good", "--", "wrong", the AI is instructed to call `sio_rate`. No guarantee the AI will always comply.

**Limitations vs full tier:**
- Telemetry is self-reported (AI may forget)
- No blocking, no passive detection
- Feedback is instruction-dependent
- Recipe approach is clean but limited

**Artifact output:** Optimized prompts written as recipe instruction updates, .goosehints changes, MCP extension parameter changes.

---

## 8. Components (Shared Core)

### 8.1 Telemetry — The Nervous System

**Purpose:** Record every AI action for later optimization.

**What it captures (via platform-native mechanisms):**
- Every tool call (MCP tools, built-in tools)
- Every skill/extension invocation
- Every preference application (or non-application)
- Every instructions file rule followed (or skipped)

**Agent auto-labels (user does NOT touch these):**
- `activated` — did anything fire?
- `correct_action` — did the right thing fire? (inferred)
- `correct_outcome` — did the output look correct? (inferred)
- `behavior_type` — skill / mcp_tool / preference / instructions_rule
- `actual_action` — what tool/skill was used
- `expected_action` — what should have been used (best guess)

**Passive signal detection** (Tier 1-2 platforms only):
| Signal | Detection | Label |
|--------|-----------|-------|
| User undoes within 30s | git checkout, undo, revert | `user_satisfied = 0` |
| User says "No," "Actually," "Instead" | Next message starts with correction | Flag for review |
| User re-invokes manually | Same intent, different tool | `correct_action = 0` |
| User says nothing, continues | No correction signal | Unlabeled (not counted) |

### 8.2 Feedback — The Pain Signal

**Purpose:** Lightweight human labeling. User provides ONE signal.

**User interface (varies by platform):**

| Platform | Quick Positive | Quick Negative | Batch Review | Friction Level |
|----------|---------------|----------------|-------------|----------------|
| Claude Code | `++` | `--` / `-- note` | `/skill-feedback review` | Minimal (2 chars) |
| Gemini CLI | `/sio-rate 1` | `/sio-rate 0` | `/sio-review` | Low (slash cmd) |
| OpenCode | `/feedback good` | `/feedback bad` | `/feedback review` | Low (slash cmd) |
| Codex CLI | `/skill-feedback` | `/skill-feedback` | `/skill-feedback review` | Medium (explicit invoke) |
| Goose | "that was good" | "that was wrong" | Natural language only | High (unreliable) |

**Design principles:**
- User labels ONLY satisfaction (0/1) + optional note
- Agent labels everything else automatically
- Minimal friction on Tier 1-2 platforms
- Batch review at end of session for anything missed

### 8.3 Optimization — The Brain

**Purpose:** DSPy optimization pipeline that evolves prompts. **Shared across all platforms** — the Python core is identical; only the input data and output artifacts differ.

**DSPy Optimizers (by use case):**

| Optimizer | Data Needed | Use Case |
|-----------|-------------|----------|
| `BootstrapFewShot` | 10-50 examples | Quick prototype, new skills |
| `MIPROv2` | 50-200 examples | Production instruction tuning |
| `GEPA` | 20-30 examples | Complex routing, agentic tasks |
| `SIMBA` | 50+ examples | Fixing edge cases, reducing variance |

**Pipeline (platform-agnostic):**
1. Fetch all labeled rows for target `behavior_id` from platform's DB
2. `dspy.RLM` mines conversation history for full context of failures
3. Convert to `dspy.Example` objects with binary metric
4. Run optimizer (GEPA for routing, MIPROv2 for instructions)
5. Extract optimized prompts from compiled JSON
6. Format as **platform-native** artifact diffs (SKILL.md for Claude, GEMINI.md for Gemini, etc.)
7. Run Arena regression test
8. Present diffs to user: [Apply All] [Review Each] [Reject]
9. Git commit approved changes

**Metric function:**
```python
def satisfaction_metric(example, pred, trace=None):
    """Binary: did the optimized behavior satisfy the user?"""
    return 1.0 if pred.action == example.expected_action else 0.0
```

**Extraction layer (platform-aware):**
```python
def extract_and_diff(compiled_path, target_behavior, platform) -> dict:
    """
    Returns platform-native diffs:
    {
        "artifact_diffs": [
            {"file": "skills/foo/SKILL.md", "diff": "..."},  # Claude Code
            # OR
            {"file": "GEMINI.md", "diff": "..."},            # Gemini CLI
            # OR
            {"file": "plugin/sio-plugin.ts", "diff": "..."},  # OpenCode
            # etc.
        ],
        "memory_updates": [...],      # Knowledge graph pointers
        "git_message": str
    }
    """
```

### 8.4 Arena — The Immune System

**Purpose:** Prevents optimization from breaking working behavior.

**How it works:**
1. Maintains a "Gold Standard" set of user-verified `satisfied=1` interactions (per platform)
2. Before applying any optimization, replays gold standards against new prompts
3. If any `1` becomes a `0` → optimization rejected
4. Cross-skill tournament: tests ALL skills against the change, not just the target

**Regression checks:**
- **Gold standard preservation** — verified good interactions must stay good
- **Skill collision detection** — embedding distance between skill/extension descriptions monitored
- **Semantic drift limit** — if optimized prompt drifts >40% from original, require manual approval
- **False trigger rate** — new description must not steal triggers from other skills

---

## 9. DSPy Integration Details

### 9.1 dspy.RLM — The Data Miner

Conversation corpus is too large for any context window. RLM explores it programmatically.

```python
from dspy import RLM

rlm = dspy.RLM(
    signature="conversation_corpus, failure_record -> failure_analysis",
    max_iterations=20,
    sub_lm=dspy.LM("anthropic/claude-haiku-4-5"),  # cheap for sub-queries
    tools=[search_corpus, extract_conversation_turn],
    verbose=True
)

# RLM writes Python code to search/filter/extract from conversation history
# Only pulls relevant snippets into token space
# Outputs structured failure analysis for GEPA
```

**Corpus abstraction:** `core/mining/corpus.py` defines an abstract interface for conversation history. Each platform implements it:
- Claude Code → SpecStory `.specstory/history/*.md`
- Gemini CLI → Gemini conversation logs
- OpenCode → OpenCode session files
- Codex CLI → Codex session logs
- Goose → Goose conversation logs

### 9.2 GEPA — The Genetic Evolver

Best for skill/tool routing optimization. Reflects on execution traces.

```python
import dspy

class SkillRouter(dspy.Signature):
    """Route user message to correct skill or tool."""
    user_message = dspy.InputField()
    available_actions = dspy.InputField()
    selected_action = dspy.OutputField()

class SIORouter(dspy.Module):
    def __init__(self):
        self.router = dspy.ChainOfThought(SkillRouter)

    def forward(self, user_message, available_actions):
        return self.router(user_message=user_message, available_actions=available_actions)

# Load labeled data FROM PLATFORM-SPECIFIC DB
trainset = load_behavior_invocations(platform="claude-code", satisfaction_filter=None)

# GEPA evolves prompts using genetic search + reflection
optimizer = dspy.GEPA(metric=satisfaction_metric, max_generations=20)
compiled = optimizer.compile(SIORouter(), trainset=trainset)
compiled.save("optimized_router.json")
```

### 9.3 MIPROv2 — The Production Tuner

Best for instruction-heavy optimization with larger datasets.

```python
optimizer = dspy.MIPROv2(metric=satisfaction_metric, auto="medium")
compiled = optimizer.compile(SIORouter(), trainset=trainset, num_trials=30)
```

### 9.4 Extraction Layer

Bridges DSPy compiled JSON → **platform-native** artifacts.

```python
import json

def extract_and_apply(compiled_path, target_skill, platform):
    with open(compiled_path) as f:
        data = json.load(f)

    optimized_instruction = data["router"]["instructions"]
    demonstrations = data["router"]["demos"]

    if platform == "claude-code":
        # Format as SKILL.md content
        skill_md = format_skill_md(description=optimized_instruction, examples=demonstrations)
        write_skill_file(target_skill, skill_md)
    elif platform == "gemini-cli":
        # Format as GEMINI.md section or extension config update
        gemini_section = format_gemini_section(description=optimized_instruction)
        update_gemini_md(target_skill, gemini_section)
    elif platform == "opencode":
        # Format as TypeScript plugin config update
        plugin_config = format_opencode_plugin(description=optimized_instruction)
        update_opencode_plugin(target_skill, plugin_config)
    elif platform == "codex-cli":
        # Format as Agent Skills SKILL.md or AGENTS.md update
        agents_section = format_agents_section(description=optimized_instruction)
        update_agents_md(target_skill, agents_section)
    elif platform == "goose":
        # Format as recipe instruction update or .goosehints
        recipe_update = format_goose_recipe(description=optimized_instruction)
        update_goose_recipe(target_skill, recipe_update)

    # Git commit
    git_commit(f"SIO: optimize {target_skill} ({platform}, GEPA, {len(trainset)} examples)")
```

---

## 10. Dual Optimization Modes

### 10.1 Real-Time Mode (Same Session)

When the user signals dissatisfaction, SIO retries with a more detailed prompt immediately. **Available on Tier 1-2 platforms only** (requires hook interception).

```
User: gemini research the latest DSPy features
AI: [uses WebSearch → gives results]

User: -- should have been gemini

SIO REAL-TIME:
  1. Log: user_satisfied=0, actual_action=WebSearch, user_note="should have been gemini"
  2. Generate detailed re-prompt:
     "The user requested Gemini research. Use gemini_research tool
      with topic='latest DSPy features 2026'. Do NOT use WebSearch."
  3. AI re-executes with correct tool
  4. User gets their answer NOW (not next session)
  5. Successful correction saved as positive training example
```

**Platform availability:**

| Platform | Real-Time Re-prompt | How |
|----------|-------------------|-----|
| Claude Code | Yes | PreToolUse can block + Stop hook re-prompts |
| Gemini CLI | Partial | BeforeTool can deny, AfterAgent can retry |
| OpenCode | Yes | tool.execute.before blocks + re-prompts via plugin |
| Codex CLI | No | No interception capability |
| Goose | No | No hook system |

### 10.2 Background Mode (Between Sessions)

Scheduled or on-demand batch optimization using DSPy. **Available on all platforms** — runs against the platform's local DB.

```
BACKGROUND PIPELINE:
  1. Aggregate all 0s and 1s from recent sessions
  2. RLM mines conversation history for failure context
  3. GEPA/MIPROv2 evolves prompts
  4. Skill Arena regression test
  5. Updated platform-native artifacts committed to git
  6. Next session starts with better defaults
```

### 10.3 How They Work Together

```
Real-time:  Fix the problem NOW (immediate re-prompt)
              ↓
            Log the correction as a training example
              ↓
Background: Use accumulated corrections to permanently improve prompts
              ↓
            Next session: AI gets it right the FIRST time
              ↓
            Fewer real-time corrections needed → cycle converges
```

---

## 11. What Gets Optimized

| Artifact | Platform | What Changes | Triggered By |
|----------|----------|-------------|-------------|
| `SKILL.md` | Claude Code, Codex CLI | Trigger descriptions, instructions, examples | skill failures |
| `CLAUDE.md` | Claude Code | Rules, routing instructions, tool preferences | instructions_rule failures |
| `GEMINI.md` | Gemini CLI | Rules, routing, extension config | instructions_rule failures |
| `AGENTS.md` | Codex CLI | Rules, routing instructions | instructions_rule failures |
| `opencode.json` / plugin | OpenCode | Config, plugin parameters, system prompt | all failure types |
| `.goosehints` / recipe | Goose | Instructions, recipe parameters | all failure types |
| Knowledge graph memory | All | Preferences, workflows, tool patterns | preference failures |
| Scripts (Python/Rust/Node) | All | Tool call parameters, logic fixes | mcp_tool failures |
| New skill proposals | All | Entirely new skill/extension files | Clustered missed triggers |

---

## 12. Safety Rails

### 12.1 Regression Prevention
- Gold standard anchors: immutable test cases that must never break (per platform)
- Skill Arena cross-validation before any deployment
- Git commit for every change — `git revert` always available

### 12.2 Drift Prevention
- Semantic drift monitoring: cosine similarity between original and optimized
- If drift > 40% → require manual approval
- Skill/extension collision detection via embedding distance

### 12.3 Quality Gates
- Minimum 10 labeled examples before any optimization runs
- Satisfaction rate must improve by >= 5% to justify deployment
- No optimization on skills with < 5 failure examples (insufficient signal)

### 12.4 Isolation
- Each optimization scoped to ONE behavior_id on ONE platform
- Cross-skill impact tested in Arena before deployment
- Rollback is always one `git revert` away

---

## 13. Metrics

### 13.1 Organism Health (Per Platform)

| Metric | Formula | Target |
|--------|---------|--------|
| Satisfaction rate | `satisfied / total_labeled` | > 90% |
| Label density | `labeled / total_invocations` | > 30% |
| Conversion rate | Historical 0s that would now be 1s | Increasing |
| Evolution velocity | Optimization runs per week | 1-3 |
| Regression rate | Gold standards broken per run | 0 |
| Telemetry reliability | `logged / actual_tool_calls` | 100% (Tier 1-2), best effort (Tier 3) |

### 13.2 Per-Skill Health

| Metric | Description |
|--------|-------------|
| Invocation count | How often this skill/tool is used |
| Satisfaction rate | % of labeled invocations with satisfaction=1 |
| False trigger rate | Times skill fired when it shouldn't have |
| Missed trigger rate | Times skill should have fired but didn't |
| Last optimized | When DSPy last updated this skill |

---

## 14. Installation

### 14.1 Install Command

```bash
# Install SIO core + Claude Code adapter
./install.sh --claude

# Install SIO core + Goose adapter
./install.sh --goose

# Install SIO core + Gemini CLI adapter
./install.sh --gemini

# Install SIO core + OpenCode adapter
./install.sh --opencode

# Install SIO core + Codex CLI adapter
./install.sh --codex

# Install SIO core + ALL platform adapters
./install.sh --all

# Install SIO core + multiple specific platforms
./install.sh --claude --goose
```

### 14.2 What `install.sh` Does

1. **Core setup:**
   - Verify Python 3.11+ and `uv` available
   - `uv sync` to install core dependencies (DSPy, fastembed, etc.)
   - Create `~/.sio/` directory structure
   - Initialize empty DB for selected platform(s)

2. **Per-platform adapter setup:**

| Platform | Install Actions |
|----------|----------------|
| `--claude` | Symlink hooks → `~/.claude/hooks/sio/`, register skills in `~/.claude/skills/`, append `claude-md.inject` to CLAUDE.md, register SIO MCP server in settings.json |
| `--gemini` | Register hooks in `~/.gemini/settings.json`, install extension via `gemini extension install`, append `gemini-md.inject` to GEMINI.md |
| `--opencode` | Copy plugin to `.opencode/plugin/sio/`, run `bun install`, update opencode.json with MCP server config |
| `--codex` | Copy skills to project `skills/` dir, append `agents-md.inject` to AGENTS.md, configure `notify` in `config.toml` |
| `--goose` | Register MCP extension in `config.yaml`, copy recipe to `~/.config/goose/recipes/`, append `goosehints.inject` to .goosehints |

3. **Smoke test:**
   - Write a test invocation to the platform's DB
   - Read it back, verify schema
   - Print success message with first-use instructions

### 14.3 Platform Detection (Auto Mode)

```bash
# Detect which platforms are installed and set up all of them
./install.sh --auto
```

Detection logic:
- Claude Code: check for `~/.claude/` directory or `claude` on PATH
- Gemini CLI: check for `~/.gemini/` directory or `gemini` on PATH
- OpenCode: check for `.opencode/` in CWD or `opencode` on PATH
- Codex CLI: check for `~/.codex/` directory or `codex` on PATH
- Goose: check for `~/.config/goose/` directory or `goose` on PATH

---

## 15. Gap Analysis

### 15.1 Feature Availability by Platform

| Feature | Claude Code | Gemini CLI | OpenCode | Codex CLI | Goose |
|---------|-------------|------------|----------|-----------|-------|
| Guaranteed telemetry | Yes (hook) | Yes (hook) | Yes (plugin) | Partial (notify) | No (self-report) |
| Tool blocking | Yes | Yes (deny only) | Yes (throw) | No | No |
| Tool input modification | Yes | No | Yes | No | No |
| Passive signal detection | Yes (hook) | Yes (hook) | Yes (plugin) | No | No |
| Automatic feedback prompt | Yes (Stop hook) | Partial (AfterAgent) | Yes (plugin) | No | No |
| Native skill trigger `++/--` | Yes (SKILL.md) | No | No | No | No |
| Dynamic system prompt inject | No (static file) | No (static file) | Yes (system.transform) | No (static file) | No |
| Compaction state preservation | Yes (PreCompact) | No | Yes (session.compacting) | No | No |
| Real-time re-prompt on failure | Yes | Partial (deny+retry) | Yes | No | No |
| MCP Sampling (sub-LLM calls) | No | No | No | No | Yes |
| LLM subagent hooks | Yes | No | No | No | No |

### 15.2 Honest Degradation Notes

**Codex CLI users should expect:**
- Telemetry may miss some events (notify is fire-and-forget)
- No real-time protection against known-bad tool routing
- Higher friction for feedback (must explicitly invoke skill)
- Optimization still works — just fed with less complete data

**Goose users should expect:**
- Telemetry depends entirely on the AI self-reporting (may miss 20-40% of events)
- No automatic feedback prompt — user must remember to rate
- No blocking or prevention — only after-the-fact learning
- Recipes make installation clean, MCP Sampling is unique upside for optimization
- Optimization works but converges slower due to noisier training data

---

## 16. Milestones

### V0.1 — The Nervous System (Weeks 1-2) — Claude Code Only
- [ ] SQLite schema: `behavior_invocations`, `skill_health`, `optimization_runs`
- [ ] `core/db/queries.py` — shared query library
- [ ] `core/telemetry/logger.py` — write_invocation
- [ ] Claude Code `PostToolUse` hook → auto-logging
- [ ] `/skill-feedback` skill (++ / -- / notes)
- [ ] Basic `skill_health` aggregate view

### V0.2 — The Memory (Weeks 3-4) — Claude Code Only
- [ ] Conversation history pointer integration (file + line references)
- [ ] `core/mining/corpus.py` — abstract corpus interface
- [ ] `dspy.RLM` prototype for mining SpecStory
- [ ] Batch review UI (`/skill-feedback review`)
- [ ] Passive signal detection hooks (UserPromptSubmit)

### V0.3 — The Brain (Weeks 5-6) — Claude Code Only
- [ ] DSPy `BootstrapFewShot` first optimization loop
- [ ] DSPy `GEPA` integration for routing optimization
- [ ] Extraction layer: compiled JSON → SKILL.md / CLAUDE.md
- [ ] First successful automated skill improvement

### V0.4 — The Immune System (Weeks 7-8) — Claude Code Only
- [ ] `/skill-arena` regression testing
- [ ] Gold standard anchor management
- [ ] Semantic drift monitoring
- [ ] Skill collision detection

### V0.5 — Multi-Platform: Gemini CLI + OpenCode (Weeks 9-10)
- [ ] Gemini CLI adapter: hooks (AfterTool, BeforeTool), extension bundle
- [ ] OpenCode adapter: TypeScript plugin, system.transform, tool intercept
- [ ] Extraction layer updated for GEMINI.md and opencode.json outputs
- [ ] Cross-platform install.sh with --gemini, --opencode flags

### V0.6 — Multi-Platform: Codex CLI + Goose (Weeks 11-12)
- [ ] Codex CLI adapter: AGENTS.md, Agent Skills, notify wrapper
- [ ] Goose adapter: MCP extension, recipe YAML, .goosehints
- [ ] Extraction layer updated for AGENTS.md, recipe, .goosehints outputs
- [ ] `--codex`, `--goose`, `--all`, `--auto` install flags

### V1.0 — The Organism (Weeks 13-16)
- [ ] Full closed-loop on ALL platforms: observe → label → mine → optimize → deploy → repeat
- [ ] New skill/extension detection from clustered missed triggers
- [ ] MIPROv2 for production-grade optimization
- [ ] Knowledge graph updates from optimization results
- [ ] Dashboard: per-platform skill health, optimization history, conversion trends
- [ ] Meta-optimization: SIO optimizes its own skills/extensions

---

## 17. Tech Stack

| Component | Technology |
|-----------|-----------|
| Behavior store | SQLite (per-platform local DB) |
| Conversation corpus | Platform-native (SpecStory, Gemini logs, etc.) |
| Permanent memory | Graphiti (FalkorDB knowledge graph) — optional |
| Prompt optimization | DSPy (latest, currently 3.1.3) — GEPA, MIPROv2, BootstrapFewShot, RLM |
| Backend LM for DSPy | Claude Sonnet 4.5 (optimizer) + Haiku 4.5 (sub-queries) |
| Artifact management | Git (every optimization = commit) |
| Claude Code interface | Skills (SKILL.md) + Hooks (14 events) |
| Gemini CLI interface | Extensions + Hooks (11 events) |
| OpenCode interface | TypeScript/Bun plugins |
| Codex CLI interface | Agent Skills + AGENTS.md + notify |
| Goose interface | MCP extensions + Recipes + .goosehints |
| Regression testing | Custom Skill Arena (Python) |
| Analytics | DuckDB + per-platform DBs |
| Installer | Bash (install.sh with platform flags) |

---

## 18. Open Questions

1. **Self-reporting reliability** — Goose's instruction-injected self-reporting may miss 20-40% of events. Is this acceptable, or should Goose users be warned that SIO is "best effort" on that platform?
2. **Cross-platform learning** — Could a skill optimization on Claude Code (e.g., "search Graphiti before web search") be transferred to Gemini CLI? The prompt structure differs, but the INTENT is the same. Could DSPy handle cross-platform transfer learning?
3. **Multi-user learning** — Could SIO share anonymous success patterns across users? (Federated behavior optimization)
4. **Optimization runtime** — Run locally or on a remote "optimizer server"? GEPA can take minutes.
5. **Temporal weighting** — User gives `1` today, `0` tomorrow for same behavior. Newer signal should win.
6. **Proactive optimization** — Should SIO autonomously run optimization when it detects satisfaction rate dropping? Or always wait for user trigger?
7. **Script modification depth** — How deep should SIO go into Python/Rust/Node scripts? Just parameters, or actual logic changes?
8. **Cross-session continuity** — How to handle optimization when user's needs change over weeks/months?
9. **Community skill registry** — Could optimized SKILL.md / extension / recipe files be shared like packages?
10. **OpenCode's dynamic system.transform** — This is genuinely more powerful than static instructions files. Should all platforms aspire to dynamic injection, or is static acceptable?
11. **Goose MCP Sampling** — Can the optimization pipeline leverage Goose's unique MCP Sampling to run DSPy sub-queries more efficiently than API calls?

---

## 19. The Recursive Vision

```
Week 1:  AI makes mistakes              → User labels 0s
Week 4:  SIO optimizes from 0s          → Fewer mistakes
Week 8:  Fewer mistakes                  → Higher satisfaction rate
Week 12: System proposes new skills      → Capabilities expand
Week 14: Second platform adapter ships   → Multi-platform coverage
Week 16: SIO optimizes ITSELF           → Meta-optimization
         (The /optimize-skill skill optimizes its own SKILL.md)
Week 20: SIO cross-pollinates            → Insight from one platform improves another

The organism doesn't just get better at coding.
It gets better at getting better.
On every platform the user works on.
```

---

*This document is a living artifact. It will be optimized by SIO itself.*
