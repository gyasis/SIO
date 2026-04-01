# Research: SIO Competitive Enhancement

**Branch**: `001-competitive-enhancement` | **Date**: 2026-04-01
**Source**: `research/competitive-landscape-self-improving-agents.md` (375 lines, 10+ tool profiles)

## Research Summary

All technical unknowns from the plan's Technical Context have been resolved. The competitive landscape research document provided comprehensive coverage of implementation patterns, hook protocols, and academic foundations.

## Decisions

### D1: JSONL Metadata Fields Available for Extraction

**Decision**: Extract `usage` (input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens), `costUsd`, `stopReason`, `isSidechain`, `parentUuid`, `model`, `inferenceGeo` from assistant messages.
**Rationale**: The PRD's data source table (line 40) identifies these fields as present in Claude Code JSONL but not currently extracted. The existing `jsonl_parser.py` extracts only role, content, tool_name, tool_input, tool_output, error, and timestamp — all other fields are discarded during parsing.
**Alternatives considered**: (1) Using SpecStory markdown instead — rejected because SpecStory conversion loses structured metadata. (2) Extracting only token counts — rejected because cost tracking and sidechain identification are high-value for downstream features.

### D2: Positive Signal Detection Approach

**Decision**: Use compiled regex patterns (7+) matching natural language confirmations, coordinated with existing `_POSITIVE_KEYWORDS` in `flow_extractor.py`.
**Rationale**: `claude-reflect` (871 stars) demonstrates that keyword-based positive signal capture is effective without LLM inference. The existing `flow_extractor.py` already has `_POSITIVE_KEYWORDS` regex (line 32) — the new `positive_extractor.py` must share or extend these patterns rather than creating a parallel set.
**Alternatives considered**: (1) LLM-based classification — rejected per spec assumption (keyword-based is adequate). (2) Binary thumbs-up only — rejected because different signal types (confirmation, gratitude, implicit approval) provide richer data for velocity tracking.

### D3: Sentiment Scoring Method

**Decision**: Keyword-based scoring on a -1.0 to +1.0 scale using positive/negative keyword frequency ratios. No LLM inference.
**Rationale**: The spec explicitly states keyword-based classification. Frustration detection requires a continuous signal (3+ consecutive negative scores), which binary signals cannot detect. This score is internal analytics, not a feedback signal — it does not violate Constitution Principle III (Binary Signals) because it's not used for optimization input.
**Alternatives considered**: (1) VADER sentiment — rejected because it adds an external dependency for minimal gain over domain-specific keywords. (2) Binary positive/negative only — rejected because frustration escalation requires gradient detection.

### D4: Temporal Decay Function

**Decision**: Linear decay within bands: Fresh (0-14 days, multiplier 1.0), Cooling (15-28 days, linear from 1.0 to 0.6), Stale (29+ days, linear from 0.6 to floor 0.3).
**Rationale**: The `claude-reflect` system uses exponential decay which can be too aggressive. The PRD specifies bands with 20% per week loss. Linear decay within bands is simpler to reason about and debug. The existing `confidence.py` uses a 3-factor weighted formula — temporal decay becomes a 4th multiplicative factor.
**Alternatives considered**: (1) Exponential decay — rejected because it undervalues patterns that reappear after a gap. (2) Step function — rejected because abrupt transitions create artifacts in velocity charts.

### D5: Pattern Grading Thresholds

**Decision**: emerging (2+ occurrences, 2+ sessions) → strong (3+ occurrences, 3+ sessions) → established (5+ occurrences, consistent 7+ days) → declining (confidence below 0.5).
**Rationale**: Directly from Claude Diary's proven heuristic (2x=pattern, 3x=strong) extended with SIO's multi-session awareness. Constitution Principle III requires pattern thresholds before action — this grading enforces that. "Strong" patterns auto-generate suggestions per FR-020.
**Alternatives considered**: (1) Single threshold (5 occurrences) — rejected because it delays actionable suggestions too long. (2) Continuous confidence only — rejected because discrete grades are clearer for user communication.

### D6: Hook Implementation Pattern

**Decision**: Use Claude Code's command-type hooks (Python scripts). Each hook exits 0 with stdout data injection. On failure: retry once, then fail silent with local logging (per clarification Q3).
**Rationale**: The existing `post_tool_use.py` provides the reference pattern. The competitive research (Section 4.1-4.3) documents the full hook protocol: exit 0 = proceed, exit 2 = block, other = proceed with stderr logged. All new hooks are non-blocking (exit 0 always).
**Alternatives considered**: (1) HTTP hooks — rejected because they require a running server process. (2) Agent hooks — rejected because they're heavyweight for telemetry capture.

### D7: Experiment Branch Strategy

**Decision**: Use git worktrees (`git worktree add`) for experiment isolation rather than full branch switches.
**Rationale**: Worktrees allow concurrent experiments without disrupting the user's working directory. The existing arena modules (regression.py, drift_detector.py) can run against worktree paths. Max 3 concurrent experiments means max 3 worktrees.
**Alternatives considered**: (1) Branch checkout — rejected because it disrupts the user's working state. (2) File-copy isolation — rejected because it loses git history and diff capability.

### D8: Autonomous Loop Execution Model

**Decision**: Foreground process with configurable cycle interval (default 30 minutes). Pauses for human approval before promotion. Emergency stop via `sio autoresearch --stop` (writes a stop file checked at cycle start).
**Rationale**: The PRD specifies a local background process, but the clarification (Q1) requires human approval for promotion. A foreground process is simpler and ensures the user can see the approval prompt. The stop mechanism uses a file sentinel rather than signal handling for reliability.
**Alternatives considered**: (1) Daemon with IPC — rejected per YAGNI (Constitution VII). (2) Cron-based — rejected because it can't maintain state between cycles.

### D9: HTML Report Generation

**Decision**: Self-contained HTML with embedded CSS and inline JS (Chart.js via CDN fallback to embedded). No build step. Generated from Python string templates.
**Rationale**: The `/insights` competitor generates HTML reports — this is the proven UX pattern. Embedded assets ensure offline viewing. Python f-strings or `string.Template` avoid adding a template engine dependency.
**Alternatives considered**: (1) Jinja2 templates — rejected because it adds a dependency for a single file. (2) CLI-only Rich tables — rejected because they can't show charts or be shared.

### D10: Instruction Budget Measurement Unit

**Decision**: Count non-blank, non-comment lines in markdown files. Comments (lines starting with `<!--` through `-->`) and blank lines are excluded from the count.
**Rationale**: The PRD says "lines" but raw line count penalizes well-formatted files with spacing. Counting meaningful content lines is fairer and aligns with the instruction budget research finding that LLMs process ~150-200 distinct instructions.
**Alternatives considered**: (1) Character count — rejected because it penalizes descriptive rules over terse ones. (2) Token count — rejected because it requires tokenizer setup and varies by model.

### D11: Semantic Similarity Backend

**Decision**: Reuse existing `_get_backend()` singleton from `pattern_clusterer.py` (FastEmbed). Same embedding model for all similarity operations (deduplication, consolidation, delta writing).
**Rationale**: FastEmbed is already a project dependency. The singleton pattern prevents multiple model loads. Using the same embedding space ensures consistent similarity scores across all features.
**Alternatives considered**: (1) Separate embedding model for rules vs patterns — rejected because it complicates threshold tuning. (2) TF-IDF for rule similarity — rejected because rules have small vocabularies where TF-IDF underperforms.
