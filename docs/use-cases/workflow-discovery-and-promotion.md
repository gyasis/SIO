# Use Case: Discovering & Promoting a Workflow That's Already Working

**Scenario class:** Most SIO docs talk about errors, friction, and regressions. This one is the opposite. SIO also mines the **positive signal** — recurring tool sequences that succeed. When you keep running the same three skills in the same order to answer one question, or you just solved a hard problem with a clever sequence and don't want to lose it, the question becomes: *should this be one skill, and which version of it reflects what I actually do?*

This is the loop SIO is built for on the positive side: **mine prior sessions for what works, promote it into a reusable artifact, optimize the prompts inside it.**

---

## The story

You notice you've been doing the same thing three mornings in a row. You run `<skill-a>` to pull state, eyeball the output, run `<skill-b>` to enrich it, then run `<skill-c>` to format the result. Each one is useful on its own, but the combination is what you actually need. By the third morning you mutter "this should be one command."

Or the inverse: you just spent an hour debugging something gnarly, found a clever five-tool sequence that cracked it, and you can feel the next-week-version-of-you re-discovering it from scratch. You want to save the workflow without sitting down to write a skill file by hand.

The instinct in both cases is to open a blank `~/.claude/skills/<workflow>.md` and start drafting. **Don't.** Hand-scaffolded skills reflect what the agent *guesses* you do; mined patterns reflect what you *actually* do. The two diverge more than you'd expect — and the divergences are exactly where the hand-written skill goes stale within a month.

That's where the discovery → promotion loop comes in.

---

## Phase 1 — Discover (let SIO surface the pattern)

#### 1. What sequences am I actually running?

```
/sio-flows
```

`/sio-flows` mines recurring positive tool sequences across all your recent sessions. Output is a ranked list: each row is a sequence (`<tool-a>` → `<tool-b>` → `<tool-c>`), the frequency, the first-seen date, and a hint about whether it converged on a successful outcome. The top of the list is your actual daily rhythm, whether or not you've named it.

For most users the first few runs are a small revelation — there are 2-3 sequences you didn't realize were sequences. They were just "what I do."

#### 2. Are there repo-specific patterns worth codifying?

```
/sio-discover
```

`/sio-discover` is the targeted variant: it finds sequences scoped to the current repo (or to a domain you specify) and ranks them as **skill candidates**. The output is opinionated — not just "here are sequences," but "here are sequences that look like they want to be a skill, here's why, here's a draft name."

Run `/sio-flows` to see the landscape; run `/sio-discover` when you've decided the landscape has something promotable in it.

#### 3. Did I just solve something I want to preserve?

```
/sio-distill
```

If the trigger is "I just solved a clever thing" rather than "I keep doing this every morning," start with `/sio-distill`. It takes a long exploratory session and compresses it into a clean playbook — strips out the dead ends, keeps the path that worked, normalizes the tool calls. The output is the same shape as what `/sio-discover` returns, just sourced from one session instead of many.

> **Rule of thumb:** if it happened ≥3 times → `/sio-flows`. If it happened once but mattered → `/sio-distill`. Either way, you end up with a candidate before promoting.

---

## Phase 2 — Validate the pattern is real

A mined sequence is not automatically worth promoting. Three things make it real:

1. **Frequency.** Has it actually recurred? `/sio-flows` shows the count. Anything below 3 is probably a coincidence; 5+ in 14 days is a strong signal.
2. **Success rate.** Did the sequence converge on a useful answer, or did it terminate in a `user_correction` event ("no, that's not what I meant")? `/sio-flows` annotates this; if a "recurring" pattern is mostly the agent retrying because the user kept pushing back, you don't want to codify it.
3. **Variance.** Is the sequence stable, or does the tool order shift every time? Low variance → promote. High variance → the workflow is still exploratory, give it another week.

If you're unsure, look at the underlying sessions: `session-search "<keywords from the sequence>" --recent 14 --files` and read one or two transcripts end-to-end. Cheaper than promoting a brittle pattern.

---

## Phase 3 — Promote (turn the pattern into a skill)

#### 4. Convert a mined flow into a skill file

```
/sio-promote-flow
```

`/sio-promote-flow` takes a single mined flow (from `/sio-flows` or `/sio-discover`) and writes it out as a `~/.claude/skills/<workflow>.md` file. The skill file includes: the tool sequence, the prompts at each step extracted from the source sessions, a trigger description, and a `cross-references` block pointing at the originating session IDs (so future-you can audit it).

