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

### Key Entities

- **DSPy Signature**: Defines the input/output contract for suggestion generation — what the LLM receives (error examples) and what it must produce (structured rule).
- **DSPy Module**: The ChainOfThought wrapper that adds reasoning before generation — the "brain" that analyzes patterns before writing rules.
- **Metric Function**: Evaluates rule quality on a 0-1 scale — used both for scoring user-facing confidence and for training DSPy optimizers.
- **LLM Configuration**: User-editable settings specifying which model to use, credentials, and generation parameters.
- **Optimized Module**: A saved DSPy program (after BootstrapFewShot or MIPROv2 compilation) that contains learned few-shot examples and/or optimized instructions.
- **Training Dataset**: Approved suggestions (positive) and rejected suggestions (negative) used as training data for DSPy optimizers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Generated rules are qualitatively specific — 90% of rules reference at least one concrete detail from the error examples (tool name, error message snippet, or user context phrase) rather than generic placeholder text.
- **SC-002**: Users can configure the LLM backend in under 2 minutes by editing a single config file, with no code changes required.
- **SC-003**: The suggestion pipeline completes within 60 seconds for up to 20 patterns (including LLM calls), ensuring practical usability.
- **SC-004**: After optimization with 20+ labeled suggestions, the quality metric score of generated rules improves by at least 15% compared to the un-optimized default prompt.
- **SC-005**: Fallback to template mode works seamlessly — users without LLM access still get suggestions (at template quality) with a clear message explaining what LLM access would add.
- **SC-006**: SIO can successfully run on its own development history and produce at least 3 relevant, specific CLAUDE.md rules from its own error patterns.

## Assumptions

- The user has at least one LLM provider available (Azure OpenAI, Anthropic, OpenAI, or local Ollama). The system degrades gracefully without one.
- DSPy 3.1.3 APIs (Signature, ChainOfThought, BootstrapFewShot, MIPROv2, GEPA) remain stable. The research.md has verified all imports.
- Azure OpenAI with DeepSeek-R1-0528 is the primary deployment target (confirmed working with `dspy.LM`).
- The existing v2 pipeline (mine → cluster → dataset) produces sufficiently structured data for LLM consumption — no changes needed to upstream stages.
- TOML is the configuration format (consistent with Python ecosystem conventions and easy to hand-edit).
- Error examples may contain sensitive data — sanitization is required before LLM submission.
- Template-based generation (the current system) is preserved as the fallback — it is not deleted, just demoted.
