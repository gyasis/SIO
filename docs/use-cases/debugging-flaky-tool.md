# Use Case: Debugging a Flaky Tool / Repeated Identical Failures

**Scenario class:** You (or the agent) have called the same tool 3+ times with substantially the same input. Each call gave the same opaque failure. The retry-guard circuit breaker has either fired or is about to. You don't want to burn another retry. You want to know two things: *has anyone solved this before?* If so, can we codify the fix so the next session doesn't re-discover it?

This is the classic SIO recovery loop: **stop, recall, codify.** It prevents one failure mode. In that mode, the agent loops on the same broken call until the user intervenes.

---

## The story

The agent is mid-task. It calls `<tool_name>` with some input and gets a non-obvious failure. Examples are a 4xx, a parser error, a missing-field complaint that doesn't match the docs, or a silent empty result when the agent expected data. It retries with the same input. Same failure. It retries a third time with a trivial whitespace tweak. Then the retry-guard PreToolUse hook blocks the call with exit 2 and a one-line message: "3rd identical call — stop and investigate."

At this point the temptation is to do one of two things. Option (a) is to keep retrying with cosmetic variations. Option (b) is to escalate to the user with "this tool is broken." Both are wrong. The cluster of failures on this tool almost certainly exists in past sessions. Someone almost certainly found the fix before, but it lives in JSONL transcripts nobody indexed.

That's the gap SIO fills.

---

## The six SIO surfaces you'd actually use

These map onto three phases: **recognize you're stuck**, **pull prior solutions**, **codify the fix so future-you doesn't hit it again.**

### Phase 1 — Before retrying (recognize you're stuck)

#### 1. Notice the retry-guard fired (or is about to)

The retry-guard hook is the canonical "you are looping" signal. When it blocks on the 3rd identical call, that is your cue to stop touching the tool and start mining. The blocked call also implies an active `repeated_attempt` event in the session log — SIO will see it on the next scan.

> **Rule of thumb:** if you've called the same tool twice with the same input and got the same failure, treat the third call as forbidden until you spend at least one minute on recall. One `/sio-recall` call costs ~5 seconds. A 6th retry costs a user correction and a lost half-hour.

The auto-escalation rule (`~/.claude/rules/tools/gemini.md`) ties into this: after 3 identical failed calls, `gemini_debug` becomes the mandated next step. SIO is what you run *first* to check whether you even need Gemini, or whether the answer is already in your own history.

### Phase 2 — Pull prior solutions (recall + search)

#### 2. Did a distilled session already fix this exact error?

```
/sio-recall "<plain-English description of what's failing>"
# e.g. "<tool_name> returns empty result when input contains <pattern>"
```

`/sio-recall` runs over distilled sessions — past sessions that succeeded at solving something specific and became a reusable playbook. If a past session solved the failure, you get the exact fix. Examples are the parameter that was wrong, the alternate tool that worked, or the missing setup call. This is the highest-leverage SIO surface for stuck-on-a-tool moments because distilled sessions are pre-curated for "this is the way."

If `/sio-recall` returns nothing, that's not a dead end — it just means no session has distilled the fix yet. Drop to literal search.

#### 3. Literal text match across all recent sessions

```bash
sio search "<error keywords>" --recent 7 --files
```

`sio search` reads JSONL transcripts directly — ~200ms, zero cost. Grep the error message (or a distinctive fragment of it) against the last 7 days of sessions. Use `--files` first. It shows which sessions hit the same string. Then `Read` the one or two most recent matches to see what fixed it.

If 7 days yields nothing, widen to `--recent 30`. If 30 yields nothing, this is genuinely novel and you have permission to escalate to `gemini_debug`. Now you have a real "I checked first" story, not an "I gave up after three tries" story.

#### 4. What does the error pattern look like in aggregate?

```
/sio-suggest --grep "<error keywords>" --type repeated_attempt --preview
```

`/sio-suggest` mines clusters of `repeated_attempt` events matching the grep terms. The `--preview` flag exports the cluster to `~/.sio/previews/` without committing to generate a rule yet. You can then see how many sessions the cluster touches and what its common shape is. If the cluster is large (say >5 sessions in the last 30 days), this is a known systemic problem. It is worth codifying. If it's only your current session, it's a one-off — fix it locally and move on.

### Phase 3 — Codify the fix (suggest → review → apply → verify)

Once you have a fix in hand (from `/sio-recall`, from a `sio search` hit, or from `gemini_debug`), the question is whether to write it down. The test: *would future-me, six weeks from now, on a fresh session with no context, re-discover this fix?* If no, codify. If yes, skip — not every fix is rule-worthy.

