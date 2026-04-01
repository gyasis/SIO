# PRD: SIO Competitive Enhancement — Import Best Features from 10+ Self-Improving Agent Tools

## Problem Statement

SIO is a production-grade self-improving system for Claude Code with 15K+ LOC, 14 SQLite tables, and a closed-loop pipeline. However, a comprehensive competitive analysis of 10+ tools in the self-improving coding agent space reveals **31 features SIO lacks entirely**, **4 features where competitors outperform SIO**, and a critical data extraction gap: **SIO extracts only ~35-40% of available metadata from Claude Code JSONL session files**.

Meanwhile, tools like claude-reflect (871 GitHub stars), GuideMode (119 session metrics), and the AutoResearch Loop (overnight autonomous optimization) have pioneered capabilities that SIO should absorb to maintain its position as the most advanced self-improving agent platform.

**Research backing**: See `research/competitive-landscape-self-improving-agents.md` (30+ articles, 10 GitHub repos, academic papers, Gemini deep research) and `research/implementation-specs-detailed.md` (821-line detailed implementation specs).

## Vision

SIO becomes the definitive self-improving agent platform by absorbing the best features from every competitor — positive signal capture, real-time hooks, learning velocity tracking, instruction budget management, autonomous optimization loops, and interactive reporting — while maintaining its existing advantages (multi-session clustering, DSPy optimization, arena validation, Graphiti integration).

## Core Principle

> **Import, don't imitate.** Take the technical essence of each competitor's best feature and integrate it into SIO's existing architecture. Don't build parallel systems — extend what exists.

## Competitive Sources

| Tool | Stars/Users | Key Innovation | SIO Gap |
|------|------------|----------------|---------|
| claude-reflect (BayramAnnakov) | 871 | Real-time correction capture + positive feedback + temporal decay | SIO only captures errors, not successes; no temporal confidence decay |
| Claude Diary (rlancemartin) | 200+ | PreCompact hook + pattern grading (2x/3x heuristic) + violation detection | SIO has no PreCompact hook; no pattern promotion; no violation checking |
| GuideMode | SaaS | 119 session metrics + learning velocity tracking | SIO tracks zero quantitative session metrics |
| AutoResearch Loop | Research | Binary assertions + unattended overnight optimization + git rollback | SIO has no autonomous loop; everything requires human invocation |
| /insights (Anthropic built-in) | Official | Haiku facet extraction + HTML report + session caching | SIO is CLI-only; no qualitative summaries; no session caching |
| continuous-learning | Community | Stop hook + auto-save to skills/ | SIO has no session-end hook |
| Bootstrap Seed (ChristopherA) | Gist | Anti-proliferation guardrails (<100 lines cap) + consolidation triggers | SIO can append rules indefinitely with no budget check |
| bokan/self-improvement | 100+ | .learnings/ directory + pre-task skill consultation | SIO doesn't check learnings before major tasks |
| claude-reflect-system (haddock) | Community | Continual learning with severity taxonomy + cross-skill learning | SIO has no severity levels on patterns |
| Addy Osmani patterns | Blog | Planner-Worker-Judge concurrency + four memory channels | Architectural pattern, not direct feature |

## Data Sources

All existing SIO data sources plus newly extracted fields:

| Source | Location | Currently Extracted | Needs Extraction |
|--------|----------|-------------------|-----------------|
| Claude JSONL | `~/.claude/projects/*/*.jsonl` | role, content, tool_name, tool_input, tool_output, error, timestamp | **input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, cost_usd, stop_reason, isSidechain, parentUuid, model, inference_geo** |
| SpecStory markdown | `~/.specstory/history/*.md` | role, content, tool_calls | **plan_mode flag, context_window utilization, session_duration** |
| PostToolUse hook | Real-time telemetry | tool invocations, passive signals (3 types) | **approval/rejection signals, frustration trajectory, positive feedback** |

## Feature Requirements

