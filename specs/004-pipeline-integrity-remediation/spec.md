# Feature Specification: SIO Pipeline Integrity & Training-Data Remediation

**Feature Branch**: `004-pipeline-integrity-remediation`
**Created**: 2026-04-20
**Status**: Draft
**Input**: User description: "PRD-pipeline-integrity-remediation.md"

## Overview

SIO's data-ingestion surface (error mining, flow mining, positive-signal capture) is healthy, but the downstream training, optimization, suggestion, and audit pipeline is starving, silently destroying state, or running unsafely. An adversarial audit produced 34 findings across four severity tiers (7 CRITICAL, 12 HIGH, 8 MEDIUM, 6 LOW, including H10/H11/H12/L6 flagged by two hunters). This feature restores end-to-end data flow from hook capture → labeled examples → optimization → suggestion → safe application → audit trail, then hardens every destructive or silent-failure path, then closes the long tail of correctness defects. Per owner direction, **all audit findings are in scope — no deferrals.**

## User Scenarios & Testing *(mandatory)*

### User Story 1 - DSPy optimizer receives labeled examples and produces real optimization runs (Priority: P1)

As the SIO operator, after interacting with my coding agent for a week, I want the DSPy optimizer to have a growing pool of labeled examples so that `sio optimize` produces improved prompt modules instead of failing with "no data." Today the tool-invocation telemetry is being captured, but it lands in a location the trainer never reads, so optimization has never run and the `optimized_modules` table has been stale since one manual batch in March.

**Why this priority**: This is the headline failure. Every other downstream training capability (optimization runs, gold standards, recall examples, auto-promotion, automated suggestion generation) cascades from the fact that captured tool outcomes never reach the training data store. Fixing this single flow unblocks four empty tables.

**Independent Test**: Interact with the agent for a day with the fixed pipeline installed. Within 24 hours, training-data count is greater than zero in the location the optimizer reads from. Running `sio optimize` on the accumulated examples completes and records an optimization run in the audit store. Verifiable without looking at any other user story.

**Acceptance Scenarios**:

1. **Given** a fresh install with the integrity fixes applied, **When** the user completes one tool invocation that the hook captures, **Then** the row is readable from the same data store the trainer queries.
2. **Given** pre-existing tool-invocation rows in the legacy location, **When** the one-time migration runs, **Then** all pre-existing rows (≥ 38,091 at audit time) are present in the trainer's store with no loss.
3. **Given** accumulated labeled examples, **When** `sio optimize` runs, **Then** at least one optimization run row is written and an optimized module is produced.
4. **Given** a satisfied and correct tool outcome, **When** the hook records it, **Then** within the same session the record is promoted to the gold-standard set (eligible for future training).

---

### User Story 2 - Re-running suggestion generation preserves audit history (Priority: P1)

As the operator, when I run suggestion generation a second time, I want my history of applied rules to survive so that I can audit, roll back, or report on which rules I previously approved. Today every suggestion run silently wipes the applied-change log, patterns, datasets, and pattern-error links — destroying the one record that tells me what the system has actually done to my configuration.

**Why this priority**: Data loss. Once the audit log is gone it cannot be reconstructed from other tables. Rollback capability depends entirely on this log.

**Independent Test**: Apply a rule, confirm it appears in the audit log, then run suggestion generation again. Verify the applied-change row still exists. Verifiable independently of Story 1.

**Acceptance Scenarios**:

1. **Given** an applied-change log with N rows, **When** suggestion generation runs again, **Then** the log still contains all N rows plus any newly recorded applications.
2. **Given** a previously applied rule, **When** the operator invokes rollback, **Then** the rule is reversed using the audit-log record (not fabricated from current-state guessing).
3. **Given** a stale pattern or dataset from a previous run, **When** suggestion generation runs, **Then** the stale row is marked superseded (not deleted) and new rows are linked to the new cycle.

---

### User Story 3 - Applying a rule to a user-owned file is safe and reversible (Priority: P1)

