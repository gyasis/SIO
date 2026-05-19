# Use Case: The Rule Lifecycle — When to Codify, When to Let Go

**Scenario class:** You just got bit by the same agent mistake for the third time this week. Your instinct is to write a CLAUDE.md rule. Sometimes that's right. Often it isn't. And the rules you wrote a month ago — are they still earning their keep, or are they quietly burning tokens and misleading future agents?

Rules are not free. Every rule loaded into context costs tokens forever. A stale rule is worse than no rule because it actively misleads. SIO is what turns rule-writing from a gut decision into a data-backed one.

---

## The story

The agent just deleted a file you didn't ask it to delete. Third time this month. You're tempted to slam a "NEVER DELETE FILES WITHOUT CONFIRMATION" rule into `~/.claude/CLAUDE.md` and move on.

Stop. Three questions first:

1. **Is this really a cluster, or did I just notice it three times by coincidence?**
2. **If I write the rule, will the agent actually follow it — or will I be back here in two weeks?**
3. **What happens in six months when the surface this rule covers no longer exists?**

SIO answers all three, empirically, from the JSONL session record. That's the lifecycle.

---

## The four states

```
PROPOSED  →  ACTIVE  →  DECLINING  →  RETIRED
   ↑           ↓            ↓            ↓
   └───────────┴────────────┴── (rewrite loop)
```

Each transition has a SIO surface that gates it. Skip the gate and the rule pile rots.

---

## Phase 1 — Detect (is this a cluster, or two coincidences?)

You think you have a pattern. SIO tells you whether you do.

```bash
/sio-scan --grep "<keywords>" --type repeated_attempt --recent 14
```

`repeated_attempt` is the signal — the agent tried the same thing 3+ times. If `/sio-scan` returns 1-2 sessions, you have an anecdote. If it returns 5+ sessions across 2+ weeks, you have a pattern.

**The cluster threshold (rough):**
- Fewer than 3 sessions, single project → not a rule. Save a `/session-note`, move on.
- 3-5 sessions, single project → project-level `CLAUDE.md` in the repo, not global.
- 5+ sessions across 2+ projects → global rule candidate. Proceed to Phase 2.

> **Rule of thumb:** the cluster justifies the rule. If you can't `/sio-scan` and show the cluster, you don't have evidence — you have a feeling.

---

## Phase 2 — Codify (suggest → review → apply)

You've confirmed the cluster. Now generate the rule text from the data, not from your memory of the failures.

```bash
/sio-suggest --grep "<same keywords>"
```

`/sio-suggest` reads the error cluster and drafts a CLAUDE.md rule grounded in the specific failure modes. It tends to be more precise than a hand-written rule because it cites the exact phrases the agent emitted before failing.

Then review before anything lands:

```bash
/sio-review
```

This is the gate. Read the draft. Apply the **three-part admission test** (per `~/.claude/CLAUDE.md` constitution and `~/.claude/rules/domains/memory.md`):

1. **Universal?** Applies across projects, not just this repo? If no → project `CLAUDE.md`, not global.
2. **Low-churn?** Will still be true in 3 months? If no → file note, not a rule.
3. **Behavioral?** Changes how the agent generates code or routes tools? If no → it's documentation, not a rule.

**All three YES → apply:**

```bash
/sio-apply --suggestion <id>
```

The rule routes to the correct tier (core CLAUDE.md, `~/.claude/rules/tools/<x>.md`, or `~/.claude/rules/domains/<x>.md`) based on scope. Tier matters — tool rules only load when the tool fires, so they don't cost tokens at idle.

State transition: PROPOSED → ACTIVE.

---

## Phase 3 — Audit (monthly, not eventually)

A rule that lands is not a rule that works. Two SIO surfaces tell you whether the rule is biting.

#### Is the agent actually following it?

```bash
/sio-violations --rule "<rule key>"
```

`/sio-violations` finds sessions in the last N days where the rule applied but was ignored. Two readings:

- **0-1 violations/month** → the rule is internalized. Good.
- **3+ violations/month, same rule** → the agent is reading the rule and ignoring it. Either the wording is too soft ("should" → "MUST"), the rule isn't loading in the right tier, or the cluster shifted under the rule.

#### Is the rule reducing errors?

```bash
/sio-velocity --rule "<rule key>"
```

`/sio-velocity` measures the error rate on the original cluster **before** vs **after** the rule landed. This is the empirical post-condition.

