# Feature Specification: DSPy Suggestion Engine

**Feature Branch**: `003-dspy-suggestion-engine`
**Created**: 2026-02-26
**Status**: Draft
**Input**: Replace the fake string-template suggestion generator with real DSPy Signatures and Modules that use an LLM to generate targeted CLAUDE.md rules, skill updates, and hook configs from mined error patterns. This is the CORE of SIO — the entire project exists for this.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - LLM-Generated Improvement Rules from Error Patterns (Priority: P1)

As an AI CLI user, when I run the suggestion pipeline (`sio suggest`), the system uses an LLM to analyze my actual error examples and generate specific, actionable CLAUDE.md rules — not generic template text.

**Why this priority**: This is the entire reason SIO exists. Without LLM-powered suggestion generation, the pipeline produces boilerplate string templates that don't meaningfully improve agent behavior. Every other SIO feature (mining, clustering, datasets, review, apply) feeds into this moment — where intelligence meets data.

**Independent Test**: Can be fully tested by mining 10+ errors of any type, running `sio suggest`, and verifying the generated rules reference specific details from the actual errors (tool names, error messages, user contexts) rather than generic placeholder text. The generated rule text should be qualitatively different for different error patterns — two distinct tool_failure clusters should produce two distinct rules.

**Acceptance Scenarios**:

1. **Given** a dataset of 5+ tool_failure errors for the same tool (e.g., `Bash` returning "permission denied"), **When** the user runs `sio suggest`, **Then** the system calls an LLM via DSPy to produce a CLAUDE.md rule that specifically addresses the permission pattern — referencing the tool name, the error message pattern, and a concrete prevention instruction.
2. **Given** a dataset of 5+ user_correction errors where the user repeatedly said "wrong file", **When** the user runs `sio suggest`, **Then** the generated rule contains an instruction to confirm file paths before editing, derived from the actual correction text — not a generic "verify preconditions" template.
3. **Given** two different error patterns (one about timeout failures, one about syntax errors), **When** suggestions are generated for both, **Then** the two rules are substantively different in content and recommendations, demonstrating pattern-specific reasoning.
4. **Given** the LLM backend is unavailable or returns an error, **When** the user runs `sio suggest`, **Then** the system falls back to the existing template-based generation and informs the user that LLM generation was unavailable.
5. **Given** a pattern of MCP server timeouts (e.g., graphiti server unreachable 15 times across 4 sessions), **When** suggestions are generated, **Then** the DSPy module routes the improvement to `settings_config` (increase `MCP_TOOL_TIMEOUT`) or `mcp_config` (adjust server environment), NOT to CLAUDE.md as a generic behavioral rule.
6. **Given** a pattern of skill execution failures (e.g., memory-search skill hitting budget limits), **When** suggestions are generated, **Then** the DSPy module targets the specific SKILL.md file with a budget adjustment recommendation.

---

### User Story 2 - Configurable LLM Backend (Priority: P1)

As an AI CLI user, I can configure which LLM powers suggestion generation without changing any code, so I can use whichever model I have access to (Azure OpenAI, Anthropic, local Ollama, etc.).

**Why this priority**: Tied P1 because the LLM integration is only useful if it connects to a real model. Users have different LLM providers — some have Azure, some have Anthropic API keys, some want free local models. Configuration must be dead simple.

**Independent Test**: Can be tested by creating a config file with model details, running `sio suggest`, and verifying the system uses the configured model. Then changing the config to a different model and verifying the system switches without code changes.

**Acceptance Scenarios**:

1. **Given** a configuration file at `~/.sio/config.toml` with `[llm]` section specifying model name and credentials, **When** the user runs `sio suggest`, **Then** the system uses the configured model for all LLM calls.
2. **Given** no config file exists, **When** the user runs `sio suggest`, **Then** the system uses a sensible default (check for available environment variables — AZURE_OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY — in priority order) and falls back to template mode if no LLM is available.
3. **Given** an invalid or expired API key in the config, **When** the user runs `sio suggest`, **Then** the system reports the LLM error clearly and falls back to template generation for that run.

---

### User Story 3 - Quality Scoring via LLM Metric (Priority: P2)

As an AI CLI user, each generated suggestion has a quality score that reflects how well the LLM-generated rule addresses the underlying error pattern, so I can prioritize which suggestions to review first.

**Why this priority**: Without quality scoring, all suggestions appear equally valuable. A metric function enables the DSPy optimizers (Story 4) to select better prompts over time, and helps users focus on the highest-impact suggestions first.