As the operator, when SIO writes changes to my personal configuration files (CLAUDE.md, tool-rule files), I want the write to be crash-safe and backed up so that an interrupted write, file-watcher race, or antivirus interference cannot corrupt or empty the target file. Today the writer does a single non-atomic write with no backup, and the host environment has documented sed-style temp-rename races that have wiped files before.

**Why this priority**: Irrecoverable data loss on user-owned files. Sensitive targets (CLAUDE.md, environment config) are in the write path.

**Independent Test**: Apply a change, simulate a crash mid-write, verify the target file is either the old version or the new version (never empty or partial) and a timestamped backup exists. Verifiable independently.

**Acceptance Scenarios**:

1. **Given** an apply request, **When** the write is interrupted partway, **Then** the target file contains the original content (not empty, not partial).
2. **Given** a successful apply, **When** the operator lists backups, **Then** a timestamped pre-write copy of the target exists.
3. **Given** accumulated backups over months, **When** the backup directory grows, **Then** retention keeps the last N copies per file and prunes older ones.
4. **Given** a request to write to a file outside the explicit allowlist, **When** the writer validates the path, **Then** the write is refused with a clear message.
5. **Given** a rule that can be interpreted as a "merge" with an existing similar rule, **When** suggestion application runs, **Then** the merge requires explicit operator consent rather than being fabricated silently.

---

### User Story 4 - The autoresearch loop runs continuously without human babysitting (Priority: P2)

As the operator, I want the autoresearch loop to run on a schedule and accumulate a transaction log so that the system can promote rules automatically (within guardrails) while I am not actively using it. Today the loop exists in code but has never been scheduled, so its transaction log is empty and no automated promotion has ever occurred.

**Why this priority**: This is the self-improvement mechanism. Without it, SIO only improves when the operator manually runs the pipeline.

**Independent Test**: Install the schedule, wait 24 hours, confirm the autoresearch transaction log has ≥ 5 rows. Confirm at least one automated action was gated by a human-approval flag before being applied.

**Acceptance Scenarios**:

1. **Given** a clean install, **When** the schedule is activated, **Then** the autoresearch loop fires on the documented cadence without operator intervention.
2. **Given** a candidate promotion surfaced by autoresearch, **When** the candidate lacks arena validation or operator approval, **Then** the candidate is recorded as pending and NOT auto-applied.
3. **Given** a candidate that passes the approval gate, **When** autoresearch promotes it, **Then** the audit log records the automated application distinctly from a human application.

---

### User Story 5 - Mining the same session twice does not duplicate rows (Priority: P2)

As the operator, when I re-run mining (either manually or on a schedule), I want the system to skip already-processed content instead of re-ingesting the same events. Today flow mining re-ingests every session file every run (1,500–1,800 duplicate events per day observed in evidence), error mining rehashes the entire file every pass, and the parser reads the full file into memory with no streaming.

**Why this priority**: Correctness of everything downstream (pattern counts, recency scoring, flow success rates) depends on event counts being accurate. Duplication also wastes runtime and memory.

**Independent Test**: Run mining twice back-to-back on an unchanged corpus, verify row counts are identical before/after the second run. Run mining on a large session file (≥ 100 MB), verify peak memory stays bounded.

**Acceptance Scenarios**:

1. **Given** a session file already mined, **When** mining runs again with no new content, **Then** no new rows are written to any mining table.
2. **Given** a session file with appended content, **When** mining runs, **Then** only the appended content is parsed (not the entire file).
3. **Given** a 100 MB session file, **When** mining runs, **Then** peak memory stays below 500 MB for the full corpus pass.
4. **Given** a subagent session file, **When** mining encounters it, **Then** it is linked to its parent session and not counted as a top-level error source by default.
5. **Given** concurrent mining and hook writes against the same store, **When** both try to write at once, **Then** writes succeed without "database busy" errors.

---

### User Story 6 - `sio status` surfaces silent failures (Priority: P2)

