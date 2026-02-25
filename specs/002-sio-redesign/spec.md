# Feature Specification: SIO v2 — Session Mining & Pattern-Based Improvement

**Feature Branch**: `002-sio-redesign`
**Created**: 2026-02-25
**Status**: Draft
**Input**: PRD at /tmp/sio-002-specs-backup/PRD.md — SIO v2 redesign: mine existing SpecStory/JSONL session data for error patterns, cluster them, build datasets, generate improvement suggestions passively, and present a home file for user approval

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Error Mining (Priority: P1)

As a developer, I want to mine my recent coding sessions for tool failures, user corrections, and repeated attempts so that I can see what errors keep happening.

**Why this priority**: Mining is the foundation — nothing else works without extracted error data. This alone delivers value by showing the user what errors occurred across sessions.

**Independent Test**: Run the mining command against sample session files and verify structured error records are extracted with correct metadata (tool name, error text, timestamps, session context).

**Acceptance Scenarios**:

1. **Given** session files exist for the last 7 days, **When** the user runs a mine command with a 7-day window, **Then** structured error records are extracted and stored, with a summary count displayed.
2. **Given** session files contain tool failures, user corrections, repeated attempts, and undos, **When** mining runs, **Then** each error type is correctly identified and classified.
3. **Given** session files span multiple projects, **When** the user specifies a project filter, **Then** only sessions for that project are mined.
4. **Given** no session files exist in the time window, **When** mining runs, **Then** the user sees an informative message (no errors, no crash).

---

### User Story 2 — Pattern Clustering (Priority: P1)

As a developer, I want mined errors grouped into patterns (e.g., "Read tool fails on non-existent files") so that I can see recurring issues ranked by frequency and recency.

**Why this priority**: Clustering transforms raw error lists into actionable insight. Without patterns, the user would drown in individual error records.

**Independent Test**: Mine errors, then run clustering. Verify that similar errors group together, different errors stay separate, and patterns are ranked by importance.

**Acceptance Scenarios**:

1. **Given** 20 mined errors where 12 are "file not found" variants and 8 are "command timeout" variants, **When** clustering runs, **Then** exactly 2 patterns are created with counts 12 and 8 respectively.
2. **Given** errors from different sessions with similar messages but different wording, **When** clustering runs with a similarity threshold, **Then** semantically similar errors cluster together.
3. **Given** clustered patterns, **When** the user views patterns, **Then** patterns are ranked with recent frequent errors at the top.
4. **Given** a configurable similarity threshold, **When** the threshold is raised to 0.95, **Then** fewer errors cluster together (tighter grouping).

---

### User Story 3 — Dataset Builder (Priority: P2)

As a developer, I want each pattern to have a structured dataset of positive (success) and negative (failure) examples so that fix suggestions are grounded in real data.

**Why this priority**: Datasets provide the evidence base for suggestion generation. Without them, suggestions would be guesses rather than data-driven proposals.

**Independent Test**: Given a pattern with associated errors, build a dataset. Verify the dataset contains positive and negative examples with correct structure, and tracks which sessions contributed.

**Acceptance Scenarios**:

1. **Given** a pattern for "Read tool file not found" with 10 failure examples, **When** dataset building runs, **Then** a dataset is created with negative examples (failures) and positive examples (successful Read calls from the same sessions).
2. **Given** a pattern with fewer examples than the minimum threshold (default 5), **When** dataset building runs, **Then** the pattern is skipped with a note that more data is needed.
3. **Given** an existing dataset and new session data, **When** dataset building runs again, **Then** new examples are appended incrementally without rebuilding from scratch.
4. **Given** a built dataset, **When** inspecting its metadata, **Then** lineage information shows which sessions and time windows contributed.
5. **Given** the user wants a targeted dataset, **When** they specify a time range, specific error types, or session notes, **Then** the system collects matching errors into an on-demand dataset for that specific scope.
6. **Given** errors are mined (automatically or on-demand), **When** the mining run completes, **Then** errors are automatically accumulated into their respective pattern datasets for later assessment.

---

### User Story 4 — Passive Background Analysis (Priority: P2)

As a developer, I want automated daily and weekly analysis that writes improvement suggestions to a home file so that when I start a new session, actionable proposals are waiting for me.

