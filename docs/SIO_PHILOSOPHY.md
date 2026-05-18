# SIO Philosophy: Measured Assist, Not Autonomous Override

**Status:** Living document. Last updated 2026-05-18.
**Audience:** SIO users, contributors, and future agents reading this repo.

## TL;DR

SIO is a **suggestion engine with measured assist**, not an autonomous self-improving system. Detection, generation, and measurement are automatic. **Application, deprecation, and final judgment are human.** This is by design, not a gap.

```
  AUTOMATIC                              HUMAN
  ────────                              ─────
  Mine errors                          → Review suggestions
  Cluster patterns                     → Apply rules
  Generate rules (DSPy)                → Deprecate failing rules
  Measure rule outcomes                → Audit on demand
  Surface evidence                     → Make the call
```

The line is intentional. SIO trusts the user to be the editor of the agent's behavior; it just makes that editorship cheap, informed, and reversible.

## The Loop, Honestly

The implicit SIO promise is a closed self-improvement loop:

```
  ┌───────────────────────────────────────────────────────────┐
  │  1. Claude makes an error                                  │
  │  2. SIO mines JSONL → error_records                        │
  │  3. SIO clusters errors → patterns                         │
  │  4. SIO suggests rules (DSPy-generated)                    │
  │  5. Human reviews + applies  →  CLAUDE.md  / hooks         │
  │  6. Claude reads rules → fewer errors (hopefully)          │
  │  7. SIO measures the delta → velocity / rule-outcomes      │
  │  8. Human reads the data → keeps / deprecates / audits     │
  │  9. DSPy can be retrained on labeled (rule, outcome) pairs │
  │     IF the human curates the labels                        │
  └───────────────────────────────────────────────────────────┘
```

**Steps 1-4 and 7 are automatic.** Steps 5, 8, and the labeling in 9 are deliberately human-driven.

## Why human-in-the-loop is not a bug

Three reasons:

### 1. CLAUDE.md rules are guidance, not enforcement

A line in CLAUDE.md changes what the agent *reads*; it doesn't change what the agent *does*. The agent can ignore rules, misinterpret them, or apply them too broadly. A bad rule landing automatically could degrade the agent globally before the next measurement window closes.

The **hooks** path (via `sio promote-rule`) is real enforcement — but hooks fire on every call and need careful scope-gating. (See `prd/scratch/devkid_session_scope_gate_2026-05-17.md` for an example of why blind auto-firing hooks is its own failure mode.)

### 2. Measurement has irreducible lag

The current implementation requires ~2 weeks of organic rule churn before `sio velocity --by-rule` produces signal (see `src/sio/core/metrics/velocity.py`). Until then, every applied rule is a measurement-dark unknown. An autonomous SIO that applies-and-deprecates inside that window is making decisions on noise.

### 3. The metric ≠ the outcome

The DSPy `suggestion_quality_metric` scores rules on specificity + actionability + surface_accuracy. That's a *proxy* for "this rule is well-written." It is NOT a measure of "this rule reduces errors." Today (2026-05-18) we have no automatic outcome metric. Until we do, the human's judgment IS the outcome metric.

## What "measured assist" looks like

The human shouldn't be eyeballing 50,000 error records and 278 pending suggestions manually. SIO provides three surfaces of math-backed assist:

### Surface 1 — Overview: `sio velocity --by-rule`
Reads `error_records.active_rules` (the JSON snapshot column added 2026-05-15). For each rule active in the corpus, computes error rate by (target_surface, error_type) and groups by rule_id. Human reads the table; rules with positive deltas are candidates for keep, negative deltas for review.

### Surface 2 — Drill-down: `sio rule-outcomes <rule_id>`
Per-rule deep stats: when applied, n_errors_before vs n_errors_after on the rule's target_surface, confidence interval based on sample size, related rule IDs. The "is this rule actually doing what I hoped" view.

### Surface 3 — Audit on demand: `sio rule-audit <rule_id>`
The "I'm suspicious — dig in" command. Pulls error text samples from before and after the rule landed, surfaces the most-similar errors that STILL occurred after the rule, optionally runs an LLM judge ("does the rule's prevention_instructions actually apply to this error?"). The human reads the evidence and makes the call.

(Surfaces 2 and 3 are not yet implemented as of 2026-05-18 — see PRD `sio_rule_outcomes_audit_2026-05-18.md` for the spec.)

## Where SIO is and isn't

What works **today**:

