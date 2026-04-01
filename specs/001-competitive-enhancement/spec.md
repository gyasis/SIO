# Feature Specification: SIO Competitive Enhancement

**Feature Branch**: `001-competitive-enhancement`  
**Created**: 2026-04-01  
**Status**: Draft  
**Input**: PRD-competitive-enhancement.md — Import best features from 10+ self-improving agent tools

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Complete Session Data Extraction (Priority: P1)

As a developer using SIO, I want the system to extract all available metadata from my coding sessions — including token usage, costs, cache efficiency, and sub-agent activity — so that I have a complete picture of my AI-assisted development workflow instead of the current ~35% extraction rate.

**Why this priority**: This is foundational. Every other feature (metrics, velocity tracking, anomaly detection, reporting) depends on having complete session data. Without comprehensive extraction, downstream features operate on incomplete information.

**Independent Test**: Run `sio mine` on a session file and verify that token counts, costs, cache ratios, stop reasons, model identifiers, and inter-message timing are all captured in the output. Confirm the extraction rate exceeds 90% of available fields.

**Acceptance Scenarios**:

1. **Given** a session file containing token usage data, **When** the user runs the mining command, **Then** per-message token counts (input, output, cache read, cache create) are extracted and stored
2. **Given** a session file with cost data, **When** the user mines it, **Then** per-message and per-session cost totals are computed and persisted
3. **Given** a session file containing sub-agent (sidechain) messages, **When** the user mines it, **Then** sidechain messages are identified and flagged separately from main conversation messages
4. **Given** a session already mined, **When** the user runs mining again on the same file, **Then** the system skips the already-processed session and reports it as such

---

### User Story 2 - Positive Signal and Sentiment Capture (Priority: P1)

As a developer, I want SIO to recognize when my coding assistant does something well — not just when it makes mistakes — so that the system reinforces effective behaviors and gives me a balanced view of assistant performance.

**Why this priority**: SIO currently only captures errors, creating a negatively biased feedback loop. Capturing positive signals (confirmations, approvals, gratitude) is essential for accurate learning velocity measurement and balanced rule generation.

**Independent Test**: Mine a session where the user says "perfect, that's exactly right" after a tool execution and verify a positive signal record is created with the correct signal type and context.

**Acceptance Scenarios**:

1. **Given** a session where the user responds "yes exactly" after a tool call, **When** mined, **Then** a positive signal of type "confirmation" is recorded along with what the assistant did to earn it
2. **Given** a session where the user says "thanks, great work", **When** mined, **Then** a positive signal of type "gratitude" is recorded
3. **Given** a session where the user approves 8 of 10 tool calls but rejects 2, **When** mined, **Then** per-tool approval rates are computed (80% overall, with per-tool breakdown)
4. **Given** a session where the user makes 3+ consecutive corrections with escalating frustration, **When** mined, **Then** the session is flagged with a frustration escalation warning and sentiment trajectory is recorded

---

### User Story 3 - Learning Velocity Tracking (Priority: P2)

As a developer, I want to see measurable evidence that rules applied through SIO actually reduce errors over time, so I can trust the system's recommendations and understand which rules are most effective.

**Why this priority**: This is the "prove it works" feature. Without velocity tracking, users have no quantitative evidence that SIO's self-improvement loop produces real results. This builds confidence and identifies ineffective rules for removal.

**Independent Test**: Apply a rule targeting a specific error type, mine 5+ subsequent sessions, and verify that the velocity report shows a measurable decrease in that error type's frequency.

**Acceptance Scenarios**:

1. **Given** a rule was applied 7 days ago targeting "unused import" errors, **When** the user checks learning velocity, **Then** the system shows error frequency for that type over a rolling window with a trend line
2. **Given** an applied rule that has NOT reduced its target error type after 5 sessions, **When** the user checks velocity, **Then** the system flags the rule as ineffective with a recommendation for review
3. **Given** no rules have been applied yet, **When** the user checks velocity, **Then** the system shows baseline error rates per type and explains that velocity tracking begins after the first rule application

---

### User Story 4 - Instruction Budget Management (Priority: P2)

As a developer, I want SIO to prevent my instruction files from growing unbounded, automatically consolidating similar rules and blocking additions that exceed a configurable budget, so my AI assistant's instructions remain focused and effective.