**Independent Test**: Can be tested by generating suggestions for patterns of varying quality (one with 20 errors across 5 sessions vs one with 3 errors in 1 session) and verifying that the confidence scores reflect the difference in evidence quality.

**Acceptance Scenarios**:

1. **Given** an LLM-generated rule for a pattern with strong evidence (many errors, multiple sessions, clear user feedback), **When** the quality metric is computed, **Then** the score reflects both the statistical evidence strength AND the rule's specificity (does it address the actual error content, or is it vague?).
2. **Given** an LLM-generated rule that merely restates the error without actionable prevention guidance, **When** the quality metric evaluates it, **Then** the score is lower than a rule that provides specific, actionable instructions.
3. **Given** multiple candidate rules for the same pattern, **When** the metric scores all candidates, **Then** the scores allow ranking from best to worst quality.

---

### User Story 4 - DSPy Optimizer Integration (Priority: P2)

As an AI CLI user, the system can optimize its own suggestion-generation prompts over time using DSPy's BootstrapFewShot and MIPROv2 optimizers, so that approved suggestions become training data for generating even better suggestions in the future.

**Why this priority**: This closes the self-improvement loop. Without optimization, the LLM uses a static prompt forever. With it, every approved/rejected suggestion teaches the system to generate better rules next time. This is the "self-improving" part of Self-Improving Organism.

**Independent Test**: Can be tested by approving 10+ suggestions (creating labeled training data), triggering an optimization run, and verifying the optimized prompt produces measurably better suggestions than the default prompt on a held-out test set.

**Acceptance Scenarios**:

1. **Given** 10+ approved suggestions (positive examples) and 5+ rejected suggestions (negative examples), **When** the user runs `sio optimize suggestions`, **Then** the system uses BootstrapFewShot to compile an optimized suggestion-generation module with few-shot demonstrations drawn from the approved examples.
2. **Given** 50+ labeled suggestions (approved + rejected), **When** the user runs `sio optimize suggestions --optimizer miprov2`, **Then** the system uses MIPROv2 to optimize both the instruction text and few-shot examples, producing a measurably higher-scoring module.
3. **Given** an optimized module exists, **When** the user runs `sio suggest`, **Then** the system uses the optimized module instead of the default, and the generated suggestions score higher on the quality metric than the default module would produce.
4. **Given** an optimization run completes, **When** the optimized module is saved, **Then** the user can view the before/after prompt diff and approve or reject the optimization.

---

### User Story 5 - SIO Runs on Itself (Priority: P3)

As a developer of SIO, I can point SIO at its own SpecStory/JSONL session history to mine errors that occurred while building SIO, generate improvement suggestions, and verify the DSPy pipeline works end-to-end on real data.

**Why this priority**: This is the definitive integration test. If SIO can improve itself, the pipeline works. This also serves as the first real-world dataset for optimizer training.

**Independent Test**: Can be tested by running `sio mine --since "30 days"` from the SIO project directory, then `sio suggest`, and verifying the generated rules are relevant to actual development errors encountered while building SIO.

**Acceptance Scenarios**:

1. **Given** SIO has been developed over multiple sessions with real errors, **When** the user mines SIO's own session history, **Then** errors from SIO development are captured in the database.
2. **Given** SIO's own errors are mined and clustered, **When** `sio suggest` runs with the DSPy engine, **Then** the generated CLAUDE.md rules are specific to SIO development patterns (e.g., "before editing generator.py, read the full file" or "verify DSPy imports before calling optimizer").
3. **Given** generated rules from self-analysis, **When** applied to the SIO project's CLAUDE.md, **Then** the rules are syntactically valid and could be read by Claude Code in future sessions.

---

### User Story 6 - Agent-Generated Synthetic Ground Truth (Priority: P1)

As an AI CLI user, the system uses the LLM itself to generate synthetic ground truth datasets — candidate ideal outputs for each error pattern — which I then review as a data analyst before they become DSPy training data. The human never writes ground truth from scratch; the agent proposes, the human validates.

**Why this priority**: P1 because DSPy cannot optimize without training data, and humans should not be writing ideal CLAUDE.md rules from scratch. The agent is better at generating many candidate outputs; the human is better at evaluating which candidates are good. This is the same pattern as RLHF — generate candidates, human ranks/selects. Without this step, DSPy has no labeled input→output pairs to learn from.

**Independent Test**: Can be tested by running `sio ground-truth generate` on existing error patterns, verifying it produces 3-5 candidate ideal outputs per pattern, then reviewing and approving the best ones. The approved ground truth should then be usable as DSPy training data.

**Acceptance Scenarios**:

1. **Given** error datasets exist with clustered patterns, **When** the user runs ground truth generation, **Then** the system uses the LLM to produce 3-5 candidate ideal outputs per error pattern — each candidate is a complete improvement (rule, skill update, hook config, etc.) targeting the appropriate surface.
2. **Given** candidate ground truth has been generated, **When** the user enters review mode, **Then** the system presents each candidate like a data analyst would see it: the error pattern summary, the candidate output, the target surface, and a quality assessment — allowing approve, reject, or edit.
3. **Given** a user approves a ground truth candidate, **When** approval is recorded, **Then** the (error_pattern → ideal_output) pair joins the training corpus as a positive `dspy.Example`.
4. **Given** a user rejects a candidate with a note, **When** rejection is recorded, **Then** it becomes a negative training signal. If the user provides an edited version, that edit becomes the positive ground truth.
5. **Given** the ground truth corpus grows through approve/reject cycles, **When** DSPy re-optimizes, **Then** the optimizer draws from these human-validated examples to improve its few-shot demonstrations and instruction text.

---

### User Story 7 - Automated and Human-in-the-Middle Modes (Priority: P2)

As an AI CLI user, I can choose between two pipeline modes depending on whether I want speed or control: a fully automated mode for high-confidence patterns, and a human-in-the-middle mode for analysis, discussion, and careful dataset curation.

**Why this priority**: Different situations need different levels of human involvement. When SIO has high-confidence patterns with strong evidence (20+ errors, 5+ sessions), automation is appropriate. When patterns are novel, ambiguous, or high-impact (changes to hooks, MCP configs), the human should be in the loop reviewing datasets, discussing trade-offs, and making informed decisions. Both modes feed the same DSPy training pipeline.

**Independent Test**: Can be tested by running the automated mode on a well-established pattern (many errors, clear fix) and verifying it produces and applies a suggestion without human intervention. Then running the HITL mode on a novel pattern and verifying it pauses for human review at each stage.

**Acceptance Scenarios**:

1. **Given** a pattern with 20+ errors across 5+ sessions and a confidence score above 0.8, **When** the user runs `sio suggest --auto`, **Then** the system generates ground truth candidates, auto-selects the highest-scoring one, generates the suggestion, and presents it for a single approve/reject decision — no multi-step review required.
2. **Given** a novel pattern or one targeting a high-impact surface (hooks, MCP config, settings), **When** the user runs `sio suggest --analyze`, **Then** the system enters human-in-the-middle mode:
   - Step 1: Presents the error dataset as a data analysis summary (error distribution, session timeline, common tools, user contexts)
   - Step 2: Generates ground truth candidates and pauses for human review
   - Step 3: After human validates ground truth, generates the suggestion
   - Step 4: Presents the suggestion with full reasoning trace for discussion
   - Step 5: User approves, rejects with edits, or defers
3. **Given** the user wants to inspect the training dataset before any optimization, **When** the user runs `sio datasets inspect <pattern_id>`, **Then** the system shows the full dataset: error examples, ground truth corpus entries (if any), label distribution, quality metrics, and coverage gaps.
4. **Given** the default mode (no flag), **When** the user runs `sio suggest`, **Then** the system auto-selects the appropriate mode per pattern based on confidence score and target surface impact — high-confidence + low-impact → auto, everything else → HITL.

---

### Edge Cases