- ✅ Mining (49K+ errors, 107K+ flows, 747K+ behavior_invocations)
- ✅ Clustering (multi-hop search with `--strategy filter|recluster|hybrid`)
- ✅ Suggestion generation (DSPy-optimized; GEPA top score 0.8653 as of 2026-05-18)
- ✅ Application (`sio apply --auto-threshold`)
- ✅ Reproducibility chain (trainsets table, content-hashing, `--resume-from`)
- ✅ Ladder discipline (Bootstrap → MIPROv2 → GEPA with `--skip-ladder` + `--skip-data-gate` overrides)
- ✅ Cost transparency (`~/.sio/usage.log`, `sio costs summary`)
- ✅ Observability (`@runlogged` on every CLI command + DSPy capture sidecars)

What's **scaffolded but unmeasured**:

- 🟡 `sio velocity --by-rule` (plumbing landed 2026-05-15; needs ~2 weeks of rule churn for first signal)
- 🟡 promote-rule → hook generator (shipped; usage-frequency data not yet collected)

What's **honest gap**:

- ❌ Per-rule outcome metric (`rule_effectiveness_score`) — not implemented
- ❌ `sio rule-outcomes` drill-down — not implemented
- ❌ `sio rule-audit` deep-dive — not implemented
- ❌ DSPy training input that includes (rule, outcome) pairs — the model learns from rule QUALITY, not rule OUTCOMES
- ❌ Auto-deprecation — **deliberately not built**. Auto-deprecation requires confidence in the outcome metric AND tolerance for false positives that briefly degrade the agent. We don't have either yet.

## Principles SIO enforces in code

(Cross-references to the SIO Constitution, currently at v1.7.0 with proposed XIV-XVI.)

| # | Principle | Status |
|---|---|---|
| XII | Cost Transparency & Model-Tier Choice | ACTIVE (v1.7.0) |
| XIII | Transparent Machine (observability) | ACTIVE (v1.7.0) — every CLI run produces a JSONL run-log |
| XIV (proposed) | Optimizer Ladder Discipline | ACTIVE in code (gates shipped 2026-05-17); pending constitutional ratification |
| XV (proposed) | Experimental Reproducibility | ACTIVE in code (trainsets table); pending constitutional ratification |
| XVI (proposed) | Background Runs Must Be Resumable | DRAFT (PRD `sio_background_persistence_design_2026-05-18.md`) |

## Anti-patterns SIO has explicitly chosen not to build

These are aspirational ideas that look attractive but conflict with the human-in-the-loop design:

1. **Auto-deprecate failing rules.** Would require trust in an outcome metric that doesn't exist yet AND tolerance for false-positive deprecations that briefly degrade the agent. Surface the evidence, let the human decide.

2. **Auto-promote successful flows to skills.** A skill is a contract about how Claude works. Adding skills automatically would introduce behavior the user didn't ask for. `sio promote-flow` requires explicit human invocation.

3. **A/B-test rules in production.** Tempting but hard to do without sample contamination. A rule fired for half the sessions can leak via shared `~/.claude/CLAUDE.md`. Defer until the outcome metric exists and isolation is solved.

4. **Re-train DSPy on outcome-driven labels automatically.** Would require the outcome metric AND an auto-labeler with a quality bar. Today the labeler is the human running `sio approve`. Keep it that way until the outcome metric is trustworthy.

## How to read this doc if you're a future SIO user / contributor

- If you're applying a rule for the first time: **expect a 2-week lag** before `sio velocity --by-rule` can tell you if it worked. Use your judgment.
- If you suspect a rule is misfiring: **run `sio rule-audit <id>`** (once shipped). Read the evidence. Decide manually.
- If you want SIO to "just figure it out": **that's not what this is.** SIO is an editor's assistant, not a self-driving system. The agent is the manuscript; you're the editor; SIO is the line-by-line reviewer.
- If you find yourself reaching for auto-deprecation or auto-promotion: **read this doc again and check whether the outcome metric is in place yet.** If not, you're trading a known-good (human) judgment for a known-noisy (machine) one.

## Cross-references

- `~/dev/projects/SIO/CLAUDE.md` — repo-level instructions enforced by hooks
- `~/dev/projects/SIO/.specify/memory/constitution.md` — formal constitution (v1.7.0)
- `~/dev/prd/scratch/sio_optimizer_ladder_2026-05-16.md` — ladder discipline (Tier 5 = ladder gate, gate the user CAN override but is logged)
- `~/dev/prd/scratch/sio_dataset_versioning_2026-05-16.md` — trainsets table + content-hashing
- `~/dev/prd/scratch/sio_background_persistence_design_2026-05-18.md` — crash-resilient compound `optimize-ladder` design
- (To be filed) `~/dev/prd/scratch/sio_rule_outcomes_audit_2026-05-18.md` — `sio velocity --by-rule` extension + `sio rule-outcomes` + `sio rule-audit` CLI commands

## Revision log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-05-18 | Initial draft. Captures the "measured assist, not autonomous override" design stance. Added in response to today's strategic audit conversation: human element is the FEATURE, math-backed surfaces are how we earn the human's trust over time. |