**Why this priority**: Unbounded rule growth is a known failure mode — instruction files become bloated, contradictory, and degrade assistant performance. Budget enforcement and deduplication keep the system healthy as it self-improves.

**Independent Test**: Set an instruction file budget to 50 lines, attempt to apply a new rule when at 49 lines, and verify consolidation is triggered. Attempt again at the cap and verify the application is blocked with a clear budget warning.

**Acceptance Scenarios**:

1. **Given** an instruction file at 45/50 line budget, **When** the user applies a new 8-line rule, **Then** the system triggers consolidation, merging semantically similar existing rules to make room
2. **Given** an instruction file at the budget cap with no consolidation possible, **When** the user attempts to apply a rule, **Then** the application is blocked with a message explaining the budget constraint and suggesting manual review
3. **Given** two near-identical rules in an instruction file, **When** the user runs deduplication, **Then** the system proposes a single merged rule and shows the diff
4. **Given** a new rule that overlaps with an existing rule (>80% semantic similarity), **When** the user applies the new rule, **Then** the existing rule is updated in place rather than a duplicate appended

---

### User Story 5 - Rule Violation Detection (Priority: P2)

As a developer, I want SIO to detect when my AI assistant violates rules that are already in its instruction files, so I can identify enforcement failures and strengthen weak rules.

**Why this priority**: A rule that exists but gets violated is more concerning than a gap with no rule. Violations indicate the rule is poorly written, too vague, or the assistant is ignoring it — all actionable insights.

**Independent Test**: Add a rule "never use SELECT *" to the instruction file, mine a session where the assistant used SELECT *, and verify the violation is flagged with higher priority than ordinary new patterns.

**Acceptance Scenarios**:

1. **Given** an instruction file containing "never use SELECT *", **When** a mined session shows the assistant used SELECT *, **Then** a violation is flagged with the specific rule text and session evidence
2. **Given** multiple violations of different rules, **When** the user views violations, **Then** they are sorted by frequency and recency, with repeat offenders highlighted
3. **Given** no violations detected, **When** the user checks violations, **Then** the system confirms all rules are being followed with the date range checked

---

### User Story 6 - Confidence Decay and Pattern Grading (Priority: P2)

As a developer, I want patterns that haven't been seen recently to lose confidence over time, and patterns that recur consistently to be automatically promoted through a lifecycle, so the system self-prunes stale patterns and elevates reliable ones.

**Why this priority**: Without decay, old patterns accumulate indefinitely. Without grading, all patterns are treated equally regardless of how consistently they appear. Together, these create a self-maintaining pattern inventory.

**Independent Test**: Create a pattern, wait 30+ days without it recurring, and verify its confidence has decayed. Create another pattern that recurs 3+ times across 3+ sessions and verify it auto-promotes to "strong" grade.

**Acceptance Scenarios**:

1. **Given** a pattern last seen 30 days ago, **When** confidence is recalculated, **Then** the score reflects temporal decay (reduced by ~40-70% from original)
2. **Given** a pattern seen today, **When** confidence is recalculated, **Then** the score is unaffected by decay
3. **Given** a pattern seen 3 times across 3 different sessions, **When** grading runs, **Then** the pattern promotes to "strong" and a suggestion is automatically generated
4. **Given** a pattern with confidence decayed below 0.5, **When** grading runs, **Then** the pattern is marked as "declining"

---

### User Story 7 - Real-Time Session Hooks (Priority: P3)

As a developer, I want SIO to capture data at key moments during my coding session — when context is compacted, when I submit a prompt, and when the session ends — so that corrections, session metrics, and high-confidence patterns are captured in real-time rather than only during post-hoc mining.

**Why this priority**: Post-hoc mining misses real-time context. Hooks at compaction time capture the richest data (full context before compression). Session-end hooks finalize metrics. Prompt-submit hooks detect corrections immediately.

**Independent Test**: Install hooks, run a coding session, trigger compaction, type a correction, and end the session. Verify each hook fired and captured its respective data.

**Acceptance Scenarios**:

1. **Given** hooks are installed, **When** context compaction occurs, **Then** a snapshot of current session metrics and recent positive signals is captured before compression
2. **Given** hooks are installed, **When** the user types "no that's wrong, do X instead", **Then** the prompt hook detects the correction and increments the session correction counter before the assistant processes the message
3. **Given** hooks are installed, **When** the session ends, **Then** session metrics are finalized and high-confidence patterns (>0.8) are auto-saved to the learned skills directory
4. **Given** the user runs the install command, **When** installation completes, **Then** all three hooks are registered in the appropriate settings file