- What happens when the LLM generates a rule that is too long (>500 lines)? The system must truncate or summarize.
- What happens when the LLM generates identical rules for different patterns? The system must deduplicate.
- What happens when the error dataset contains sensitive data (API keys, passwords in error messages)? The system must sanitize before sending to LLM.
- What happens when the LLM returns malformed output (not valid markdown, missing required sections)? The system must retry once, then fall back to templates.
- What happens when the user has no LLM API key and no local model? The system must clearly explain what's needed and continue with template fallback.
- What happens when the optimization dataset is too small for MIPROv2 (needs 50+ but only has 15)? The system must automatically select BootstrapFewShot instead.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST define DSPy Signatures for suggestion generation that take error examples (error_text, error_type, tool_name, user_message, context) as input and produce structured rule output (rule_title, prevention_instructions, rationale).
- **FR-002**: System MUST implement a DSPy Module (using ChainOfThought) that reasons about error patterns before generating rules — the reasoning trace must be inspectable for debugging.
- **FR-003**: System MUST implement a metric function that scores generated rules on: specificity (references actual error content), actionability (contains concrete prevention steps), and relevance (addresses the root cause shown in examples).
- **FR-004**: System MUST support configuring the LLM backend via `~/.sio/config.toml` with sections for model name, API credentials, and generation parameters (temperature, max_tokens).
- **FR-005**: System MUST auto-detect available LLM providers from environment variables when no config file exists, checking in order: AZURE_OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY.
- **FR-006**: System MUST fall back gracefully to the existing template-based generator when no LLM is available, with a clear message to the user.
- **FR-007**: System MUST integrate with the existing pipeline — the `sio suggest` command must call DSPy instead of string templates, while mine → cluster → dataset stages remain unchanged.
- **FR-008**: System MUST support BootstrapFewShot optimization when 10+ labeled suggestions exist (approved = positive, rejected = negative).
- **FR-009**: System MUST support MIPROv2 optimization when 50+ labeled suggestions exist.
- **FR-010**: System MUST automatically select the appropriate optimizer based on available training data volume.
- **FR-011**: System MUST persist optimized DSPy modules to disk so they survive across sessions and can be loaded on next `sio suggest` run.
- **FR-012**: System MUST sanitize error examples before sending to LLM — strip anything that looks like an API key, password, token, or secret (regex patterns for common credential formats).
- **FR-013**: System MUST truncate individual error examples to a reasonable size (max 500 characters per field) before sending to LLM to control costs and context usage.
- **FR-014**: System MUST log DSPy call traces (input, output, reasoning) for debugging when `--verbose` flag is passed to `sio suggest`.
- **FR-015**: System MUST generate rules in valid markdown format suitable for direct insertion into CLAUDE.md files.
- **FR-016**: System MUST NOT contain placeholder, stub, or simulated implementations in any production code path — every function in the suggestion generation pipeline must perform its actual intended work using real DSPy calls to a real LLM.
- **FR-017**: System MUST ship with 5-10 seed ground truth examples — agent-generated, human-validated input→output pairs where the input is a representative error pattern (one per error type) and the output is an ideal improvement targeting the appropriate surface. Seed examples are generated by the LLM during initial setup (`sio ground-truth seed`), reviewed by the user, and approved before becoming training data. These seed examples bootstrap DSPy before any user has approved suggestions.
- **FR-018**: System MUST automatically promote approved suggestions to the ground truth training corpus — when a user approves a suggestion, the (error_pattern → generated_rule) pair becomes a positive training example for future DSPy optimization.
- **FR-019**: System MUST record rejected suggestions as negative training signals — when a user rejects a suggestion with a note, the note and original output are stored so the metric function can penalize similar outputs in future optimization.
- **FR-020**: System MUST support user-edited ground truth — when a user rejects a suggestion but provides a corrected version, that corrected version becomes a positive ground truth example (the gold standard).
- **FR-021**: System MUST store ground truth examples in a structured format compatible with DSPy's `dspy.Example` objects, with fields for: input error examples, desired rule output, label (positive/negative), and source (seed/approved/edited/rejected).
- **FR-022**: System MUST use the ground truth corpus as the `trainset` parameter when calling BootstrapFewShot or MIPROv2 optimizers — the optimizer learns from real approved/rejected examples, not synthetic data.
- **FR-023**: The existing dataset builder output (JSON files with error examples) MUST remain the INPUT to the DSPy Signature — datasets provide what went wrong; ground truth provides what the ideal fix looks like.
- **FR-024**: System MUST support generating improvements for ALL agent behavior surfaces, not just CLAUDE.md. The DSPy Signature's output MUST include a `target_surface` field that routes the generated improvement to the correct file. Supported surfaces include:
  - `claude_md_rule` → `~/.claude/CLAUDE.md` or `<project>/CLAUDE.md` — behavioral rules, memory triggers, search strategies, preference overrides
  - `skill_update` → `~/.claude/skills/<name>/SKILL.md` — skill instructions, budget caps, escalation ladders, search strategies
  - `hook_config` → `~/.claude/hooks/<name>/*` — hook thresholds, timeout values, mode escalation, conditional logic
  - `mcp_config` → `~/.claude/mcp.json` — MCP server environment variables, timeout overrides, feature flags
  - `settings_config` → `~/.claude/settings.json` — tool timeouts (`MCP_TOOL_TIMEOUT`, `MCP_TIMEOUT`), effort levels, permission defaults
  - `agent_profile` → `~/.claude/agents/<name>.md` — agent specialization instructions, focus areas, output format guidance
  - `project_config` → `<project>/CLAUDE.md` — project-specific tech stack rules, command preferences, code style overrides
