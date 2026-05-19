# Use Case: Pre-Merge PR Safety on Shared Infrastructure

**Scenario class:** You've opened a PR that touches a piece of code lots of downstream systems depend on — a shared model, a hook, an MCP server wiring, an auth helper, a schema migration. The diff is small. CI is green. Reviewers will nod. You still want a second opinion before merge, because the blast radius if this is wrong is "everyone's next session."

SIO is the **prior-art layer**. It answers "have we touched this surface before, and what broke when we did?" `/adversarial-audit` is the **active layer** — it answers "what's wrong with this diff right now?" Use both. SIO goes first; adversarial-audit consumes its output.

---

## The story

You're on a small team. The PR modifies `<file path>` — a file that gets imported by half the agent stack. There are no behavioral tests covering the exact code path you touched, because the file is infrastructure: it gets exercised implicitly by every other workflow. The reviewer's mental model of "what could break" is bounded by their memory of recent incidents. Yours is too.

That's the gap. The institutional memory of "we tried something like this two months ago and it silently broke X" lives in session transcripts, not in anyone's head and not in the PR description. SIO is the only thing that reads those transcripts at scale.

The question is: **before I hit Merge, what do prior sessions say about this surface?**

That's where SIO comes in — as a prior-art sweep that feeds into the actual bug hunt.

---

## The six SIO surfaces you'd actually use

These map onto three phases of pre-merge review: **establish prior art**, **cross-check against codified rules**, and **hand off to adversarial-audit with context**.

### Phase 1 — Establish prior art (history of this surface)

#### 1. Every prior session that touched this file or symbol

```bash
session-search "<file/symbol names from diff>" --all --files
```

`session-search` reads JSONL session transcripts directly — free, fast, exhaustive. `--all` because for pre-merge review you want the full history of the surface, not just the last 7 days. `--files` first so you can pick which sessions to read in detail.

Look for:
- Sessions where the same file was edited and shipped cleanly → confirms the surface is well-trodden.
- Sessions where the same file was edited and immediately reverted → that's a buried incident. Read it.
- Sessions that touched adjacent symbols (`<feature surface>`-related) → expands the blast radius your diff might inherit.

> **Rule of thumb:** never merge a shared-infra PR without `session-search --all --files` on the changed files and the primary symbols. The cost is one query; the upside is "oh, this exact line was rolled back in March."

#### 2. Is there a codified pattern for this kind of change?

```
/sio-recall "<feature surface>"
# e.g. "shared hook ordering" or "MCP entry add with backup"
```

`/sio-recall` runs over distilled, curated playbooks — past successful flows that have been compressed into reusable patterns. If your diff matches a codified pattern, you can compare your implementation step-for-step. If your diff *contradicts* a codified pattern (e.g. the playbook says "always emit a `.bak` sibling before atomic rename" and your diff skips that step), stop and justify the deviation in the PR description before merge.

If there's no codified pattern for this surface, that's a signal: this is high-blast-radius work that hasn't been formalized. Treat it as such.

#### 3. What does a working tool sequence on this surface look like?

```
/sio-flows --query "<feature surface>"
```

`/sio-flows` mines raw recurring tool sequences across all sessions — the "what successful sessions actually look like" view. Unlike `/sio-recall` (curated), `/sio-flows` shows the unedited pattern. Useful for: "the curated playbook says do A → B → C, but every session that actually shipped did A → C → B → verify. Why?"

The gap between flows and recall is often where the real institutional knowledge lives.

### Phase 2 — Cross-check against codified rules

#### 4. What errors clustered on this surface historically?

```
/sio-scan --grep "<keywords from the diff>" --recent 30
```

`/sio-scan` mines the last N days of sessions for error events, `repeated_attempt` markers, and `user_correction` events. Run it scoped to the keywords of your diff — file names, symbol names, the names of any new identifiers you're introducing.

- **Zero clusters** → the surface is quiet. Lower risk.
- **One cluster of known errors** → expected; check that your diff doesn't reintroduce them.
- **A cluster you don't recognize** → read it. This is the failure mode that catches "we tried this two months ago and it broke" before merge.

Bring the cluster summary into the PR description as "Prior incidents on this surface" so reviewers see it too.

#### 5. Do any active CLAUDE.md rules cover this surface?

```
/sio-violations --rule "<rule key>"
```

If there are rules in CLAUDE.md that touch your surface (e.g. "always pass `_slug` to MCP calls", "never use `SELECT *`", "atomic rename + `.bak` sibling on config writes"), `/sio-violations` shows whether sessions in the last N days have been honoring them.

