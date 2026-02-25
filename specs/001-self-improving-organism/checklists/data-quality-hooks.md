# Data Quality & Platform Hooks Checklist: Self-Improving Organism

**Purpose**: Author self-check — validate that Data Quality/Telemetry and Platform Adapter/Hook requirements are complete, clear, and buildable without guessing
**Created**: 2026-02-25
**Feature**: [spec.md](../spec.md) | [hook-contracts.md](../contracts/hook-contracts.md)
**Focus**: FR-001, FR-003, FR-013, FR-021, FR-022, FR-024, FR-025–027, hook contracts, Constitution IX

## Requirement Completeness — Data Collection

- [x] CHK001 - Are all 20 fields of the BehaviorInvocation schema explicitly defined with types and constraints, or are any left as "TBD"? [Completeness, data-model.md §BehaviorInvocation]
- [x] CHK002 - Is the deduplication strategy specified — what constitutes a duplicate invocation? The natural key `(session_id, timestamp, actual_action)` is listed but is it enforced as a UNIQUE constraint or application-level check? [Clarity, data-model.md §Identity]
- [x] CHK003 - Are write-time validation rules for FR-025 enumerated exhaustively, or does "schema validation" leave room for interpretation about which fields are validated and how? [Clarity, Spec §FR-025]
- [x] CHK004 - Is the `behavior_type` enum closed or open? The spec lists 4 values ('skill', 'mcp_tool', 'preference', 'instructions_rule') — are new types allowed, and if so, how are they registered? [Gap, data-model.md §BehaviorInvocation]
- [x] CHK005 - Are requirements defined for what happens when `user_message` extraction fails (set to `[UNAVAILABLE]`) — does this affect downstream auto-labeling, passive signal detection, or optimizer quality gates? [Coverage, contracts/hook-contracts.md §PostToolUse]
- [x] CHK006 - Is the `token_count` field sourced from the platform hook payload, estimated, or left NULL? The spec doesn't define how it's populated. [Gap, data-model.md §BehaviorInvocation]
- [x] CHK007 - Is the `latency_ms` measurement point defined — wall clock from tool start to tool end, or from user message to tool response? [Clarity, data-model.md §BehaviorInvocation]

## Requirement Completeness — Secret Scrubbing

