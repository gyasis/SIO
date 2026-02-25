# Feature Specification: Self-Improving Organism (SIO)

**Feature Branch**: `001-self-improving-organism`
**Created**: 2026-02-25
**Status**: Draft
**Input**: PRD describing a self-improving feedback loop system for AI coding CLIs that captures behavior telemetry, collects user satisfaction signals, and uses prompt optimization to permanently fix recurring mistakes across multiple platforms.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Behavior Telemetry Capture (Priority: P1)

As an AI CLI user, every tool call, skill invocation, and preference application during my session is automatically recorded so that the system has data to learn from.

**Why this priority**: Without telemetry data, no other SIO capability works. This is the foundation — the "nervous system" — that feeds all downstream optimization.

**Independent Test**: Can be fully tested by running a typical CLI session with multiple tool calls and verifying that each action appears as a row in the local behavior database. Delivers value as a session activity log even without optimization.

**Acceptance Scenarios**:

1. **Given** a user is running an AI CLI session with SIO installed, **When** the AI executes any tool call, **Then** a behavior invocation record is created with the session ID, timestamp, user message, tool name, and behavior type — within 2 seconds of the tool completing.
2. **Given** a user completes a session with 20 tool calls, **When** the user queries the behavior database, **Then** all 20 invocations appear with correct metadata and no duplicates.
3. **Given** SIO is installed on a Tier 1 or Tier 2 platform, **When** telemetry logging encounters an error (e.g., disk full), **Then** the user's CLI session continues uninterrupted and the error is logged to a separate error log.

---

### User Story 2 - User Satisfaction Feedback (Priority: P1)

As an AI CLI user, I can quickly rate whether the AI's last action was helpful or not, using minimal-effort signals like `++` or `--`, so the system knows which behaviors to reinforce and which to fix.

**Why this priority**: Tied for P1 because telemetry without labels has no learning signal. The user's binary satisfaction rating is the "pain signal" that drives optimization. Without it, the system collects data but never improves.

**Independent Test**: Can be fully tested by performing an AI action, typing `++` or `--`, and verifying the corresponding satisfaction field is updated in the database. Delivers value as a personal action journal.

**Acceptance Scenarios**:

1. **Given** the AI just completed a tool call, **When** the user types `++`, **Then** the most recent invocation's satisfaction field is set to satisfied and acknowledged within 1 second.
2. **Given** the AI just completed a tool call, **When** the user types `-- should have used gemini`, **Then** the most recent invocation's satisfaction is set to unsatisfied and the note "should have used gemini" is saved.
3. **Given** a user has 15 unlabeled invocations from the current session, **When** the user invokes the batch review command, **Then** the system presents each unlabeled invocation for review, allowing the user to label them sequentially.

---

### User Story 3 - Passive Dissatisfaction Detection (Priority: P2)

As an AI CLI user, the system automatically detects when I undo, correct, or re-state my intent after an AI action, so that negative signals are captured even when I forget to explicitly rate.

**Why this priority**: Reduces the labeling burden on the user. Many failures go unrated because users just fix the problem and move on. Passive detection catches these "silent dissatisfactions" automatically.

**Independent Test**: Can be fully tested by triggering an AI action, then typing a correction like "No, actually use gemini_research", and verifying the prior invocation is flagged with a passive signal value.

**Acceptance Scenarios**:

1. **Given** the AI just executed a tool call, **When** the user's next message starts with "No," or "Actually," or "Instead,", **Then** the prior invocation is flagged with a correction passive signal for later review.
2. **Given** the AI just wrote a file, **When** the user undoes the change within 30 seconds (via git checkout, undo, or revert), **Then** the prior invocation is flagged with an undo passive signal and marked as unsatisfied.
3. **Given** the user manually invokes a different tool for the same intent (e.g., the AI used WebSearch but the user explicitly calls gemini_research), **When** the system detects overlapping intent, **Then** the prior invocation is flagged with a correction signal and the correct action field is marked as incorrect.

---