**Why this priority**: Passive analysis is what makes SIO self-improving without user effort. The home file bridges the gap between analysis and action.

**Independent Test**: Run the passive pipeline manually. Verify that the home file is populated with ranked suggestions including pattern descriptions, confidence scores, and approve/reject commands.

**Acceptance Scenarios**:

1. **Given** patterns with sufficient datasets, **When** the passive pipeline runs, **Then** suggestions are generated and written to the home file as ranked markdown.
2. **Given** a scheduled daily run, **When** the schedule triggers, **Then** the last 24 hours of sessions are mined, clusters updated, and home file refreshed.
3. **Given** a scheduled weekly run, **When** the schedule triggers, **Then** a full re-analysis regenerates all suggestions from the complete dataset.
4. **Given** suggestions older than 30 days that were never acted on, **When** the pipeline runs, **Then** stale suggestions are archived.
5. **Given** an active coding session, **When** the passive pipeline runs in the background, **Then** it does not interfere with or slow down the active session.

---

### User Story 5 — Human Review & Tagging (Priority: P3)

As a developer, I want to interactively review suggestions — approving, rejecting, or deferring each — with optional AI-assisted explanations of patterns so that I stay in control of what changes get applied.

**Why this priority**: Human oversight is the safety gate. No change should be applied without explicit approval.

**Independent Test**: Given a home file with pending suggestions, run the review command. Verify approve/reject/defer actions persist and AI tagging produces meaningful explanations.

**Acceptance Scenarios**:

1. **Given** 5 pending suggestions, **When** the user runs the review command, **Then** each suggestion is presented with its pattern, confidence score, and proposed change.
2. **Given** a suggestion, **When** the user approves it, **Then** the suggestion status changes to "approved" and persists across sessions.
3. **Given** a suggestion, **When** the user requests AI-assisted tagging, **Then** an explanation is generated from the positive and negative examples describing what the pattern is and why the fix should help.
4. **Given** a partially completed review session, **When** the user quits and re-enters review, **Then** the review resumes from where they left off.

---

### User Story 6 — Change Application & Rollback (Priority: P3)

As a developer, I want approved suggestions to be written to the correct configuration files with version control and rollback capability so that improvements are applied safely.

**Why this priority**: This closes the loop — without application, suggestions are just recommendations.

**Independent Test**: Approve a suggestion, verify the target file is updated and a version-controlled record is created. Then rollback and verify the file is restored.

**Acceptance Scenarios**:

1. **Given** an approved prompt rule suggestion, **When** the change is applied, **Then** the rule is appended to the user's configuration file (never overwriting existing content).
2. **Given** an applied change, **When** the user requests rollback, **Then** the file is restored to its pre-change state and the rollback is logged.
3. **Given** two suggestions that modify overlapping file sections, **When** both are approved, **Then** the user is warned about the conflict before application.
4. **Given** a proposed change that diverges significantly from the current configuration, **When** the change is about to be applied, **Then** the user is prompted for explicit confirmation.
5. **Given** an applied change, **When** viewing the change log, **Then** the entry shows timestamp, pattern reference, target file, and a record identifier for rollback.

---

### Edge Cases