This is **not** an autonomous commit — `/sio-promote-flow` produces a draft and surfaces it for review. The mined sequence is ground truth, but the *prompts* and the *trigger phrasing* need a human pass before they go live.

#### 5. Or do the whole loop in one shot

```
/sio-codify-workflow
```

`/sio-codify-workflow` chains the three steps: `/sio-distill` (or `/sio-flows` if you specify a recurring pattern) → `/sio-promote-flow` → `/sio-optimize` (next phase). It pauses between steps for confirmation so you're never surprised by a write. Use this when you're confident in the candidate and just want to walk through the pipeline with checkpoints.

If the trigger is "save this session's workflow," `/sio-codify-workflow` is the right entry point. If the trigger is "I want to browse what patterns exist before committing to any," start with `/sio-flows` instead.

---

## Phase 4 — Optimize (DSPy on the prompts inside the skill) — optional

#### 6. Improve the prompts the skill uses

```
sio optimize --skill <skill name>
```

Once the skill exists, the prompts inside each step are first-draft text lifted from your sessions. They work because that's what worked in the original runs — but they can almost always be tightened. `sio optimize` runs DSPy (GEPA by default, or `--optimizer mipro` / `bootstrap`) on the prompts, using your historical successful runs as the training signal. The output is a revised version of the skill where each step's prompt has been compiled against the data.

This phase is optional. A promoted skill is useful immediately; optimization makes it better over time. Most users skip Phase 4 on the first promotion and circle back after the skill has been used 10-20 times — at that point there's enough usage data to make optimization meaningful.

---

## Checklist

### When you keep running the same N commands

```
/sio-flows                              # see your actual rhythm
/sio-discover                           # surface skill candidates for this repo
# validate: frequency ≥3, success-rate high, variance low
/sio-promote-flow                       # draft a skill from the chosen pattern
# human review the draft
sio optimize --skill <name>             # optional, after the skill has usage
```

### When you just solved something clever and want to save it

```
/sio-distill                            # compress the session into a playbook
/sio-codify-workflow                    # one-shot: distill → promote → optimize
# review at each checkpoint
```

### When you're about to hand-write a skill

```
/sio-flows                              # STOP — check what you actually do first
```

The check costs 30 seconds and frequently surfaces a pattern that's different — sometimes meaningfully different — from what you were about to write.

---

## Why this loop matters

Most "I should build a skill for this" intuitions are wrong, and the failure mode is silent: you write a skill, it sits there, you don't use it because subtly it doesn't match the actual flow, and six months later you delete it. The hand-scaffolded skill encoded the agent's *theory* of what you do, not the *evidence* of what you do.

Mined patterns flip the polarity. The data tells you which intuitions are real, which are aspirational, and which are stale habits worth replacing. The `/sio-flows` → `/sio-promote-flow` → `sio optimize` loop is the difference between writing skills by guess and writing them by ground truth.

There's a related rule in `~/.claude/rules/tools/sio.md`: **when the user describes inefficiency, route to flow skills.** That rule exists because the agent's default reflex on "I keep doing X" is to draft a new skill by hand. Don't. Check `/sio-flows` first.

---

## What this use case is *not*

- **Not magic.** Promoted skills still need a human review pass on the trigger description and the per-step prompts. `/sio-promote-flow` drafts; it does not commit autonomously.
- **Not a substitute for one-off skills.** Some skills exist for things that happen once a quarter (annual review, audit prep, migration). Those don't recur enough to mine. Hand-write them.
- **Not a substitute for thinking.** A high-frequency pattern can still be the wrong abstraction. Mining tells you it recurs; it doesn't tell you it *should* recur. If the data shows you running the same five-step workaround every morning, the right move might be to fix the underlying tool, not codify the workaround.
- **Not real-time.** `/sio-flows` needs sessions to mine. A pattern that emerged yesterday probably needs another few days of recurrence before it shows up reliably.

---

## Cross-references

- `~/.claude/rules/tools/sio.md` — the "SIO Is NOT Only About Errors" section and the "when user describes inefficiency, route to flow skills" routing rule
- `docs/use-cases/validating-a-config-change.md` — the sibling negative-signal use case
- `docs/SIO_PHILOSOPHY.md` — three signal types (errors, friction, positive flows) and why all three matter
- `docs/user-guide.md` — full CLI surface for every skill mentioned here