### User Story 4 - Prompt Optimization from Feedback (Priority: P2)

As an AI CLI user, when a skill or tool consistently fails, I can trigger an optimization run that permanently improves the AI's prompts so the same mistakes stop recurring.

**Why this priority**: This is the "brain" — the core value proposition. Optimization converts accumulated failure data into permanent improvements. Ranked P2 because it requires P1 telemetry and feedback data to function.

**Independent Test**: Can be fully tested by accumulating 10+ labeled failures for a specific skill, triggering the optimization command, and verifying the skill's configuration file is updated with improved instructions that produce better behavior in subsequent sessions.

**Acceptance Scenarios**:

1. **Given** a skill has 10+ labeled invocations with a satisfaction rate below 60%, **When** the user triggers the optimization command for that skill, **Then** the system generates updated prompt instructions and presents a diff of proposed changes.
2. **Given** the optimization pipeline produces new prompt instructions, **When** the user approves the changes, **Then** the updated instructions are written to the appropriate platform-native configuration file and committed to version control.
3. **Given** the optimization pipeline runs successfully, **When** the user starts a new session, **Then** the AI uses the improved prompts and the previously-failing behavior succeeds without user correction.

---

### User Story 5 - Regression Prevention via Arena Testing (Priority: P3)

As an AI CLI user, when the system proposes optimization changes, it first verifies that previously-working behaviors still work, so I never lose reliable functionality.

**Why this priority**: Prevents the optimization loop from accidentally breaking good behavior. Essential for trust, but requires the optimization pipeline (P2) to exist first.

**Independent Test**: Can be fully tested by creating gold-standard test cases from verified-good interactions, running an optimization that conflicts with one, and verifying the system blocks the harmful change.

**Acceptance Scenarios**:

1. **Given** a set of gold-standard interactions (verified satisfied), **When** a new optimization is proposed, **Then** the system replays all gold standards against the new prompts and rejects the optimization if any gold standard would break.
2. **Given** an optimization changes a skill's trigger description, **When** the Arena detects the new description is semantically too close to another skill's description (embedding distance below threshold), **Then** the system warns about potential trigger collision before allowing deployment.
3. **Given** an optimization produces a prompt whose semantic drift score exceeds 0.40 (cosine distance between original and optimized prompt embeddings), **When** the Arena evaluates the change, **Then** the system requires explicit manual approval before deployment.

---

### User Story 6 - Skill Health Dashboard (Priority: P3)

As an AI CLI user, I can view an overview of how well each skill and tool is performing so I know which behaviors need attention and how the system is improving over time.

**Why this priority**: Provides visibility into the system's health. Users need to see trends, identify problematic skills, and understand whether SIO is working. Important for trust and adoption but not for core functionality.

**Independent Test**: Can be fully tested by accumulating session data and invoking the health command to see per-skill metrics rendered in a readable format.

**Acceptance Scenarios**:

1. **Given** the user has completed multiple sessions with labeled invocations, **When** the user invokes the health dashboard command, **Then** the system displays per-skill metrics including invocation count, satisfaction rate, false trigger rate, missed trigger rate, and last optimization date.
2. **Given** a skill's satisfaction rate drops below 50%, **When** the user views the dashboard, **Then** that skill is highlighted as needing attention, along with the count of available training examples.

---

### User Story 7 - Multi-Platform Installation (Priority: P4)

As an AI CLI user who works with multiple platforms (Claude Code, Gemini CLI, OpenCode, Codex CLI, Goose), I can install SIO with a single command per platform so each gets native integration using that platform's extension model.

**Why this priority**: Extends SIO's value beyond a single platform. Important for the vision but the core feedback loop must work on one platform first (Claude Code) before expanding.

**Independent Test**: Can be fully tested by running the install command with a platform flag on a machine with that platform installed, and verifying that hooks, skills/extensions, and database are properly configured.

**Acceptance Scenarios**:

1. **Given** the user has Claude Code installed, **When** they run the platform installer with the Claude flag, **Then** SIO hooks are registered, skills are installed, the instructions file is updated with SIO rules, and a smoke test confirms the database is working.
2. **Given** the user has multiple platforms installed, **When** they run the installer in auto-detect mode, **Then** SIO detects all installed platforms and sets up native adapters for each with separate per-platform databases.
3. **Given** SIO is installed on a Tier 3 platform (Codex CLI or Goose), **When** the user views installation output, **Then** the system clearly communicates the platform's limitations (e.g., "Telemetry is best-effort on this platform. Expect 60-80% capture rate.").

---

### Edge Cases

- What happens when the behavior database file is corrupted or deleted mid-session? On DB open, run `PRAGMA integrity_check`. If it fails, rename corrupt file to `behavior_invocations.db.corrupt.{timestamp}`, create fresh DB with schema, log to error.log. Corrupt file preserved for forensics. Continue logging with no disruption.
- What happens when a user rates the same invocation twice? The most recent rating should overwrite the previous one.
- What happens when the optimization pipeline runs with insufficient labeled data (fewer than 10 examples)? The system should refuse to optimize and inform the user how many more labels are needed.
- What happens when two skills have nearly identical trigger descriptions after optimization? The Arena's collision detection should block the change and suggest differentiating the descriptions.
- What happens when a real-time correction fails (e.g., the corrected tool also fails)? The system should log both failures and not enter an infinite correction loop.
- What happens when the user's coding preferences change over time? Newer feedback signals should carry more weight than older ones during optimization.
- What happens when multiple CLI sessions write to the same database concurrently? SQLite WAL mode handles concurrent writes; each session uses a unique session_id. Optimization and health aggregates span all sessions.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST automatically record every AI tool call, skill invocation, and preference application as a row in a local database, capturing session ID, timestamp, user message, actual action taken, behavior type, and platform identifier.
- **FR-002**: System MUST allow users to label any invocation with a binary satisfaction signal (satisfied or unsatisfied) plus an optional free-text note, using a platform-appropriate quick-entry mechanism.
- **FR-003**: System MUST auto-label each invocation with agent-inferred fields — without requiring user input for these fields. Heuristics: `activated` = 1 if `tool_output` is non-null and `error` is null, else 0. `correct_action` = 1 initially (optimistic), updated to 0 retroactively when passive signal detection (look-back) detects a correction on the next invocation. `correct_outcome` = 1 if `activated=1` AND `error` is null AND `tool_output` is non-empty, else 0. User labels override all auto-labels.
- **FR-004**: System MUST detect passive dissatisfaction signals (undos within 30 seconds, correction language in follow-up messages, manual re-invocation of a different tool for the same intent) on platforms that support hook-based interception.
- **FR-005**: System MUST maintain a per-skill/tool health aggregate that tracks total invocations, satisfied count, unsatisfied count, unlabeled count, false trigger count, missed trigger count, satisfaction rate, and last optimization date.
- **FR-006**: System MUST support a batch review mode where the user can sequentially label all unlabeled invocations from recent sessions.
- **FR-007**: System MUST support triggering a prompt optimization pipeline for a specific skill or tool when sufficient labeled data exists (minimum 10 examples), producing updated prompt instructions as platform-native configuration diffs.
- **FR-008**: System MUST present optimization results as reviewable diffs that the user can approve, reject, or review individually before any changes are applied.
- **FR-009**: System MUST commit every approved optimization change to version control with a descriptive message including the behavior ID, platform, optimizer used, and example count.
- **FR-010**: System MUST maintain a set of gold-standard test cases (verified-good interactions) and replay them against proposed optimizations, rejecting any optimization that would break a gold standard.
- **FR-011**: System MUST detect semantic drift between original and optimized prompts and require manual approval when drift exceeds 40%.
- **FR-012**: System MUST detect potential trigger collisions between skills/extensions by monitoring embedding cosine similarity between their descriptions. Default collision threshold: 0.85 (configurable via `~/.sio/config.toml` → `[optimization] collision_threshold`). Threshold should be tuned empirically after V0.1 accumulates data — the Arena logs collision scores for every check.
- **FR-013**: System MUST store behavior data in separate per-platform databases, since different platforms use different AI models with fundamentally different behavior patterns.
- **FR-014**: System MUST provide per-platform installation via a command-line installer that sets up hooks, skills/extensions, configuration file updates, and database initialization appropriate to each supported platform's extension model.
- **FR-015**: *(V0.2 — deferred)* System SHOULD support real-time correction on platforms with hook-based interception (Tier 1-2): when a user signals dissatisfaction in the same session, the system generates a corrective re-prompt and retries the action immediately. **V0.1 scope**: PreToolUse hook logs pre-tool events passively only (no correction). Real-time correction requires accumulated pattern data from V0.1's closed loop before it can make reliable corrections.
- **FR-016**: System MUST support background optimization mode where accumulated labeled data is processed between sessions, producing improved prompts for the next session.
- **FR-017**: System MUST enforce quality gates: no optimization with fewer than 10 labeled examples, no deployment unless satisfaction rate improves by at least 5%, and no optimization on skills with fewer than 5 failure examples.
- **FR-018**: System MUST mine conversation history to extract full context around failure cases, using the platform's native history format. Corpus mining MUST use programmatic extraction via variable space (Constitution X) — corpora are loaded into RLM's REPL variables and queried via code, never stuffed into LLM context windows. All mining runs MUST log execution trajectories for audit.
- **FR-019**: *(V0.2 — deferred)* System SHOULD support proposing entirely new skills/extensions when it detects clusters of missed triggers (user intents that no existing skill handles). **V0.1 scope**: The system captures `missed_trigger_count` in SkillHealth aggregates (FR-005) and logs unmatched intents in BehaviorInvocation records, building the dataset needed for V0.2's clustering and proposal engine.
- **FR-020**: System MUST automatically purge behavior invocation records older than 90 days from per-platform databases. Gold-standard test cases are exempt from purging. Records referenced by an in-progress OptimizationRun (status='pending') are also exempt until the run completes or is rolled back.
- **FR-021**: System MUST support concurrent CLI sessions writing to the same per-platform database using SQLite WAL mode with unique session IDs. Health aggregates and optimization pipelines MUST operate across all sessions to provide a holistic view of per-platform model/skill behavior.
- **FR-022**: System MUST scrub known secret patterns from `user_message` AND `user_note` fields before persisting to the behavior database. `tool_input` and `tool_output` are NOT scrubbed (machine-generated, needed verbatim for optimizer training). Scrubbing MUST use these regex patterns applied in order (longest/most specific first): (1) connection strings (`postgres|mysql|mongodb|redis|amqp://...@...`), (2) AWS access keys (`AKIA[0-9A-Z]{16}`), (3) AWS secret keys (40-char base64 preceded by "secret" keyword), (4) bearer tokens (`Bearer [A-Za-z0-9\-._~+/]+=*`), (5) generic API keys (`api[_-]?key|token` followed by 20+ char value), (6) passwords in URLs (`://user:password@`). MUST NOT false-positive on: git SHAs, UUIDs, base64 content >100 chars. Replace matches with `[REDACTED]`.
- **FR-023**: System MUST treat each optimization run as an atomic transaction. If the optimizer crashes, produces invalid output, or fails any validation gate, all proposed changes MUST be rolled back with no partial application. The failure MUST be logged with full context (optimizer name, input count, error trace) to enable retry.
- **FR-024**: System MUST default to a local lightweight embedding model (fastembed with ONNX runtime, default model: all-MiniLM-L6-v2) for semantic drift detection and trigger collision monitoring. System MUST allow users to configure an external embedding API provider as an override via a settings file.
- **FR-025**: System MUST validate all behavior invocation records at write time — enforced before persistence, not as post-hoc cleanup. Validation checks in order: (1) all NOT NULL fields present and correct type, (2) `timestamp` is valid ISO-8601, (3) `platform` is in the allowed enum, (4) `behavior_type` is in the closed enum, (5) `user_satisfied` is NULL, 0, or 1, (6) secret scrubbing applied to `user_message` and `user_note`, (7) deduplication check (same `session_id + timestamp + actual_action` within 1-second window → skip). Any validation failure rejects the write and logs the error — MUST NOT crash the hook.
- **FR-026**: System MUST ensure labeled datasets contain both successes (user_satisfied=1) AND failures (user_satisfied=0) before triggering optimization. Batch review MUST warn if the label distribution is severely skewed (>90% one class) via a banner message but MUST NOT halt review or re-sort presentation order. The optimizer's quality gate (FR-017, minimum 5 failure examples) is the hard enforcement; the batch review warning is advisory.
- **FR-027**: System MUST preserve temporal ordering and before/after relationships in all dataset exports consumed by DSPy optimizers. Ordering is both within-session (by `timestamp`) and cross-session (sessions ordered by their first `timestamp`). DSPy receives data as a list sorted by `timestamp` ascending. Recency weighting uses exponential decay with a 30-day half-life: `weight = exp(-0.693 * age_days / 30)` — configurable via `~/.sio/config.toml` → `[optimization] recency_half_life_days`. At 90 days (purge boundary), weight is ~0.125 (negligible).
- **FR-028**: System MUST NOT trigger optimization from a single dissatisfied interaction. Optimization MUST only be proposed when the same failure pattern (same behavior type + same failure mode) recurs across multiple sessions, with a configurable threshold (default: 3 occurrences minimum, recommended range 3-10). This prevents overfitting to noise and ensures only genuine recurring problems drive behavior shifts.
- **FR-029**: System MUST support user-flagged pattern acceleration — when a user explicitly identifies a recurring problem (via the feedback mechanism or a dedicated "this keeps happening" signal), the system MUST treat it as a priority optimization candidate. However, the flagged pattern MUST still pass through the standard dataset/arena quality gates (minimum examples, balanced labels, gold-standard regression) before any behavior change is deployed. No shortcut past validation.
- **FR-030**: System MUST surface detected recurring failure patterns to the user with a summary (pattern description, occurrence count, affected sessions, proposed fix) and MUST require explicit user acknowledgment before deploying any behavior change. The system MUST NOT autonomously deploy optimizations without human approval.