As the operator, when I run `sio status`, I want to see whether hooks are healthy, when they last succeeded, and whether any are in a consecutive-failure state so that I can diagnose telemetry gaps without reading code or logs. Today hooks swallow exceptions silently and there is no health surface.

**Why this priority**: Silent failure is the failure mode that caused the audit-discovered issues to persist for weeks unnoticed. Observability closes the feedback loop.

**Independent Test**: Inject a hook failure, run `sio status`, verify the hook shows as degraded with last-error timestamp and consecutive-failure count. Fix the failure, verify the surface recovers.

**Acceptance Scenarios**:

1. **Given** all hooks healthy, **When** the operator runs `sio status`, **Then** each hook reports a recent successful heartbeat.
2. **Given** a hook has failed repeatedly, **When** the operator runs `sio status`, **Then** the hook reports degraded with last-error and failure-count visible.
3. **Given** a stale heartbeat older than the documented threshold, **When** the operator runs `sio status`, **Then** the hook is flagged as stale even if no explicit error occurred.

---

### User Story 7 - Pattern identifiers are stable across runs (Priority: P3)

As the operator, when I label a pattern with ground-truth feedback and the corpus is re-clustered, I want the pattern identifier to remain stable so that my labeled feedback still joins correctly. Today the slug is order-dependent, and any change in input ordering can rename every pattern, orphaning ground-truth rows.

**Why this priority**: Data-join correctness over time. Without stable IDs, historical feedback detaches from current patterns every run, invalidating ground truth.

**Independent Test**: Run clustering twice on identical input in different orders, verify the same pattern receives the same slug. Add one new error, re-cluster, verify unchanged patterns keep their slugs.

**Acceptance Scenarios**:

1. **Given** the same corpus clustered twice, **When** the input order differs, **Then** the generated pattern slugs are identical.
2. **Given** a corpus with a new error added, **When** re-clustering runs, **Then** previously-stable patterns retain their original slugs.
3. **Given** ground-truth rows keyed on old slugs, **When** the slug generation changes, **Then** a one-time remap updates the foreign keys by error-overlap match.

---

### User Story 8 - Suggestion approval rate improves (Priority: P3)

As the operator, when I review generated suggestions, I want most to be worth approving. Today 92% of generated suggestions are rejected at the approval gate, indicating low signal from the generator.

**Why this priority**: Product quality of the main operator-facing output. Not a correctness bug, but a bar for "done."

**Independent Test**: Run a batch of suggestions against a held-out corpus, measure approval rate, compare to the 92% rejection baseline.

**Acceptance Scenarios**:

1. **Given** an instrumented generator, **When** a batch of suggestions is produced, **Then** per-stage rejection reasons are recorded for analysis.
2. **Given** a tuned generator with an improved metric, **When** a new batch is produced, **Then** approval rate exceeds 30% (target; baseline 8%).

---

### User Story 9 - Adversarial re-audit returns clean (Priority: P3)

As the operator, after the remediation lands, I want an independent re-audit to confirm zero CRITICAL and zero HIGH findings remain, so that I have an objective gate on "done" rather than relying on the original audit list being exhaustive.

**Why this priority**: Quality gate on the PRD itself. Ensures nothing was missed or newly introduced.

**Independent Test**: Spawn two adversarial audit agents on the post-fix repo; both must return zero CRITICAL / HIGH findings.

**Acceptance Scenarios**:

1. **Given** all phase 1–3 work merged, **When** both adversarial agents re-scan, **Then** neither reports a CRITICAL finding.
2. **Given** all phase 1–3 work merged, **When** both adversarial agents re-scan, **Then** neither reports a HIGH finding not already covered.
3. **Given** the re-scan surfaces new MEDIUM / LOW findings, **When** the operator reviews them, **Then** those are opened as a follow-up (not blocking this feature's closure).

---

### Edge Cases

- **Partial migration**: the one-time backfill script is interrupted halfway through copying legacy rows. The resumed run must not produce duplicates nor drop rows (idempotent on `INSERT OR IGNORE`).
- **Timezone drift**: the host runs in a non-UTC timezone and ingests a mix of naive timestamps, UTC-suffix timestamps, and localized timestamps. All recency and "established/declining" grading must remain correct regardless of input format; timestamps normalize to UTC on write.
- **Reinstall loop**: the operator re-runs `sio install` after the remediation. The installer MUST NOT recreate the legacy split-brain store, otherwise every reinstall resurrects the headline bug.
- **Platform string drift**: a writer records rows with one platform label and a reader filters for a slightly different label. The result must not be silent zero rows; writers and readers share the same constant.
- **Empty-timestamp crash**: a row with an empty timestamp string reaches ranking/grading. The code must fall back to a substitute timestamp, not raise.
- **Unattended auto-promotion**: autoresearch suggests a rule and there is no human online. The rule MUST NOT be auto-applied without passing the arena-validation + approval gate.
- **Polyglot repositories**: mining encounters Rust / Go / Java / C++ / notebook files. Flow extraction must not silently skip them.
- **Growing JSONL during mining**: a session file is being appended to while mining reads it. Byte-offset resume must pick up only the appended region on the next pass.
- **Very large session file**: a pathological multi-gigabyte file appears in the mining path. The file-hash path must guard against OOM rather than attempt to hash the whole file.
- **Suggestion run collides with hook writes**: both processes hit the store simultaneously. The longer busy-timeout absorbs the contention without raising.
- **Path-traversal-style target**: an apply request references `../../etc/hosts` or a file outside the explicit allowlist. The writer refuses.
- **Rule-merge fabrication**: two distinct rules with similar embeddings are silently merged into a hybrid third rule. This MUST require explicit operator consent.

## Requirements *(mandatory)*

### Functional Requirements

#### Data Flow Integrity (Phase 1)

- **FR-001**: All tool-invocation hooks MUST write captured events to the single canonical data store that the training, optimization, and suggestion pipelines read from.
- **FR-002**: The system MUST provide a one-time migration that copies all pre-existing legacy tool-invocation rows into the canonical store, with an idempotent insert semantic so that re-running the migration does not duplicate rows.
- **FR-003**: Re-running suggestion generation MUST NOT delete the applied-change audit log, pattern rows, dataset rows, or pattern-error links. Stale entries MUST be marked as superseded rather than removed.
- **FR-004**: Writes to user-owned files MUST be atomic (temp-file + fsync + rename) AND MUST create a timestamped backup copy before write.
- **FR-005**: The system MUST auto-promote a tool-invocation record to the gold-standard set when the record indicates both user satisfaction and correct outcome.
- **FR-006**: The autoresearch loop MUST run on a documented schedule without requiring an interactive session, and it MUST record every firing in its transaction log.
- **FR-007**: The installer MUST be idempotent: re-running it MUST NOT recreate the legacy split-brain data store or revert the canonical data path.

#### Mining Correctness (Phase 2)

- **FR-008**: Flow mining MUST honor the same processed-session set that error mining honors; re-running flow mining on an unchanged corpus MUST NOT write new flow rows.
- **FR-009**: The session-file parser MUST read files in a streaming fashion so that memory usage is bounded regardless of file size.
- **FR-010**: File-level dedup MUST support byte-offset-resume so that growing session files are only re-parsed from the last known offset forward.
- **FR-011**: Subagent session files MUST be linked to their parent session, marked as subagent sources, and excluded from top-level error mining unless explicitly requested.
- **FR-012**: The data store MUST use a busy-timeout long enough to absorb expected concurrent mine + hook contention without raising "database busy" errors.
- **FR-013**: Ranking and grading MUST safely handle rows with empty-string timestamps without raising.
- **FR-014**: Pattern identifiers MUST be deterministic given the same set of input errors, independent of row insertion order. A one-time remap of ground-truth foreign keys by error-overlap MUST accompany any slug-algorithm change.
- **FR-015**: Indexes MUST exist for the hot read paths on insert-time dedup and flow promotion queries.

#### Hardening & Coverage (Phase 3)

- **FR-016**: All hooks MUST write a heartbeat record with last-success, last-error, consecutive-failure-count, and hook name; `sio status` MUST surface this health to the operator, including stale-heartbeat detection.
- **FR-017**: The data store MUST carry a schema-version marker and MUST refuse to start if a prior migration is partially applied.
- **FR-018**: The recall-evaluation metric MUST distinguish correct from hallucinated outputs (the current trivial string-equality metric MUST be replaced by a semantic-similarity or exact-match method appropriate per task type).
- **FR-019**: The file-write allowlist MUST NOT grant blanket write access to the current working directory; only explicit allowlisted locations are writable.
- **FR-020**: Lower-priority error records (e.g., tool-failure) MUST NOT be deduped away in favor of higher-priority rows of a different type; dedup MUST be within-type only.
- **FR-021**: Flow success heuristics MUST require an explicit positive signal rather than marking any absence-of-negative as success.
- **FR-022**: N-gram extraction MUST produce n-grams for every requested length including the upper bound (the current range is off by one).
- **FR-023**: Pattern grading MUST compute recency against the latest error timestamp for that pattern so that the "declining" grade is actually reachable.
- **FR-024**: Rule merges that would combine two existing rules into a hybrid MUST require explicit operator consent.
- **FR-025**: The purge command MUST target the canonical data store (not the legacy one) and MUST provide a separate flag for behavior-only purges.
- **FR-026**: The flow extractor MUST accept additional common language extensions (at minimum Rust, Go, Java, C++, notebook files).
- **FR-027**: The mining pipeline MUST log a warning when an expected session directory is missing, not skip silently.
- **FR-028**: The file-hash function MUST guard against pathologically large files (e.g., > 1 GB) with a size cap and warning, not OOM.
- **FR-029**: Suggestion generation MUST be instrumented so that rejection reasons at each stage are recorded for quality analysis.

#### Timezone, Platform, and Centroid Correctness (Phase 3 add-ons)

- **FR-030**: All timestamps MUST be timezone-aware; naive inputs MUST be normalized to UTC on write and stored in an explicit ISO-8601 form with UTC offset.
- **FR-031**: The platform label used by writers and readers MUST come from a single shared constant; no string duplication is permitted in either path.
- **FR-032**: Clustering MUST support per-pattern centroid persistence so that re-running suggestion generation reuses existing vectors for unchanged patterns instead of recomputing embeddings for the entire corpus every run.

#### Coverage Closure (Phase 4)

- **FR-033**: Every finding from the original adversarial audit MUST close with a linked task and file:line citation in the changelog; no "deferred" markers remain in the PRD.
- **FR-034**: Two independent adversarial audit agents MUST re-scan the post-fix codebase and return zero CRITICAL and zero HIGH findings. Any new MEDIUM/LOW findings are handled as follow-up, not blockers for this feature.

### Key Entities *(include if feature involves data)*

- **Tool-invocation record**: A captured event from agent tool use, containing the tool name, input, outcome, user-satisfaction signal, correctness signal, platform label, and timestamp. Consumed by the optimizer and gold-standard promotion.
- **Applied-change record**: An immutable audit entry describing which rule was applied to which target file at what time, with enough context to roll back. Must survive suggestion-generation cycles.
- **Pattern**: A named cluster of related errors with a stable slug, a centroid representation, associated error links, a grade (e.g., emerging, established, declining), and attached ground-truth feedback.
- **Gold-standard example**: A curated tool-invocation record promoted because it represented a known-good operator outcome; used as training data for optimization.
- **Autoresearch transaction**: A log entry describing each firing of the autoresearch loop, including whether a candidate was promoted, gated, or rejected, and by what criterion.
- **Hook heartbeat**: A per-hook health record containing last-success time, last-error time and message, consecutive-failure count, and hook name; read by `sio status`.
- **Session mining checkpoint**: Per-file state recording the last processed byte offset and subagent/parent linkage so that re-runs are idempotent.
- **Schema version marker**: A record of the current data-store migration version so that the system refuses to run against a partially migrated store.
- **Backup snapshot**: A timestamped pre-write copy of a user-owned target file, retained per a documented retention policy (keep last N per file).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Within 24 hours of the remediation being installed and the operator using the agent normally, the training store holds at least 38,091 tool-invocation rows (all legacy rows present) plus any new activity.
- **SC-002**: Running suggestion generation twice in a row preserves 100% of applied-change audit rows (no data loss).
- **SC-003**: Applying the same rule twice produces two backup snapshots with distinct timestamps, and an interrupted write leaves the target file in its pre-write state 100% of the time across a crash-injection test.
- **SC-004**: Within 7 days of activation, the gold-standard set is non-empty and at least one optimization run has been recorded.
- **SC-005**: The autoresearch transaction log accumulates at least 5 entries within 24 hours of schedule activation.
- **SC-006**: Running mining twice back-to-back on an unchanged corpus produces zero new rows on the second run (idempotency).
- **SC-007**: Mining a corpus containing at least one 100 MB session file completes with peak memory below 500 MB.
- **SC-008**: On a host running in a non-UTC timezone with mixed timestamp formats in the corpus, pattern grading and recency ranking produce the same results as on a UTC host (no drift).
- **SC-009**: `sio status` surfaces hook health within 2 seconds, including stale-heartbeat detection, and an injected hook failure appears as degraded within one heartbeat cycle.
- **SC-010**: Re-running clustering on the same corpus in different input orders produces identical pattern identifiers (zero slug churn).
- **SC-011**: Re-running suggestion generation with no new errors completes in under 5 seconds (no full-corpus embedding recomputation).
- **SC-012**: Suggestion approval rate reaches at least 30% on a fresh batch (up from the current 8% baseline).
- **SC-013**: Two independent adversarial audits of the post-fix codebase return zero CRITICAL and zero HIGH findings.
- **SC-014**: Running `sio install` after the remediation does not create the legacy data store path and does not revert the canonical data path.
- **SC-015**: Every finding from the original audit is closed with a task reference in the changelog; zero deferrals remain.

## Assumptions

- The canonical data store path (the location readers expect) is kept as the consolidation target; writers move to it. (Alternative "sync" design is recorded as an Open Question but not assumed here.)
- The one-time legacy backfill copies the full legacy row set (not a trimmed last-30-days window). The backfill is idempotent.
- The autoresearch schedule uses an in-ecosystem scheduler preferred by the operator over host-level systemd (lower friction), but either satisfies FR-006.
- Only one agent platform is in scope (`claude-code`). Multi-platform is a follow-up.
- "Explicit positive signal" for flow success (FR-021) means a documented positive-outcome marker in the mined transcript; defining the precise marker list is implementation, not spec.
- The backup retention policy default is "last 10 per file." The operator may adjust; the default satisfies FR-004.
- Ground-truth remap after slug change is keyed on overlap of the member error set between old and new clusters.
- `sio status` runs in under 2 seconds on a typical store (up to low millions of rows); the 2-second target in SC-009 is a usability bar, not a hard performance requirement.

## Dependencies

- The operator's host supports a user-level scheduler (systemd-user, cron, or the in-ecosystem scheduler) capable of firing at the documented cadence without an interactive session.
- The host filesystem supports atomic rename semantics (standard on ext4 / NTFS / APFS).
- An adversarial audit capability is available to perform the phase 4 re-scan.
- The operator's existing `~/.sio/sio.db` is preserved as-is and the migration runs against a copy first for verification before being applied to the live store.

## Out of Scope

- New SIO commands or user-facing features beyond the `sio status` health surface update.
- DSPy module or signature redesign beyond replacing the trivially-broken recall metric.
- Full migration framework. A `schema_version` marker is added; the existing `IF NOT EXISTS` ALTER pattern is retained.
- Multi-platform agent support (Cursor, Codex, Aider). Only `claude-code` is in scope.
- Web UI for suggestion review.
- LLM-as-judge replacement for the recall metric.
- Distributed mining (remains single-machine).