---

### User Story 8 - Automated Validation and Experimentation (Priority: P3)

As a developer, I want SIO to automatically test rules before permanently applying them — creating isolated experiments, running pass/fail assertions, and rolling back failures — so I can trust that applied rules actually improve my workflow.

**Why this priority**: Untested rules can degrade performance. Git-backed experiments with binary assertions create a scientific approach to self-improvement. The autonomous loop extends this to unattended optimization.

**Independent Test**: Apply a rule via the experiment path, verify a separate branch is created, run assertions against subsequent sessions, and verify the rule is either promoted (pass) or rolled back (fail).

**Acceptance Scenarios**:

1. **Given** a suggestion ready for testing, **When** the user applies it as an experiment, **Then** the rule is applied on an isolated branch separate from the main working configuration
2. **Given** an active experiment, **When** assertion checks run after sufficient sessions, **Then** the experiment passes if the target error rate decreased and no new regressions appeared
3. **Given** a failed experiment, **When** assertions fail, **Then** the experiment branch is removed and the suggestion is marked as "failed_experiment"
4. **Given** the autonomous optimization loop is running, **When** a cycle completes, **Then** exactly one suggestion is tested per cycle with a maximum of 3 concurrent experiments, and a transaction log records all actions
5. **Given** an anomalous session (e.g., 10x normal error rate), **When** anomaly detection runs, **Then** the session is flagged for manual review before being used in validation

---

### User Story 9 - Interactive Reporting (Priority: P3)

As a developer, I want to generate a visual report showing session metrics trends, error patterns, learning velocity, and copy-paste-ready rule suggestions, so I can quickly understand SIO's analysis without reading raw data.

**Why this priority**: Usability feature that makes all other capabilities accessible. Without reporting, users must interpret raw database queries. A visual report with actionable suggestions completes the user experience.

**Independent Test**: Run the report command, verify an interactive report is generated showing metrics charts, pattern tables with confidence visualization, and suggestion cards with a copy action.

**Acceptance Scenarios**:

1. **Given** at least 5 mined sessions exist, **When** the user generates a report, **Then** a standalone visual report is produced showing session metrics trends over 30 days
2. **Given** patterns with confidence scores and decay data, **When** the report is generated, **Then** each pattern shows its confidence level and decay status visually
3. **Given** approved suggestions exist, **When** the report is generated, **Then** each suggestion has a copy-ready format that can be pasted directly into instruction files
4. **Given** learning velocity data exists, **When** the report is generated, **Then** a trend visualization shows error rate changes per type over time

---

### Edge Cases

- What happens when a session file is corrupted or has malformed entries? The system skips malformed entries with a warning, processes valid ones, and reports the skip count.
- What happens when two mining processes run concurrently on the same file? The processed-sessions tracker prevents duplicate processing; the second process detects the file is already being processed and skips it.
- What happens when the instruction budget is set to a value lower than the current file length? The system warns the user and suggests running consolidation/deduplication, but does not delete existing rules automatically.
- What happens when the autonomous loop encounters a system error mid-cycle? The transaction log captures the failure point, the current experiment is paused (not deleted), and the loop halts with an error report.
- What happens when a session has zero tool calls and only conversational messages? The session is filtered out by the smart filtering rule (minimum 2 tool calls) and not processed for tool-specific metrics.
- What happens when all patterns have decayed below the confidence floor? The system retains them at the floor value (0.3) indefinitely and reports them as "stale" in the velocity and grading views.
- What happens when semantic consolidation merges rules incorrectly? The delta-based writer tracks all merges, and the user can review/revert any merge via the transaction history.

## Requirements *(mandatory)*

### Functional Requirements

**Data Extraction & Session Tracking**

- **FR-001**: System MUST extract token usage (input, output, cache read, cache create), per-message cost, stop reason, sub-agent flag, and model identifier from each session message
- **FR-002**: System MUST compute derived metrics per session: inter-message latency, cache hit ratio, total token count, total cost, session duration
- **FR-003**: System MUST track which session files have been processed (by file path and content hash) and skip already-processed files on subsequent runs
- **FR-004**: System MUST store per-session aggregate metrics: total tokens, total cost, cache efficiency, duration, error count, correction count, positive signal count, sub-agent count, stop reason distribution
- **FR-005**: System MUST filter out sessions with fewer than 5 messages or fewer than 2 tool calls
- **FR-006**: System MUST support excluding sub-agent (sidechain) data from metrics via a user-controlled flag

