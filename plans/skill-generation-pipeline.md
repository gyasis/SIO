# Implementation Plan: SIO Skill Generation Pipeline

**PRD**: `PRD-skill-generation-pipeline.md`
**Estimated effort**: 15-20 tasks, 1-2 sessions
**Dependencies**: v3.0 competitive enhancement (branch `001-competitive-enhancement`)

## Architecture

```
Existing SIO Pipeline (v3.0)          New Skill Pipeline
┌────────────────────────┐     ┌──────────────────────────────┐
│ error_records           │     │                              │
│ positive_records        │────>│  skill_generator.py          │
│ flow_events            │     │    ├─ template mode (free)   │
│ patterns (graded)      │     │    └─ DSPy mode (~$0.03)    │
│ velocity_snapshots     │     │           │                   │
│ violation reports      │     │           ▼                   │
└────────────────────────┘     │  ~/.claude/skills/learned/   │
                               │    ├─ python-editing.md      │
                               │    ├─ test-after-edit.md     │
                               │    └─ read-before-write.md   │
                               └──────────────────────────────┘
                                          │
                               ┌──────────▼──────────────────┐
                               │  velocity tracking           │
                               │  (did the skill reduce       │
                               │   errors? → DSPy feedback)   │
                               └──────────────────────────────┘
```

## Implementation Phases

### Phase 1: Skill File Template + Generator (FR-002)

**Files:**
- NEW: `src/sio/suggestions/skill_generator.py`
- NEW: `tests/unit/test_skill_generator.py`

**Tasks:**
1. Define Claude Code skill file format (study existing skills in `~/.claude/skills/`)
2. Create `_SKILL_TEMPLATE` — string.Template with sections: trigger, steps, guardrails, examples
3. Implement `generate_skill_from_pattern(pattern, positive_examples, flow_sequence) -> str`
   - Extract trigger conditions from pattern's tool_name and error context
   - Convert flow n-grams into ordered steps
   - Convert error patterns into guardrails (negate the error)
   - Pull real examples from positive_records where context matches
4. Implement `generate_skill_from_flow(flow_pattern, session_data) -> str`
   - Takes a high-confidence flow (tool sequence) and wraps it as a skill
5. Write to `~/.claude/skills/learned/<slug>.md`
6. Tests: pattern → skill, flow → skill, empty data → graceful fallback

### Phase 2: Session-Start Consultant Skill (FR-001)

**Files:**
- NEW: `src/sio/adapters/claude_code/skills/sio-consultant/SKILL.md`
- NEW: `src/sio/suggestions/consultant.py`
- NEW: `tests/unit/test_consultant.py`

**Tasks:**
7. Create `build_session_briefing(db) -> str` that queries:
   - Recent violations (last 7 days) → "Watch out: you've been violating 'Never use SELECT *'"
   - Declining velocity → "Rule #5 isn't working — Edit failures still at 0.25"
   - Budget warnings → "CLAUDE.md at 95/100 lines"
   - Pending high-confidence suggestions → "3 suggestions ready for review"
8. Create the SKILL.md that invokes `sio briefing` (new CLI command)
9. Add `sio briefing` CLI command — outputs the briefing as markdown
10. Tests: violations present → mentioned, clean slate → short "all good" message

### Phase 3: Flow-to-Skill Promotion (FR-003)

**Files:**
- MODIFY: `src/sio/clustering/grader.py`
- MODIFY: `src/sio/mining/flow_pipeline.py`
- NEW: `tests/unit/test_flow_promotion.py`

**Tasks:**
11. Add `promote_flow_to_skill(db, flow_hash) -> str` to grader.py
    - Query flow_events for the flow, get tool sequence
    - Query positive_records for sessions where this flow succeeded
    - Query error_records for sessions where deviations from this flow failed
    - Call `generate_skill_from_flow()` from Phase 1
12. Add `sio promote-flow <flow-id>` CLI command
13. Wire auto-promotion: when grader detects a flow at HIGH confidence in 5+ sessions, auto-generate skill
14. Tests: flow promotion produces valid skill, auto-promotion triggers at threshold

### Phase 4: DSPy Skill Optimization (FR-004)

**Files:**
- NEW: `src/sio/core/dspy/skill_module.py`
- MODIFY: `src/sio/core/dspy/signatures.py`
- NEW: `tests/unit/test_skill_module.py`

**Tasks:**
15. Define `SkillGeneratorSignature` in signatures.py:
    - Input: pattern_description, error_examples, positive_examples, flow_sequence
    - Output: trigger_conditions, ordered_steps, guardrails, examples
16. Implement `SkillGeneratorModule` extending `dspy.Module`
17. Create velocity feedback loop: after 5 sessions, check if target error rate decreased
    - Yes → label skill as positive training example
    - No → label as negative
18. Wire into existing DSPy training pipeline (`sio train --task skill`)
19. Tests: module produces valid output, training loop completes

### Phase 5: Discovery + Tracking (FR-005, FR-006)

**Files:**
- NEW: `src/sio/suggestions/discoverer.py`
- MODIFY: `src/sio/core/metrics/velocity.py`
- NEW: `tests/unit/test_discoverer.py`

**Tasks:**
20. Implement `discover_skill_candidates(db, repo_path) -> list[dict]`
    - Group patterns by file extension, tool type, error category
    - Rank by frequency × sessions
    - Return candidates with: description, pattern_ids, suggested_skill_type
21. Add `sio discover --repo .` CLI command
22. Add `skill_file_path` column to suggestions table
23. Add `sio velocity --skills` view showing per-skill effectiveness
24. Tests: discovery finds candidates, velocity tracks per-skill

## Config Additions

```toml
[skills]
auto_promote_flows = true          # Auto-generate skills from HIGH-confidence flows
auto_promote_threshold = 5         # Minimum sessions before flow promotion
consultant_mode = "silent"         # "silent" (inject context) or "spoken" (show to user)
skill_effectiveness_window = 10    # Sessions before flagging ineffective skills
```

## What This Does NOT Do

- No runtime enforcement (skills are instructions, not interceptors)
- No new hooks (existing 4 hooks are sufficient)
- No new DB tables (reuses existing patterns, flow_events, velocity_snapshots, suggestions)
- No web UI