### Key Entities

- **Behavior Invocation**: A single recorded AI action during a session. Captures what the user said, what the AI did, whether it was correct, and whether the user was satisfied. The central fact record.
- **Skill Health**: An aggregate view of a specific skill or tool's performance over time. Derived from behavior invocations. Used to identify candidates for optimization.
- **Optimization Run**: A record of a single prompt optimization attempt, tracking which optimizer was used, how many examples were included, the before/after satisfaction rates, what changes were applied, and whether regression tests passed.
- **Gold Standard**: A verified-good interaction that must never break. Used as an anchor during regression testing to ensure optimization doesn't degrade working behavior.
- **Platform Adapter**: The platform-specific layer that translates SIO's generic telemetry and optimization interfaces into native hooks, skills, extensions, configuration files, and feedback mechanisms for a specific AI CLI.
- **Conversation Corpus**: The full history of user-AI interactions stored in platform-native format. Mined by the optimization pipeline to understand the full context around failures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After 4 weeks of use, the overall user satisfaction rate (satisfied labels / total labels) reaches 90% or higher, up from the baseline measured in week 1.
- **SC-002**: The system captures at least 95% of all tool calls as telemetry records on Tier 1-2 platforms (verified by comparing logged invocations against actual tool call counts).
- **SC-003**: Users can provide feedback (rate an interaction) in 2 seconds or less using the quick-entry mechanism, with no disruption to their workflow.
- **SC-004**: After an optimization run, the target skill's satisfaction rate improves by at least 5 percentage points when measured over the next 20 invocations.
- **SC-005**: Zero gold-standard regressions occur across all optimization runs — no verified-good interaction breaks due to an optimization change.
- **SC-006**: Users label at least 30% of invocations (combining explicit feedback and passive signal detection), providing sufficient signal for the optimization pipeline.
- **SC-007**: The system successfully installs and operates on at least 3 of the 5 supported platforms within the first release cycle, with platform-appropriate degradation clearly communicated for Tier 3 platforms.
- **SC-008**: Recurring mistakes (same failure pattern across sessions) decrease by 50% or more after the first optimization cycle targeting that behavior.