**Positive Signal & Sentiment Analysis**

- **FR-007**: System MUST detect positive user signals including explicit confirmations, gratitude expressions, implicit approvals (short positive response after tool execution), and session-level success indicators
- **FR-008**: System MUST use pattern-matching rules (7+ patterns) to classify positive signals by type: confirmation, gratitude, implicit approval, session success
- **FR-009**: System MUST store each positive signal with its type, source text, preceding context (what the assistant did), and associated tool name
- **FR-010**: System MUST detect user approval vs rejection of tool calls based on the user's response following each tool execution
- **FR-011**: System MUST compute per-tool approval rates across sessions
- **FR-012**: System MUST assign a sentiment score (-1.0 to +1.0) to each user message using keyword-based classification
- **FR-013**: System MUST detect frustration escalation when 3 or more consecutive user messages have negative sentiment scores, and flag the session accordingly

**Learning Velocity & Confidence**

- **FR-014**: System MUST compute error frequency per error type over a configurable rolling time window
- **FR-015**: System MUST measure correction decay rate — how quickly errors of a given type decrease after a rule targeting that type is applied
- **FR-016**: System MUST report adaptation speed — the number of sessions until an error type drops below a configurable threshold after rule application
- **FR-017**: System MUST apply temporal decay to pattern confidence scores: patterns not observed for 14+ days lose confidence progressively, with a configurable floor (default 0.3)
- **FR-018**: System MUST define decay bands: Fresh (0-14 days, no decay), Cooling (15-28 days, moderate decay), Stale (29+ days, significant decay to floor)
- **FR-019**: System MUST grade patterns through a lifecycle: emerging (2+ occurrences across 2+ sessions) to strong (3+ occurrences across 3+ sessions) to established (5+ occurrences, consistent over 7+ days) to declining (confidence below 0.5)
- **FR-020**: System MUST auto-generate suggestions for patterns that reach "strong" grade without requiring manual trigger

**Instruction Budget & Rule Management**

- **FR-021**: System MUST count current lines in the target instruction file before applying any new rule
- **FR-022**: System MUST enforce configurable line caps per instruction file (default: 100 lines for primary file, 50 lines for supplementary files)
- **FR-023**: System MUST trigger semantic consolidation (merging similar rules) when a new rule would exceed the budget, rather than simply appending
- **FR-024**: System MUST block rule application when consolidation still cannot bring the file within budget, and report the constraint to the user
- **FR-025**: System MUST report per-file instruction budget usage (current lines / cap) on demand
- **FR-026**: System MUST parse existing instruction file rules into structured constraints and compare mined errors against them to detect violations
- **FR-027**: System MUST flag rule violations at higher priority than new patterns, since violations indicate enforcement failures
- **FR-028**: System MUST identify semantically similar rules (>85% similarity) across all instruction files and propose consolidated versions
- **FR-029**: System MUST use delta-based rule writing: if a new rule overlaps an existing rule (>80% similarity), update the existing rule in place rather than appending a duplicate
- **FR-030**: System MUST track the type of each rule change (merge vs append) in the change history

**Lifecycle Hooks**

- **FR-031**: System MUST provide a pre-compaction hook that captures a snapshot of current session metrics and recent positive signals before context compression occurs
- **FR-032**: Pre-compaction hook MUST never block the compaction process
- **FR-033**: System MUST provide a session-end hook that finalizes session metrics, runs lightweight pattern detection, and auto-saves high-confidence patterns (>0.8 threshold) to the learned skills directory
- **FR-034**: System MUST provide a prompt-submit hook that detects corrections, undo requests, and frustration escalation in the user's message before the assistant processes it
- **FR-035**: Prompt-submit hook MUST never block the user's message from being processed
- **FR-036**: System MUST register all lifecycle hooks through a single installation command

**Automated Validation & Experimentation**