Two failure modes to catch pre-merge:
- Your diff violates an active rule → the rule will start firing on you. Either fix the diff or update the rule with a justification in the PR.
- The surface has a rule but it's already being violated regularly → the rule is dead and your diff might be reinforcing the wrong behavior. Flag it.

### Phase 3 — Hand off to `/adversarial-audit` with SIO findings as context

#### 6. Feed prior art into the formal bug hunt

```
/adversarial-audit
```

`/adversarial-audit` is the three-phase formal review: parallel adversarial agents (general sweep + targeted hunt), then `/paired-debate` adjudication of each finding, then a verdict table (CONFIRMED / REFUTED / NEEDS-TEST / INTENTIONAL).

SIO's job is to **arm adversarial-audit with prior-art context** before it runs:

- The session IDs from Phase 1 → adversarial-audit can read the prior incident sessions as part of its sweep.
- The error clusters from `/sio-scan` → become targeted hunt queries ("verify this PR doesn't reintroduce <cluster pattern>").
- The codified pattern from `/sio-recall` → becomes a deviation check ("does this diff deviate from the codified flow, and is the deviation justified?").
- The rule list from `/sio-violations` → becomes a compliance check.

This is the multiplier. Adversarial-audit alone is "what's wrong with this diff?" SIO + adversarial-audit is "what's wrong with this diff *in the context of every prior session that touched this surface*?"

---

## Putting it together — a concrete checklist

### Phase 1: Prior art (before requesting review)

```bash
# 1. Full history of the surface
session-search "<file path>" --all --files
session-search "<primary symbol>" --all --files

# 2 + 3 (parallel — run in one message)
/sio-recall "<feature surface>"
/sio-flows  --query "<feature surface>"
```

**Gate:** you've read at least the top 2 sessions from `session-search` and compared your diff against `/sio-recall` and `/sio-flows`. Note any deviations in the PR description.

### Phase 2: Codified-rule cross-check

```bash
# 4. Error cluster history
/sio-scan --grep "<keywords from diff>" --recent 30

# 5. Active rule compliance
/sio-violations --rule "<rule key>"
```

**Gate:** no unknown error clusters on this surface in the last 30 days, and no active-rule violations introduced by the diff. If either fires, resolve before requesting review.

### Phase 3: Adversarial-audit handoff

```
/adversarial-audit
```

Provide SIO findings as input context: prior session IDs, error cluster summaries, codified pattern reference, rule compliance status.

**Gate:** adversarial-audit verdict table has zero CONFIRMED findings AND every NEEDS-TEST item has a concrete test plan. Then — and only then — request human review.

---

## Why this loop matters

The thing SIO catches that nothing else catches pre-merge: **"we already solved this, and the solution is in a session transcript no one remembers."** Reviewers operate on recent memory. CI tests cover what someone thought to test. Adversarial-audit covers what's logically wrong with the diff *as written*. None of those layers reach into "what broke last time someone tried this exact change."

For shared infrastructure, the cost of a silent regression is multiplied by every downstream consumer that picks up the new behavior. The 30 seconds of prior-art lookup converts a class of incidents from "we'll find out in a week when sessions start failing" into "we caught it before merge because session-search surfaced the March rollback."

If your PR touches anything imported by more than a couple of consumers — shared models, hooks, MCP wiring, auth, schema, any config loaded at session start — this is the loop.

---

## What this use case is *not*

- **Not a replacement for human reviewers.** SIO surfaces prior art; reviewers judge intent, design, and team context. Both matter.
- **Not a replacement for CI / unit tests.** SIO observes the agent's behavior across sessions, not the code's correctness on a fixed input. Write the tests separately.
- **Not a replacement for `/adversarial-audit`.** SIO is the prior-art layer; adversarial-audit is the active bug hunt. SIO finds what broke last time; adversarial-audit finds what's wrong now. The combo is the point — don't skip the formal audit because SIO came back clean.
- **Not real-time.** The signal is only as fresh as the most recent indexed sessions. If the relevant prior incident was last week, you'll find it; if it was yesterday on an un-indexed session, you might not.

---

## Cross-references

- `docs/use-cases/validating-a-config-change.md` — the post-apply complement to this pre-merge loop
- `docs/SIO_PHILOSOPHY.md` — why SIO is "measured assist, not autonomous override"
- `docs/user-guide.md` — every CLI surface
- `~/.claude/skills/adversarial-audit.md` — the formal three-phase audit this loop hands off to
- `~/.claude/skills/paired-debate.md` — the adjudication step inside adversarial-audit
- `~/.claude/rules/tools/sio.md` — when to route the user to which SIO skill
