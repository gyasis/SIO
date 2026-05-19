# Use Case: Validating a Risky Config Change Before & After Apply

**Scenario class:** You've written a small script that mutates a piece of agent infrastructure (an MCP server registry, a hook config, a shell rc fragment, a CI workflow file). It dry-runs cleanly. You want SIO to help you (a) sanity-check the assumptions before you flip `--apply`, and (b) catch regressions in the first few sessions after it lands.

This is the bread-and-butter loop SIO is built for: **mine prior sessions for ground truth, then mine future sessions for outcome.**

---

## The story

You have a script `regen-config-entries.py` that regenerates N entries in a JSON config file based on currently-active project slugs. It:

- Has a `--list` mode (read-only enumeration)
- Has a default dry-run mode (prints the ADD / REMOVE / UNCHANGED diff)
- Has an `--apply` mode (atomic write + `.bak.<tag>` sibling)
- Is idempotent

Dry-run looks correct. Three new entries would be added, zero modified, zero removed. The change is small and reversible — but the config file is loaded by every future agent session, so a mistake propagates silently. You want a second opinion before you commit.

You also have a parallel PRD describing a "wrapper-as-proxy" alternative if Option 1 (the slug-pinned entries this script writes) gets noisy in practice.

The question is: **how do I know whether Option 1 is the right call, before I burn a week on it?**

That's where SIO comes in.

---

## The eight SIO surfaces you'd actually use

These map onto the three phases of any risky change: **before**, **immediately after**, and **steady-state**.

### Phase 1 — Before `--apply` (validate assumptions)

#### 1. Did we do this exact pattern before?

```bash
session-search "<keywords of the change>" --recent 30 --files
```

`session-search` reads JSONL session transcripts directly — free, fast (~200ms), ground truth. If you wrote a similar script three weeks ago and it shipped fine, you'll see the session and can read what worked. If you wrote one and it broke, you'll see *that* too. Either way you stop guessing.

> **Rule of thumb:** never make a config-mutating change without `session-search` first. The cost is one line; the upside is "oh, we already solved this."

#### 2. Is there a codified workflow for this kind of change?

```
/sio-recall "<plain-English description of the workflow>"
# e.g. "idempotent JSON config edit with atomic rename and .bak sibling"
```

`/sio-recall` runs over distilled sessions — successful past flows that have been compressed into reusable playbooks. If the pattern exists, it returns the exact tool order (backup → write-temp → fsync → rename → verify) so you can compare your script's order against it. If it doesn't exist, that's a signal you might want to codify *this* session once the change lands (`/sio-codify-workflow`).

#### 3. What tool sequence has actually worked for changes like this?

```
/sio-flows --query "<surface being changed>"
# e.g. "mcp config entry add"
```

`/sio-flows` mines positive tool patterns — the "what successful sessions actually look like" view. Unlike `/sio-recall` (curated playbooks), `/sio-flows` shows raw recurring sequences across all sessions. Useful for: "am I doing this in the same order other sessions did, or am I about to deviate from a proven path?"

**Pre-apply combo (parallel, ~30 seconds):**
- `session-search` for literal text matches
- `/sio-recall` for codified playbook
- `/sio-flows` for raw tool-pattern evidence

If all three agree, ship. If two of three disagree with what your script does, stop and re-read the differences.

### Phase 2 — Immediately after apply (the first 24 hours)

#### 4. Did the change introduce any error cluster?

```
/sio-scan --recent 1 --grep "<keywords>"
```

`/sio-scan` mines the last N days of sessions for error events, repeated attempts, and `user_correction` markers. Run it 24 hours after the apply with grep tuned to the surface you changed.

- **0 hits** → the change is invisible to the agent, which is the goal.
- **A few hits, all expected** → noted, but acceptable (e.g. one stale session caught a transition).
- **A cluster** → roll back via the `.bak.<tag>` file you wrote, then read the cluster.

#### 5. Did the agent silently work around the new behavior?

```
/sio-scan --recent 1 --type user_correction --grep "<keywords>"
```