- **FR-037**: System MUST support binary pass/fail assertion checks including: error rate decreased, no new regressions introduced, confidence above threshold, budget within limits, no rule collisions
- **FR-038**: System MUST support user-defined custom assertions via configuration
- **FR-039**: System MUST create isolated experiment branches before applying rules, keeping them separate from the user's main configuration
- **FR-040**: System MUST automatically validate experiments after a configurable number of sessions by running assertions
- **FR-041**: Experiments that pass validation MUST be promoted to the main configuration; experiments that fail MUST be rolled back and marked as "failed_experiment"
- **FR-042**: System MUST support an autonomous optimization loop that cycles through: mine, cluster, grade, generate, assert, experiment, validate, promote/rollback
- **FR-043**: Autonomous loop MUST enforce safety limits: maximum 3 concurrent experiments, maximum 1 new rule per cycle, budget enforcement on every application
- **FR-044**: Autonomous loop MUST maintain an append-only transaction log of all actions taken
- **FR-045**: Autonomous loop MUST support immediate stop via user command
- **FR-046**: System MUST detect statistically anomalous sessions using deviation-based analysis and flag them for manual review

**Reporting**

- **FR-047**: System MUST generate a standalone visual report (no external dependencies required to view)
- **FR-048**: Report MUST include: session metrics dashboard, error trend visualization (30-day rolling), pattern table with confidence and decay status, copy-ready suggestion cards, learning velocity trends
- **FR-049**: System MUST generate qualitative session summaries categorized as: tool mastery, error-prone area, user satisfaction, session complexity
- **FR-050**: Session facets MUST be cached by session file content hash to avoid recomputation

### Key Entities

- **Session**: A single coding session identified by file path and content hash; has aggregate metrics (tokens, cost, duration, error/positive counts)
- **Positive Signal**: A detected instance of user approval or satisfaction; classified by type (confirmation, gratitude, implicit approval, session success); linked to the assistant action that triggered it
- **Sentiment Score**: A per-message numerical rating (-1.0 to +1.0) of user mood; used to detect frustration escalation trajectories
- **Velocity Snapshot**: A point-in-time measurement of error frequency per type within a rolling window; used to track learning over time
- **Pattern Grade**: A lifecycle stage (emerging, strong, established, declining) assigned to each error pattern based on recurrence and recency
- **Instruction Budget**: A per-file line count constraint with current usage; governs whether new rules can be applied or consolidation is needed
- **Rule Violation**: An instance where a mined error matches a prevention rule already in the instruction file; flagged at higher priority than new patterns
- **Experiment**: An isolated test of a proposed rule with binary assertions; tracks status (active, passed, failed) and links to the transaction log
- **Transaction Log Entry**: An immutable record of an autonomous loop action (mine, apply, promote, rollback) with timestamp and outcome
- **Session Facet**: A qualitative summary of a session's character (e.g., tool mastery, error-prone area); cached for reuse

## Assumptions

- Session files follow the existing JSONL format used by the target coding assistant platform, with `usage`, `costUsd`, `stopReason`, and `isSidechain` fields available in assistant messages
- The existing embedding library (FastEmbed) is sufficient for semantic similarity comparisons used in consolidation and deduplication
- Keyword-based sentiment scoring is adequate for detecting frustration escalation; LLM-based sentiment analysis is not required for this feature
- The "learned skills" directory for auto-saved patterns already exists or will be created on first use
- Git is available on the user's system for experiment branching
- The autonomous loop runs as a local background process, not a daemon or service
- Instruction file format is line-oriented markdown with one rule per logical block

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Session data extraction covers >90% of available metadata fields (up from ~35%)
- **SC-002**: System captures both positive signals and errors, with positive signals detected in >80% of sessions that contain user approval language
- **SC-003**: Learning velocity is quantifiable: after applying a rule, the targeted error type's frequency shows a measurable trend within 5 sessions
- **SC-004**: Instruction files remain within configured budget caps, with automatic consolidation preventing unbounded growth
- **SC-005**: The autonomous optimization loop completes 10+ consecutive cycles without human intervention or system failure
- **SC-006**: Rules that fail to reduce their targeted error rate within the configured validation window are automatically rolled back
- **SC-007**: Visual reports are generated as standalone files viewable without additional tools, containing actionable copy-ready suggestions
- **SC-008**: All three lifecycle hooks fire at their designated moments and capture data without blocking the user's workflow
- **SC-009**: Anomalous sessions (>3 standard deviations from median on key metrics) are detected and flagged before being used in validation
- **SC-010**: Pattern grading correctly promotes recurring patterns through the lifecycle within 3-7 sessions of consistent observation