### FR-001: Enhanced JSONL Parser — Token, Cost, and Metadata Extraction
- Extract `usage` object from each assistant message: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`
- Extract `costUsd` field for per-message cost tracking
- Extract `stop_reason` ("end_turn" vs "max_tokens") for context exhaustion detection
- Extract `isSidechain` boolean for sub-agent identification
- Extract `model` identifier per message
- Compute inter-message latency from timestamp diffs
- Compute `cache_hit_ratio = cache_read / (cache_read + input_tokens)` per session
- **Files affected**: `src/sio/mining/jsonl_parser.py`
- **Acceptance**: Parse a real JSONL session → all new fields populated in returned records

### FR-002: Session Metrics Table and Processed Session Tracking
- New `session_metrics` table storing per-session aggregates: total tokens, cost, cache efficiency, duration, error/correction/positive counts, plan mode ratio, sidechain count, stop reason distribution
- New `processed_sessions` table tracking file_path + file_hash to prevent re-mining the same session
- Pipeline integration: `run_mine()` checks processed_sessions before parsing; inserts session_metrics after parsing
- Smart filtering: skip sessions with <5 messages or <2 tool calls (from /insights pattern)
- Sub-agent filtering: `--exclude-sidechains` flag to separate sub-agent metrics
- **Files affected**: `src/sio/core/db/schema.py`, `src/sio/mining/pipeline.py`
- **Acceptance**: Run `sio mine` twice on same files → second run skips already-processed sessions; session_metrics table populated with token/cost data

### FR-003: Positive Signal Extraction
- New module parallel to `error_extractor.py` for detecting positive user signals
- Signal types: confirmation ("yes exactly", "perfect", "that's right"), gratitude ("thanks", "great work"), implicit approval (short response + no negatives after tool execution), session success (ends with positive + no pending errors)
- 7+ compiled regex patterns matching natural language confirmations
- New `positive_records` database table (parallel to `error_records`)
- Each positive record includes: signal_type, signal_text, context_before (what agent did), tool_name
- Integration with flow_extractor's existing `_POSITIVE_KEYWORDS` to avoid duplication
- **Files affected**: NEW `src/sio/mining/positive_extractor.py`, `src/sio/core/db/schema.py`
- **Acceptance**: Run on a session with known "perfect, thanks" messages → positive_records populated with correct signal types

### FR-004: Tool Approval/Rejection Detection and Sentiment Scoring
- Detect when users approve vs reject tool calls based on response patterns after tool_use blocks
- Track approval rate per tool type (which tools get accepted vs rejected most)
- Sentiment scoring per user message on -1.0 to +1.0 scale using keyword matching (no LLM needed)
- Frustration escalation detection: 3+ consecutive negative sentiment scores → flag session
- Escalation keywords: "frustrated", "annoying", "waste of time", "just do X", "stop"
- **Files affected**: NEW `src/sio/mining/approval_detector.py`, NEW `src/sio/mining/sentiment_scorer.py`
- **Acceptance**: Run on a session where user corrects Claude 5 times → sentiment trajectory shows decline; approval rate for corrected tool below average

### FR-005: Learning Velocity Tracking
- Compute how fast Claude adapts to corrections over time for each error type
- `error_rate_per_session`: rolling 7-day window error frequency per type
- `correction_decay_rate`: after a rule is applied, how quickly do related errors decrease?
- `adaptation_speed`: number of sessions until error type drops below threshold after rule applied
- New `velocity_snapshots` database table: session_id, error_type, rate, window_start, window_end
- CLI command: `sio velocity` to display learning velocity trends
- **Files affected**: NEW `src/sio/core/metrics/velocity.py`, `src/sio/core/db/schema.py`, `src/sio/cli/main.py`
- **Acceptance**: Apply a rule via `sio apply` → mine 5+ subsequent sessions → velocity shows measurable decrease in related error type

### FR-006: Confidence Scoring with Temporal Decay
- Modify existing `score_confidence()` to add temporal decay factor
- Patterns not seen in 14+ days lose 20% confidence per week (floor at 0.3)
- Decay bands: Fresh (0-14 days, 1.0x), Cooling (15-28 days, 0.8x-0.6x), Stale (29+ days, 0.6x-0.3x)
- Existing rank_score and dataset_coverage factors remain
- **Files affected**: `src/sio/suggestions/confidence.py`
- **Acceptance**: Create a pattern not seen in 30 days → confidence score shows decay; create a pattern seen today → confidence unaffected

### FR-007: Pattern Grading and Promotion Mechanics
- Implement promotion lifecycle: `emerging` (seen 2x across 2+ sessions) → `strong` (3+ times, 3+ sessions) → `established` (5+ times, consistent 7+ days) → `declining` (temporal decay below 0.5)
- Auto-promote `strong` patterns to suggestion generation without human trigger
- New column `grade` on patterns table
- **Files affected**: NEW `src/sio/clustering/grader.py`, `src/sio/core/db/schema.py`
- **Acceptance**: Mine sessions containing a recurring error 3 times → pattern auto-grades to "strong" and suggestion generated

### FR-008: Instruction Budget Awareness and Anti-Proliferation
- Before applying any suggestion, count current lines in target file
- CLAUDE.md: configurable hard cap (default 100 lines)
- Rules files: configurable soft cap (default 50 lines each)
- When budget exceeded: trigger consolidation (merge semantically similar rules via FastEmbed) instead of appending
- Block application if consolidation still exceeds budget
- CLI command: `sio budget` to show per-file instruction budget usage
- **Files affected**: NEW `src/sio/applier/budget.py`, `src/sio/cli/main.py`
- **Acceptance**: Set CLAUDE.md cap to 50 lines → attempt to apply a rule when at 49 lines → consolidation triggered; apply when at 50 → blocked with budget warning

### FR-009: CLAUDE.md Violation Detection
- Parse existing CLAUDE.md rules into structured list of constraints
- For each mined error, check if it matches an existing rule's prevention criteria (the agent violated its own rule)
- Flag violations as higher priority than new patterns — these indicate rule enforcement failures
- CLI command: `sio violations` to show CLAUDE.md violation report
- **Files affected**: NEW `src/sio/mining/violation_detector.py`, `src/sio/cli/main.py`
- **Acceptance**: Add a rule "never use SELECT *" to CLAUDE.md → mine a session where Claude used SELECT * → violation flagged

### FR-010: Semantic Deduplication of Applied Rules
- Scan all rule files (CLAUDE.md, rules/*.md) for semantically similar entries using FastEmbed
- Propose consolidated versions when cosine similarity > 0.85
- CLI command: `sio dedupe` to find and consolidate duplicate rules
- Reuse `_get_backend()` singleton from `pattern_clusterer.py`
- **Files affected**: NEW `src/sio/applier/deduplicator.py`, `src/sio/cli/main.py`
- **Acceptance**: Add two near-identical rules → run `sio dedupe` → consolidation proposed with merged text

### FR-011: Delta-Based Rule Writer
- Replace pure append strategy with delta-based updates
- Before appending, parse existing rules in target file
- If new rule overlaps existing rule (FastEmbed similarity > 0.80): MERGE (update existing text)
- If no overlap: APPEND as before
- Track delta type ("merge" vs "append") in `applied_changes` table
- **Files affected**: `src/sio/applier/writer.py`
- **Acceptance**: Apply a rule similar to an existing one → existing rule updated in place rather than new rule appended

### FR-012: PreCompact Hook
- New hook handler for Claude Code's PreCompact lifecycle event
- Captures context state BEFORE compression — the richest data capture point
- Actions: save snapshot of current session metrics, trigger incremental mining, detect "what just worked" from recent positive signals
- Always returns `{"action": "allow"}` — never blocks compaction
- **Files affected**: NEW `src/sio/adapters/claude_code/hooks/pre_compact.py`
- **Acceptance**: Start a Claude Code session with hook installed → trigger compaction → verify pre_compact.py fired and session data captured

### FR-013: Stop Hook (Session End Capture)
- New hook handler for Claude Code's Stop lifecycle event
- Finalizes session_metrics entry, runs lightweight pattern detection
- Auto-saves high-confidence patterns (>0.8) to `~/.claude/skills/learned/`
- Updates processed_sessions table
- **Files affected**: NEW `src/sio/adapters/claude_code/hooks/stop.py`
- **Acceptance**: End a Claude Code session → Stop hook fires → session_metrics finalized; high-confidence pattern saved to skills/

### FR-014: UserPromptSubmit Hook (Real-Time Correction Detection)
- New hook handler for Claude Code's UserPromptSubmit lifecycle event
- Intercepts user message BEFORE Claude processes it
- Detects corrections, undos, and frustration escalation in real-time
- Increments session correction counter; logs frustration warnings
- Always returns `{"action": "allow"}` — never blocks the prompt
- **Files affected**: NEW `src/sio/adapters/claude_code/hooks/user_prompt_submit.py`
- **Acceptance**: Type "no that's wrong, do X instead" → hook detects correction and logs it before Claude processes

### FR-015: Hook Installer Update
- Register all 3 new hooks (PreCompact, Stop, UserPromptSubmit) in the SIO installer
- `sio install` sets up all hooks in `.claude/settings.json`
- **Files affected**: `src/sio/adapters/claude_code/installer.py`
- **Acceptance**: Run `sio install` → verify all 3 new hooks appear in settings.json

### FR-016: Binary Assertion Framework
- Deterministic pass/fail gates for rule testing (from AutoResearch Loop pattern)
- Built-in assertions: `error_rate_decreased`, `no_new_regressions`, `confidence_above_threshold`, `budget_within_limits`, `no_collisions`
- Custom assertion support via config
- Each assertion takes session_metrics dict, returns bool + actual_value
- **Files affected**: NEW `src/sio/core/arena/assertions.py`
- **Acceptance**: Define assertion "error_rate_decreased for tool_failure" → run against pre/post metrics → pass/fail result returned

### FR-017: Git-Backed Experiment Branches
- Before applying a rule, create `experiment/<suggestion-id>-<timestamp>` git branch
- Apply rule on experiment branch only
- After N sessions (configurable), run assertions to validate
- Pass → merge to main, delete branch. Fail → delete branch, mark suggestion as "failed_experiment"
- **Files affected**: NEW `src/sio/core/arena/experiment.py`
- **Acceptance**: Apply rule via experiment → verify git branch created → run validation → verify merge or rollback

### FR-018: AutoResearch Loop Engine
- Unattended optimization loop running on configurable cycle interval (default 30 minutes)
- Cycle: mine → cluster → grade → generate → assert → experiment → validate → promote/rollback
- Safety: max 3 active experiments, max 1 rule per cycle, budget enforcement, human gate by default
- Emergency stop: `sio autoresearch --stop`
- Append-only transaction log at `~/.sio/autoresearch.jsonl`
- Integration with existing arena: uses `regression.py`, `drift_detector.py`, `collision.py`, `gold_standards.py`
- CLI commands: `sio autoresearch start`, `sio autoresearch stop`, `sio autoresearch status`
- **Files affected**: NEW `src/sio/core/arena/autoresearch.py`, NEW `src/sio/core/arena/txlog.py`, `src/sio/cli/main.py`
- **Acceptance**: Run `sio autoresearch start` → 1 cycle completes → experiment branch created → txlog.jsonl has entries → `sio autoresearch status` shows cycle count

### FR-019: MAD Anomaly Detection
- Median Absolute Deviation statistics for detecting unusual session metrics
- Apply to: error_rate, token_usage, session_duration, cost_per_session
- Flag anomalous sessions for manual review
- **Files affected**: NEW `src/sio/core/arena/anomaly.py`
- **Acceptance**: Inject a session with 10x normal error rate → MAD flags it as anomalous

### FR-020: Interactive HTML Report
- Generate standalone HTML report (embedded CSS/JS, no external deps)
- Content: session metrics dashboard, error trend chart (30-day rolling), pattern table with confidence + decay visualization, copy-paste suggestion cards, learning velocity graph
- CLI command: `sio report --html`
- **Files affected**: NEW `src/sio/reports/html_report.py`, `src/sio/cli/main.py`
- **Acceptance**: Run `sio report --html` → opens browser with interactive report; click "copy" on suggestion → CLAUDE.md rule in clipboard

### FR-021: Qualitative Session Facets
- Generate qualitative session summaries using sub-LLM or keyword-based fallback
- Categories: "tool_mastery", "error_prone_area", "user_satisfaction", "session_complexity"
- Cache facets in `~/.sio/facets/` directory keyed by session file hash
- **Files affected**: NEW `src/sio/mining/facet_extractor.py`
- **Acceptance**: Mine a complex session → facets generated with correct categories; re-mine same session → cached facets returned

## Non-Requirements
- No new capture infrastructure — SIO reads existing JSONL/SpecStory data
- No multi-platform support in this PRD (Claude Code only; other platforms are separate PRDs)
- No UI beyond CLI and HTML report (no web server, no dashboard app)
- No cloud/SaaS component — everything runs locally

## Dependencies
- Existing SIO codebase (15K+ LOC, 14 tables, 40+ CLI commands)
- Existing arena modules: `regression.py`, `drift_detector.py`, `collision.py`, `gold_standards.py`
- Existing parsers: `jsonl_parser.py`, `specstory_parser.py`
- Existing hook: `post_tool_use.py` (reference pattern for new hooks)
- FastEmbed for embeddings (already a dependency)
- Git CLI for experiment branches

## Success Criteria
1. Session data extraction increases from ~35% to >90% of available JSONL fields
2. SIO captures both errors AND positive signals (not just errors)
3. Learning velocity is measurable and shows improvement after rules are applied
4. CLAUDE.md stays under instruction budget cap with automatic consolidation
5. AutoResearch loop can run unattended for 10+ cycles without human intervention
6. Applied rules that don't reduce error rates are automatically rolled back
7. HTML report provides actionable, copy-paste-ready improvement suggestions

## References
- `research/competitive-landscape-self-improving-agents.md` — Full competitive analysis
- `research/implementation-specs-detailed.md` — Detailed implementation specifications (821 lines)
- `~/.claude/plans/shimmying-drifting-wand.md` — Wave-based implementation plan with dependency chains
- Reflexion paper (arXiv:2303.11366) — Academic foundation for self-reflection in agents
- CoALA framework — Converting episodic memory to procedural memory