The non-error signal. If the user said "no that's not what I meant" or "use the other one instead" — that's a `user_correction` event. It means the agent's behavior is *technically working* but *semantically wrong*. This is the failure mode that gets ignored when you only look at exception logs.

> SIO captures three kinds of signal: errors, friction (corrections), and positive flows. Do not skip the friction check after a config change.

### Phase 3 — Steady-state (week 1+)

#### 6. Are agents actually using the new code paths?

```
sio trend --grep "<new entry name>" --windows 14
```

`sio trend` shows pattern-cluster growth over time. After a week, you want to see calls trending up on the new code path and trending flat/down on the old one. If usage is split or still flowing through the legacy path, the new entries aren't being picked up — which usually means either a CLI/loader cache issue or a missing migration step.

#### 7. Do existing rules still hold under the new behavior?

```
/sio-violations --rule "<rule key>"
```

If you have CLAUDE.md rules that touch the surface you just modified (e.g. "always pass `_slug` to MCP calls"), `/sio-violations` shows which sessions in the last N days ignored them. After a config change, this is the canary for "did the agent revert to old habits because the rule was tied to the old surface?"

#### 8. Are the rules you wrote actually reducing errors?

```
/sio-velocity --rule "<rule key>"
```

`/sio-velocity` is the empirical post-condition: error rate on a given pattern **before** the rule was applied vs **after**. If you wrote a rule to lock in the change (e.g. "always use the slug-pinned entry"), velocity tells you whether errors on that pattern are dropping. If they're not, the rule isn't biting and needs rewording — or the change itself wasn't the right one.

---

## Putting it together — a concrete checklist

### Before `--apply`

```bash
# 1. Literal evidence from past sessions
session-search "<change keywords>" --recent 30 --files

# 2 + 3 (parallel — run in one message)
/sio-recall "<workflow description>"
/sio-flows  --query "<surface>"
```

**Gate:** all three agree → proceed. Disagreement → stop, read, decide.

### Immediately after `--apply` (next session)

```bash
# 4. Error cluster check
/sio-scan --recent 1 --grep "<change keywords>"

# 5. Friction check
/sio-scan --recent 1 --type user_correction --grep "<change keywords>"
```

**Gate:** zero clusters of either type → ship the change for the week.

### Week 1 review

```bash
# 6. Adoption check
sio trend --grep "<new identifier>" --windows 7

# 7. Rule compliance
/sio-violations --rule "<rule key>"

# 8. Empirical impact
/sio-velocity --rule "<rule key>"
```

**Gate:** adoption up, violations down, velocity positive → the change is real. If any of the three is flat or negative, you have a story to investigate — and the data to investigate it with.

---

## Why this loop matters

The thing SIO catches that nothing else catches: **silent success → eventual friction**. The config change *works* in the sense that no exception is thrown. But it shifts the agent's default path in a way that surfaces a week later as "why is the agent always picking the wrong slug?" or "why does this hook never fire on the new project?"

`/sio-scan`, `/sio-flows`, and `sio trend` are what turn that lag into a 24-hour feedback loop instead of a one-week regret loop.

If you're about to flip `--apply` on anything that mutates `~/.claude.json`, `~/.claude/hooks/`, `~/.bashrc`, `~/.gitconfig`, a CI workflow, or any other piece of always-loaded infrastructure — this is the loop.

---

## What this use case is *not*

- Not a substitute for `/paired-debate` on architectural choices (use that for Option 1 vs Option 3 decisions, not for "did the apply land cleanly").
- Not a substitute for unit tests on the script itself (write those separately — SIO observes the *agent's behavior*, not the script's correctness).
- Not real-time. The signal lands as sessions accumulate; the first useful read is 24 hours after apply.

---

## Cross-references

- `docs/SIO_PHILOSOPHY.md` — why SIO is "measured assist, not autonomous override"
- `docs/cookbook-2026-05-15.md` — full curate/amplify/optimize pipeline
- `docs/user-guide.md` — every CLI surface
- `~/.claude/rules/tools/sio.md` — when to route the user to which SIO skill