- [x] CHK008 - Are the specific regex patterns for secret scrubbing enumerated (AWS keys, bearer tokens, passwords, connection strings), or is "common secret formats" left undefined? [Clarity, Spec §FR-022]
- [x] CHK009 - Is the scrubbing order specified — what happens when a secret pattern overlaps with another (e.g., a connection string containing an API key)? [Edge Case, Spec §FR-022]
- [x] CHK010 - Are false positive requirements defined — what classes of normal text should NOT be scrubbed (e.g., base64-encoded content, long hex strings that aren't keys)? [Coverage, Spec §FR-022]
- [x] CHK011 - Is the scrubbing boundary defined — does it apply only to `user_message` or also to `tool_input`, `tool_output`, `user_note`? The spec says "user_message field" but tool_output could also contain secrets. [Gap, Spec §FR-022]

## Requirement Clarity — Dataset Quality (Constitution IX)

- [x] CHK012 - Is "balanced labels" (FR-026) quantified beyond ">90% one class" — what is the minimum number of each class needed, and what happens when the warning fires? Does batch review halt, warn, or re-sort to present underrepresented labels first? [Clarity, Spec §FR-026]
- [x] CHK013 - Is "recency weighting" (FR-027) specified with a formula or decay function, or is it left to the implementer? What weight does a 1-day-old label get vs a 60-day-old label? [Clarity, Spec §FR-027]
- [x] CHK014 - Is "temporal ordering" (FR-027) defined as within-session ordering, cross-session ordering, or both? Are there requirements for how DSPy receives the ordered data (sorted list, time-stamped pairs)? [Clarity, Spec §FR-027]
- [x] CHK015 - Are requirements for the 90-day rolling window purge (FR-020) consistent with recency weighting (FR-027) — does purging a 91-day-old record that still has weight in an active optimization cause data loss? [Consistency, Spec §FR-020 vs FR-027]

## Requirement Completeness — Embedding & Drift

- [x] CHK016 - Is the embedding cache invalidation strategy fully specified — does model swap invalidate ALL cached embeddings or only new ones? What about cache size limits? [Gap, Spec §FR-024]
- [x] CHK017 - Is the external API embedding override (FR-024) specified with required config fields (endpoint URL, API key env var, model name, dimension), or just "configure via settings file"? [Clarity, Spec §FR-024]
- [x] CHK018 - Is the collision detection threshold (0.85 cosine similarity) justified with empirical rationale, or is it arbitrary? Are requirements defined for tuning this threshold per-platform? [Clarity, Spec §FR-012]

## Requirement Completeness — Hook Contracts

- [x] CHK019 - Does the PostToolUse hook contract define ALL fields that Claude Code actually sends in the stdin JSON, or is it a subset? Are there platform fields being silently ignored that could be useful for telemetry? [Completeness, contracts/hook-contracts.md §PostToolUse]
- [x] CHK020 - Is the Notification hook event type validated against Claude Code's actual hook event taxonomy? The implementation note flags a potential mismatch — are requirements defined for the fallback if `Notification` is the wrong event? [Clarity, contracts/hook-contracts.md §Notification]
- [x] CHK021 - Are error handling requirements for hooks complete — "exit 0 on any error" is defined, but are retry semantics specified? If the DB write fails, is the invocation lost forever or queued? [Gap, contracts/hook-contracts.md §Error Handling]
- [x] CHK022 - Is the PreToolUse hook contract's V0.1 "passive only" behavior specified with enough detail to implement — what exactly does "log the pre-tool event for context" mean? What table/field does it write to? [Clarity, contracts/hook-contracts.md §PreToolUse]
- [x] CHK023 - Are hook installation requirements (T073) specific about WHERE hooks register in `~/.claude/settings.json` — the JSON path, the hook event names, and the exact shell command format? [Completeness, Spec §FR-014]

## Requirement Consistency — Cross-Artifact Alignment

- [x] CHK024 - Do the hook contract skill YAML descriptions (`sio-feedback`, `sio-optimize`, `sio-health`, `sio-review`) align with the CLI command signatures in cli-commands.md? Are trigger descriptions and allowed_tools consistent? [Consistency, contracts/hook-contracts.md §Skills vs cli-commands.md]
- [x] CHK025 - Is the `passive_signal` enum in data-model.md ('undo', 'correction', 're_invocation') consistent with the signals detected in spec FR-004 (undos, correction language, manual re-invocation)? RESOLVED: `negative_rating` renamed to `re_invocation` to match spec's detection list. [Consistency, data-model.md vs Spec §FR-004]
- [x] CHK026 - Are the auto-labeler fields (`activated`, `correct_action`, `correct_outcome`) defined with clear heuristics for how each is inferred, or is "agent-inferred" left to the implementer's judgment? [Clarity, Spec §FR-003]

## Edge Case & Recovery Coverage

- [x] CHK027 - Are requirements defined for hook timeout behavior — what happens if the PostToolUse hook takes >2 seconds (the telemetry overhead budget)? Does Claude Code kill it, and if so, is the invocation lost? [Edge Case, plan.md §Performance Goals]
- [x] CHK028 - Are requirements defined for database corruption recovery beyond "recreate the database" — is there a WAL checkpoint strategy, and what happens to in-flight writes during corruption detection? [Edge Case, spec.md §Edge Cases]
- [x] CHK029 - Are requirements defined for concurrent optimization + telemetry writes — if `sio optimize` holds a long read transaction while hooks are writing, does WAL handle this, and are there deadlock/lock-timeout requirements? [Edge Case, Spec §FR-021]
- [x] CHK030 - Is the behavior defined when a user rates an invocation from a PREVIOUS session during batch review — does `label_latest` still work, or is session_id scoping an issue? [Edge Case, Spec §FR-006]

## Non-Functional Requirements Specificity

- [x] CHK031 - Is "<100MB disk per platform DB" (plan.md) a hard requirement with enforcement (auto-purge, compaction) or a soft estimate? What happens when the limit is exceeded? [Measurability, plan.md §Constraints]
- [x] CHK032 - Is "feedback entry <1s" measured from keypress to DB write, or from keypress to visual acknowledgment? Is there a requirement for visual feedback to the user that the label was saved? [Clarity, plan.md §Performance Goals]
- [x] CHK033 - Are offline-capability requirements fully specified — does "no cloud dependencies for core loop" include the external embedding API fallback, or must the core loop work when the API is unreachable? [Clarity, plan.md §Constraints]

## Notes

- Check items off as completed: `[x]`
- Add findings or decisions inline as you work through each item
- Items tagged `[Gap]` indicate missing requirements that should be added before implementing
- Items tagged `[Clarity]` indicate existing requirements needing tighter specification
- Items tagged `[Consistency]` indicate cross-artifact alignment issues to resolve
- 80%+ items include traceability references to spec sections or artifact locations