## Clarifications

### Session 2026-02-25

- Q: How long should behavior invocation records be kept? → A: Rolling 90-day window with automatic purge of records older than 90 days.
- Q: How should concurrent CLI sessions be handled? → A: Allow concurrent writes via SQLite WAL mode with separate session IDs. All session data MUST be aggregated cross-session for per-platform model/skill behavior analysis (overall harness view).
- Q: What is the data protection posture for user messages in the DB? → A: Scrub known secret patterns (API keys, tokens, passwords) from user_message field before writing to the database. Regex-based detection for common secret formats.
- Q: What happens when a DSPy optimization run crashes or produces garbage? → A: Atomic rollback — treat optimization as a transaction. Any failure reverts all changes and logs the error with full context for retry. No partial prompt changes applied.
- Q: Where should the embedding model for drift/collision detection run? → A: Configurable — default to a local lightweight model (fastembed with ONNX runtime, default model: all-MiniLM-L6-v2), with user option to override with an external embedding API provider.

## Assumptions

- Users have Python 3.11+, a package manager, and Deno runtime available on their system for core installation. Deno is required for the RLM WASM sandbox used in corpus mining (Constitution X).
- Users are working with at least one of the five supported AI CLI platforms (Claude Code, Gemini CLI, OpenCode, Codex CLI, Goose).
- Users interact with their AI CLI frequently enough to generate sufficient labeled data for optimization (at least 10 labeled examples per skill within a reasonable timeframe).
- The platform's native hook/extension system is stable and its API contracts will not change drastically between minor versions.
- Version control is available and the user's project is version-controlled, enabling commit-based artifact management and rollback.
- Users are willing to occasionally provide binary feedback (even if passive detection reduces this burden).
- Local storage is sufficient for single-user behavior data volumes (no need for a remote database).
- The core telemetry/feedback/passive-detection loop is fully offline. DSPy optimizers (GEPA, MIPROv2) require an LLM API key (configurable). The external embedding API backend is an opt-in enhancement that gracefully falls back to fastembed when unreachable.