#### 5. Generate a candidate rule from the cluster

```
/sio-suggest --grep "<error keywords>" --type repeated_attempt --refine "<more specific terms>" --strategy filter
```

This is the same command as above but without `--preview`. It generates a candidate CLAUDE.md rule (or tool-specific rule under `~/.claude/rules/tools/<tool>.md`) that, if applied, would prevent the cluster. The `--refine` flag is a Hop-2 narrowing — use it when the wide grep would pull in unrelated noise.

#### 6. Review and apply

```
/sio-review            # see the candidate rule, edit if needed
/sio-apply <id>        # write to the correct rule tier
```

`/sio-review` shows you the generated rule with its source-cluster evidence. Edit the wording if it's too generic or too specific. `/sio-apply` writes it to the right place. Global CLAUDE.md gets behavioral rules. `~/.claude/rules/tools/<tool>.md` gets tool-specific gotchas. Project CLAUDE.md gets failures that affect only one repo. SIO's rule-routing logic picks the tier; don't override it without reason.

#### 7. Verify the rule is biting

```
/sio-velocity --rule "<rule key>"
```

A week later, `/sio-velocity` shows the error rate on the matching pattern *before* and *after* you applied the rule. If errors are dropping, the rule is doing its job. If they're flat, the agent either ignores the rule (run `/sio-violations --rule "<key>"` to verify) or the rule is wrong for the actual root cause.

---

## Putting it together — a concrete checklist

### When retry-guard fires (or you notice the loop)

```bash
# 1. Recall first — cheapest, highest leverage
/sio-recall "<plain-English description>"

# 2 + 3 (parallel — run in one message)
sio search "<error keywords>" --recent 7 --files
/sio-suggest --grep "<error keywords>" --type repeated_attempt --preview
```

**Gate:** if any of the three returns a credible fix → apply it, do not retry the original approach. If all three are empty → escalate to `gemini_debug` with full context of what you tried.

### After the fix lands

```bash
# 4. Decide: is this rule-worthy?
# Test: would future-me re-discover this in 6 weeks? If no, codify.

# 5. Generate the candidate
/sio-suggest --grep "<error keywords>" --type repeated_attempt

# 6. Review and apply
/sio-review
/sio-apply <id>
```

**Gate:** rule written, tier chosen, source cluster cited in the rule body. If the rule is generic enough to apply across projects, it goes global; otherwise it stays scoped.

### Week 1 follow-up

```bash
# 7. Verify
/sio-velocity --rule "<rule key>"
/sio-violations --rule "<rule key>"
```

**Gate:** velocity trending positive (errors dropping), violations low. If violations are high, the rule is being ignored — either reword it for clarity or move it to a tier with hook enforcement.

---

## Why this loop matters

SIO catches one thing that nothing else catches: **the same tool fails the same way in session after session, and each session re-discovers the fix from scratch.** The fix exists. It's in the transcripts. But no index lets the next session find it without help.

`/sio-recall` + `sio search` collapse "I have to figure this out again" into "someone already figured this out, here's the answer." `/sio-suggest` + `/sio-apply` close the loop. The *next* session doesn't even have to recall, because the rule fires before the broken call happens.

The retry-guard hook is what makes this loop physical. Without it, the agent loops indefinitely. With it, the hook forces the agent to pause, and SIO fills the pause productively.

---

## What this use case is *not*

- Not a substitute for actually reading the error message. Run `sio search` and `/sio-recall`, but also *look at what the tool is telling you*. Half the time the error describes itself and you skipped past it.
- Not a substitute for unit tests on tooling you control. If you own the tool that's flaky, fix the tool — don't paper over it with a CLAUDE.md rule.
- Not a substitute for asking the user when you're genuinely stuck. SIO is for "has anyone seen this before"; the user is for "I have no idea what this domain even is." Different escalations.
- Not a way to silence the retry-guard. If you find yourself wanting to bypass the hook on the 3rd call, you have already failed this loop — go back to step 1.

---

## Cross-references

- `docs/use-cases/validating-a-config-change.md` — the proactive cousin of this loop (mine *before* the change, not after the failure)
- `docs/SIO_PHILOSOPHY.md` — why SIO is "measured assist, not autonomous override"
- `docs/user-guide.md` — every CLI surface in detail
- `~/.claude/rules/tools/retry.md` — the retry-guard circuit-breaker spec
- `~/.claude/rules/tools/gemini.md` — auto-escalation to `gemini_debug` after 3 failed calls
- `~/.claude/rules/tools/sio.md` — when to route to which SIO skill
