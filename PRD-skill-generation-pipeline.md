# PRD: SIO Skill Generation Pipeline — From Patterns to Agent Skills

## Problem Statement

SIO v3.0 collects comprehensive session data (errors, positive signals, velocity, violations, patterns) but the "last mile" is weak: it generates imperative one-liner rules for CLAUDE.md but cannot produce structured Claude Code **skill files** that teach the agent multi-step workflows. The stop hook writes minimal pattern dumps to `~/.claude/skills/learned/` that contain raw error text — not actionable behavior guidance.

Meanwhile, users have two unmet needs:
1. **"The agent keeps skipping step 3"** — SIO can detect the error but can't teach the agent the correct ordered workflow
2. **"Auto-generate a specialized skill for this repo"** — SIO can identify repo-specific patterns but can't produce a skill file the agent actually follows

## Vision

SIO becomes a **skill factory**: it mines session data, identifies what works and what fails, and generates structured Claude Code skill files that the agent harness loads and follows. DSPy optimizes the generation over time based on velocity feedback (did the generated skill actually reduce errors?).

## Core Principle

> **SIO produces, the agent harness consumes.** SIO never enforces at runtime — it generates better instructions. The agent follows them because they're in skills/ and CLAUDE.md.

## Feature Requirements

### FR-001: Session-Start Consultant Skill
- A Claude Code skill (`~/.claude/skills/sio-consultant.md`) that fires at session start
- Reads SIO's SQLite DB for: recent violations, declining velocity, budget warnings, high-confidence unreviewed suggestions
- Injects a brief context block into the session: "Heads up: you've been getting Edit failures in this repo — always Read first"
- Silent mode (writes to CLAUDE.md preamble) or spoken mode (surfaces to user)
- Must complete in <3 seconds (reads DB only, no LLM)

### FR-002: Structured Skill File Generator
- New module `src/sio/suggestions/skill_generator.py`
- Takes a graded pattern (strong/established) and generates a complete Claude Code skill file with:
  - **Trigger conditions**: "When working on [repo/filetype/task]..."
  - **Ordered steps**: "1. Read the file first, 2. Check imports, 3. Edit, 4. Run tests"
  - **Guardrails**: "NEVER edit without reading", "ALWAYS run tests after changes"
  - **Examples**: Real examples from positive sessions where the workflow succeeded
- Template-based fallback (no LLM needed) + DSPy-enhanced mode
- Output goes to `~/.claude/skills/learned/<pattern-slug>.md`

### FR-003: Flow-to-Skill Promotion
- When a flow pattern (from `flow_events`) reaches HIGH confidence across 5+ sessions:
  - Extract the tool sequence as ordered steps
  - Cross-reference with positive signals (which steps got approval)
  - Cross-reference with error patterns (which deviations caused failures)
  - Generate a skill file encoding the successful workflow
- CLI: `sio promote-flow <flow-id>` or automatic via grader

### FR-004: DSPy-Optimized Skill Generation
- Create a DSPy `SkillGeneratorModule` signature:
  - Input: pattern description, error examples, positive examples, flow sequence
  - Output: trigger_conditions, ordered_steps, guardrails, examples
- Train on ground truth: manually-written good skills vs SIO-generated skills
- Velocity feedback loop: if a generated skill reduces its target error rate within 5 sessions, mark as positive training example; if not, mark as negative
- Reuse existing DSPy infrastructure (lm_factory, optimizer, module_store)

### FR-005: Repo-Specific Skill Discovery
- `sio discover --repo .` scans session history for the current repo
- Groups patterns by: file types touched, tools used, error types
- Proposes skill candidates: "You have 3 recurring Edit failures in .py files — generate a Python editing skill?"
- User approves → skill generated → installed to project-level `.claude/skills/`

### FR-006: Skill Effectiveness Tracking
- After a skill is generated and deployed, track its effectiveness via velocity
- New column on suggestions: `skill_file_path` linking to the generated skill
- `sio velocity --skills` shows per-skill error rate changes
- Skills that don't reduce errors after 10 sessions are flagged for review or removal

## Non-Requirements
- No runtime enforcement (SIO doesn't block or intercept)
- No web UI (CLI + generated files only)
- No multi-agent orchestration (single agent, single harness)

## Success Criteria
1. Generated skill files follow Claude Code skill format and are loaded by the agent
2. At least 3 skill types can be generated: tool-specific, workflow-sequence, repo-specific
3. DSPy optimization improves skill quality (measured by velocity) within 10 training rounds
4. Consultant skill injects relevant context at session start in <3 seconds
5. Skills that don't reduce errors are automatically flagged within 10 sessions