- What happens when session files are corrupted or partially written? The system skips malformed entries and continues processing valid ones.
- What happens when the embedding model is not yet downloaded? The system prompts the user to download it on first run and exits gracefully.
- What happens when two passive analysis runs overlap? The system uses database-level locking to prevent concurrent writes; the second run waits or skips.
- What happens when a target configuration file does not exist? The system creates it with only the suggested content.
- What happens when the user has no session data at all? The system reports "No sessions found in the specified time window" and exits cleanly.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST parse conversation history files to extract tool calls, their inputs, outputs, and error states.
- **FR-002**: System MUST parse structured session transcripts to extract message objects with role, content, and tool metadata.
- **FR-003**: System MUST identify four types of errors: tool failures, user corrections, repeated attempts, and undos.
- **FR-004**: System MUST support time-window filtering (last N days, last N weeks, custom date range) when mining sessions.
- **FR-005**: System MUST support project-based filtering when mining sessions.
- **FR-006**: System MUST group similar errors into patterns using semantic similarity with a configurable threshold (default 0.80).
- **FR-007**: System MUST rank patterns by a composite score of frequency and recency.
- **FR-008**: System MUST require a minimum number of occurrences (default 3) before recognizing a pattern.
- **FR-009**: System MUST build structured datasets with positive and negative examples for each qualifying pattern.
- **FR-010**: System MUST track dataset lineage (contributing sessions, time windows, pattern version).
- **FR-011**: System MUST support incremental dataset updates (append new data, no full rebuild).
- **FR-012**: System MUST enforce a minimum dataset size (default 5 examples) before generating suggestions.
- **FR-025**: System MUST support on-demand dataset collection from a user-specified time range, specific error types, or session notes (e.g., `sio datasets collect --since "2 weeks" --error-type tool_failure`).
- **FR-026**: System MUST automatically accumulate mined errors into their respective pattern datasets on every mining run, so datasets grow passively over time for later assessment.
- **FR-013**: System MUST generate improvement suggestions categorized as: prompt rules, skill updates, or configuration changes.
- **FR-014**: System MUST assign a confidence score (0-100%) to each suggestion based on pattern strength and dataset quality.
- **FR-015**: System MUST write suggestions to a ranked home file with priority sections and actionable commands.
- **FR-016**: System MUST support automated daily (last 24h) and weekly (full re-analysis) scheduling.
- **FR-017**: System MUST provide interactive review with approve, reject, defer, and AI-tag actions per suggestion.
- **FR-018**: System MUST persist review state across sessions.
- **FR-019**: System MUST apply approved changes to the correct target files, appending (never overwriting) existing content.
- **FR-020**: System MUST create a version-controlled record for every applied change.
- **FR-021**: System MUST support rollback of any applied change by its identifier.
- **FR-022**: System MUST detect and warn about conflicting changes to the same file section.
- **FR-023**: System MUST require explicit confirmation for changes that diverge significantly from current configuration.
- **FR-024**: System MUST auto-archive suggestions older than 30 days that have not been acted upon.

### Key Entities

- **ErrorRecord**: A single extracted error from a session — includes session reference, timestamp, tool involved, error description, surrounding context, and error classification.
- **Pattern**: A cluster of similar errors — includes a human-readable identifier, description, occurrence count, affected session count, time range, importance score, and representative examples.
- **Dataset**: Positive and negative examples for a pattern — includes example count breakdowns, minimum threshold, contributing session references, and creation/update timestamps.
- **Suggestion**: A proposed improvement generated from a pattern and its dataset — includes description, confidence score, proposed change content, target file, change category, review status, and optional AI explanation.
- **AppliedChange**: A deployed suggestion — includes target file, before/after content snapshots, version control reference, application timestamp, and rollback status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Mining 30 days of sessions (up to 500 sessions) completes in under 60 seconds.
- **SC-002**: 90% of semantically similar errors (same root cause, different wording) cluster into the same pattern.
- **SC-003**: Users can review and act on a suggestion in under 30 seconds per item.
- **SC-004**: Approved changes reduce recurrence of the targeted error pattern by at least 50% in subsequent sessions.
- **SC-005**: Passive analysis runs complete without impacting active coding session performance (no user-perceptible slowdown).
- **SC-006**: 100% of applied changes can be rolled back to restore the previous state.
- **SC-007**: The home file contains actionable suggestions within 24 hours of the first mining run.

## Assumptions

- Users have existing session history files from their AI coding assistant (at least 7 days of data for meaningful patterns).
- The local embedding model download (~90MB) is a one-time cost that users accept.
- Users are comfortable reviewing suggestions via a command-line interface.
- The system runs on Linux or macOS (including WSL2) with standard scheduling tools available.
- Version control (git) is available in the user's environment for change tracking.

## Scope Boundaries

**In Scope**:
- Mining SpecStory markdown files and Claude JSONL transcripts
- Embedding-based pattern clustering
- Dataset construction with lineage tracking
- Passive daily/weekly scheduling
- Home file generation
- Interactive review with AI-assisted tagging
- Change application with rollback

**Out of Scope**:
- Real-time capture via hooks (v1 approach, replaced by mining)
- Support for non-Claude AI coding assistants (future work)
- Cloud-based analysis or external API calls
- Automatic change application without human approval
- Custom embedding model training