## Scope Boundaries

**In Scope:**
- Telemetry capture, feedback collection, prompt optimization, regression testing, and installation for 5 AI CLI platforms
- Per-platform database storage and platform-native artifact generation
- Real-time correction on Tier 1-2 platforms
- New skill/extension proposal from clustered missed triggers
- Single-user, local-first operation

**Out of Scope:**
- Multi-user or federated learning across users
- Cloud-hosted optimization server (all processing runs locally)
- Cross-platform transfer learning (optimizing one platform from another platform's data)
- Modifications to the AI CLI platforms themselves (SIO works within existing extension models)
- Mobile or web UI for the dashboard (CLI-only interface)
- Real-time correction of tool calls mid-session (FR-015 — V0.2, PreToolUse hook is passive in V0.1)
- Automatic proposal of new skills from missed trigger clusters (FR-019 — V0.2, data collection active in V0.1)

## Dependencies

- A prompt optimization library for evolving AI instructions from labeled examples
- An embedding model for drift detection and collision monitoring between skill descriptions (default: local fastembed with ONNX runtime, configurable to external API)
- Deno runtime for RLM WASM sandbox (corpus mining, Constitution X)
- An LLM provider for DSPy optimizers and RLM sub-LM (configurable: Azure, OpenAI, Anthropic, or local Ollama)
- A local database for per-platform behavior storage
- Version control for artifact management and rollback
- Platform-specific CLIs/SDKs must be installed for their respective adapters