- **Velocity positive, sustained for 4+ weeks** → rule is doing its job. Leave it.
- **Velocity flat for 4+ weeks** → the rule isn't moving the metric. State transition: ACTIVE → DECLINING.
- **Velocity negative** → errors got worse after the rule. The rule may be steering the agent into a new failure mode. Stop, investigate.

> Run the audit once a month. Quarterly is too slow — stale rules accumulate fast.

---

## Phase 4 — Retire (the hardest phase)

A rule in DECLINING needs a decision. Two paths:

#### Path A: Rewrite (cluster is real, wording is wrong)

The cluster `/sio-scan` originally identified is still present, but `/sio-velocity` is flat. The rule isn't catching it. Re-run `/sio-suggest` with fresh data — the agent's failure phrasing may have drifted, and the new draft will cite current evidence.

#### Path B: Delete (cluster is gone, rule is obsolete)

Re-run the original `/sio-scan --grep "<keywords>"`. If the cluster has dropped to zero for 4+ weeks, the rule is solving a problem that no longer exists. **Delete it.**

Deletion feels harder than writing because deleting *feels* like losing institutional knowledge. It isn't. The JSONL transcript and the `/sio-velocity` history are the institutional knowledge. The rule is just a runtime hint. When the surface it covers is gone (tool deprecated, MCP server removed, workflow replaced), the rule is dead weight.

State transition: DECLINING → RETIRED. Delete the rule, note the date in a commit message, move on.

---

## Phase checklist (gate criteria)

### Phase 1 — Detect
```bash
/sio-scan --grep "<keywords>" --type repeated_attempt --recent 14
```
**Gate:** 5+ sessions across 2+ projects → proceed to Phase 2. Else → `/session-note` and stop.

### Phase 2 — Codify
```bash
/sio-suggest --grep "<keywords>"
/sio-review
/sio-apply --suggestion <id>
```
**Gate:** passes 3-part admission test (universal, low-churn, behavioral) → apply. Else → file memory or project-level rule.

### Phase 3 — Audit (monthly)
```bash
/sio-violations --rule "<rule key>"
/sio-velocity --rule "<rule key>"
```
**Gate:** violations low AND velocity positive → ACTIVE. Else → DECLINING.

### Phase 4 — Retire
```bash
/sio-scan --grep "<original keywords>" --recent 30
```
**Gate:** cluster still present → rewrite (Path A). Cluster gone → delete (Path B).

---

## Why this loop matters

Rules are forever unless you audit them. Every CLAUDE.md rule the agent reads on every session is a recurring tax — small per session, large in aggregate. A `~/.claude/CLAUDE.md` that grew to 800 lines by accretion isn't a constitution, it's a museum.

The constitution caps it at 200 lines for exactly this reason (per `~/.claude/CLAUDE.md` § "CLAUDE.MD CONSTITUTION"). The cap only works if there's a retirement path. SIO is that path.

**The data-backed loop:**
- A new rule has to clear a `/sio-scan` cluster.
- An old rule has to clear `/sio-velocity` and `/sio-violations`.
- Neither is judgment. Both are evidence.

When the loop is closed, the rule set stays small, sharp, and current. When the loop is open, the rule set grows, contradicts itself, and stops being read.

---

## What this use case is *not*

- **Not a substitute for reading what the rule actually says.** `/sio-velocity` tells you the rule is biting; it doesn't tell you it's biting *correctly*. A rule that fixes errors by steering the agent into a worse pattern still scores positive velocity. Read the rule.
- **Not a substitute for human judgment on universal-vs-project-specific.** SIO can tell you a cluster exists across 5 sessions. It can't tell you the cluster is universal or just an artifact of one weird repo. That's your call — the admission test is yours, not the tool's.
- **Not real-time.** Velocity needs 4+ weeks to be a stable read. Don't audit a rule the week after you wrote it; you'll get noise.
- **Not a license to skip `/paired-debate` on architectural rules.** A rule that changes how the agent reasons (not just how it routes tools) deserves a debate, not just a `/sio-suggest` draft.

---

## Cross-references

- `~/.claude/CLAUDE.md` — the constitution (200-line cap, tiered rule routing, admission test)
- `~/.claude/rules/tools/sio.md` — full SIO skill map and routing
- `~/.claude/rules/domains/memory.md` — admission test for behavioral-vs-memory
- `docs/SIO_PHILOSOPHY.md` — why SIO is "measured assist, not autonomous override"
- `docs/use-cases/validating-a-config-change.md` — the sibling loop for infra changes
- `docs/user-guide.md` — every CLI surface