- **FR-025**: The DSPy Module MUST reason about WHICH surface is the correct target for a given error pattern — tool failures from MCP servers should route to `mcp_config` or `hook_config`, user corrections about tool routing should route to `skill_update`, repeated timeout errors should route to `settings_config`, etc. The surface selection MUST be part of the LLM's reasoning chain, not a hardcoded heuristic.
- **FR-026**: Seed ground truth examples (FR-017) MUST include at least one example per target surface type, so DSPy learns to route improvements to the correct file from the start.
- **FR-027**: The metric function (FR-003) MUST penalize suggestions that target the wrong surface — e.g., a suggestion about MCP timeouts should NOT be routed to CLAUDE.md as a behavioral rule when it belongs in settings.json as a timeout value.

### Key Entities

- **Target Surface**: Any file or configuration that influences Claude Code agent behavior. SIO maps 7 surface types covering the full agent behavior stack:
  - CLAUDE.md (global + project) — behavioral rules, memory strategy, preferences
  - Skills (SKILL.md) — tool routing, execution budgets, escalation logic
  - Hooks (JS/Python/Bash) — thresholds, cascade prevention, timeout values
  - MCP config (mcp.json) — server environment, API settings, feature flags
  - Settings (settings.json) — timeouts, permissions, effort levels
  - Agent profiles (agents/*.md) — specialization instructions, focus areas
  - Project config (project CLAUDE.md) — tech stack rules, code style
- **DSPy Signature**: Defines the input/output contract for suggestion generation — what the LLM receives (error examples) and what it must produce (structured rule, target surface, and rationale for surface selection).
- **DSPy Module**: The ChainOfThought wrapper that adds reasoning before generation — the "brain" that analyzes patterns before writing rules.
- **Metric Function**: Evaluates rule quality on a 0-1 scale — used both for scoring user-facing confidence and for training DSPy optimizers.
- **LLM Configuration**: User-editable settings specifying which model to use, credentials, and generation parameters.
- **Optimized Module**: A saved DSPy program (after BootstrapFewShot or MIPROv2 compilation) that contains learned few-shot examples and/or optimized instructions.
- **Ground Truth Corpus**: The collection of input→output pairs that DSPy trains on. Inputs are error pattern summaries; outputs are ideal improvements targeting the correct surface. Sources: (a) agent-generated seed examples reviewed and approved by the user during initial setup, (b) approved suggestions promoted automatically, (c) user-edited corrections from rejected suggestions. The human never writes ground truth from scratch — the agent proposes, the human validates. This is THE critical asset — it grows with every approve/reject cycle and directly improves future suggestion quality.
- **Training Dataset**: The ground truth corpus formatted as `dspy.Example` objects for optimizer consumption. Each example has: `error_pattern` (input), `ideal_rule` (output), `label` (positive/negative), `source` (seed/approved/edited/rejected).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Generated rules are qualitatively specific — 90% of rules reference at least one concrete detail from the error examples (tool name, error message snippet, or user context phrase) rather than generic placeholder text.
- **SC-002**: Users can configure the LLM backend in under 2 minutes by editing a single config file, with no code changes required.
- **SC-003**: The suggestion pipeline completes within 60 seconds for up to 20 patterns (including LLM calls), ensuring practical usability.
- **SC-004**: After optimization with 20+ labeled suggestions, the quality metric score of generated rules improves by at least 15% compared to the un-optimized default prompt.
- **SC-005**: Fallback to template mode works seamlessly — users without LLM access still get suggestions (at template quality) with a clear message explaining what LLM access would add.
- **SC-006**: SIO can successfully run on its own development history and produce at least 3 relevant, specific improvement suggestions from its own error patterns.
- **SC-007**: Generated suggestions target at least 3 different surface types (not just CLAUDE.md) — demonstrating that the DSPy module correctly routes MCP failures to mcp_config, timeout patterns to settings_config, and behavioral corrections to claude_md_rule.
- **SC-008**: Seed ground truth corpus covers all 7 target surface types with at least 1 example each, and the system generates valid suggestions for surfaces beyond CLAUDE.md within its first run.

## Assumptions

- The user has at least one LLM provider available (Azure OpenAI, Anthropic, OpenAI, or local Ollama). The system degrades gracefully without one.
- DSPy 3.1.3 APIs (Signature, ChainOfThought, BootstrapFewShot, MIPROv2, GEPA) remain stable. The research.md has verified all imports.
- Azure OpenAI with DeepSeek-R1-0528 is the primary deployment target (confirmed working with `dspy.LM`).
- The existing v2 pipeline (mine → cluster → dataset) produces sufficiently structured data for LLM consumption — no changes needed to upstream stages.
- TOML is the configuration format (consistent with Python ecosystem conventions and easy to hand-edit).
- Error examples may contain sensitive data — sanitization is required before LLM submission.
- Template-based generation (the current system) is preserved as the fallback — it is not deleted, just demoted.
