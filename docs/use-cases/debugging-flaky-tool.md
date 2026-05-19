# Use Case: Debugging a Flaky Tool / Repeated Identical Failures

**Scenario class:** You (or the agent) have called the same tool 3+ times with substantially the same input and gotten the same opaque failure each time. The retry-guard circuit breaker has either fired or is about to. You don't want to burn another retry — you want to know: *has anyone solved this before, and if so, can we codify the fix so the next session doesn't re-discover it?*

This is the classic SIO recovery loop: **stop, recall, codify.** It is the antidote to the "agent loops on the same broken call until the user intervenes" failure mode.

---

## The story

The agent is mid-task. It calls `<tool_name>` with some input, gets a non-obvious failure (a 4xx, a parser error, a missing-field complaint that doesn't match the docs, a silent empty result where data was expected). It retries with the same input. Same failure. It retries a third time with a trivial whitespace tweak — the retry-guard PreToolUse hook blocks the call with exit 2 and a one-line message: "3rd identical call — stop and investigate."

At this point the temptation is to either (a) keep retrying with cosmetic variations, or (b) escalate to the user with "this tool is broken." Both are wrong. The cluster of failures on this tool almost certainly exists in past sessions, and the fix has almost certainly been figured out before — but it lives in JSONL transcripts nobody indexed.

That's the gap SIO fills.

---

## The six SIO surfaces you'd actually use

These map onto three phases: **recognize you're stuck**, **pull prior solutions**, **codify the fix so future-you doesn't hit it again.**

### Phase 1 — Before retrying (recognize you're stuck)

#### 1. Notice the retry-guard fired (or is about to)

The retry-guard hook is the canonical "you are looping" signal. When it blocks on the 3rd identical call, that is your cue to stop touching the tool and start mining. The blocked call also implies an active `repeated_attempt` event in the session log — SIO will see it on the next scan.

> **Rule of thumb:** if you've called the same tool twice with the same input and gotten the same failure, treat the third attempt as forbidden until you've spent at least one minute on recall. The cost of one `/sio-recall` call is ~5 seconds. The cost of a 6th retry is a user correction and a lost half-hour.

The auto-escalation rule (`~/.claude/rules/tools/gemini.md`) ties into this: after 3 failed identical attempts, `gemini_debug` becomes the mandated next step. SIO is what you run *first* to check whether you even need Gemini, or whether the answer is already in your own history.

### Phase 2 — Pull prior solutions (recall + search)

#### 2. Has this exact error been fixed before in a distilled session?

```
/sio-recall "<plain-English description of what's failing>"
# e.g. "<tool_name> returns empty result when input contains <pattern>"
```

`/sio-recall` runs over distilled sessions — past sessions that succeeded at solving something specific and got compressed into a reusable playbook. If the failure has been solved before, you get the exact fix: the parameter that was wrong, the alternate tool that worked, the missing setup call. This is the highest-leverage SIO surface for stuck-on-a-tool moments because distilled sessions are pre-curated for "this is the way."

If `/sio-recall` returns nothing, that's not a dead end — it just means the fix hasn't been distilled yet. Drop to literal search.

#### 3. Literal text match across all recent sessions

```bash
session-search "<error keywords>" --recent 7 --files
```

`session-search` reads JSONL transcripts directly — ~200ms, zero cost. Grep the error message (or a distinctive fragment of it) against the last 7 days of sessions. Use `--files` first to see which sessions hit the same string; then `Read` the one or two most recent matches to see what actually fixed it.

If 7 days yields nothing, widen to `--recent 30`. If 30 yields nothing, this is genuinely novel and you have permission to escalate to `gemini_debug` — but now you have a real "I checked first" story instead of a "I gave up after three tries" story.

#### 4. What does the error pattern look like in aggregate?

```
/sio-suggest --grep "<error keywords>" --type repeated_attempt --preview
```

`/sio-suggest` mines clusters of `repeated_attempt` events matching the grep terms. The `--preview` flag exports the cluster to `~/.sio/previews/` without committing to generating a rule yet, so you can see how many sessions are affected and what the cluster's common shape is. If the cluster is large (say >5 sessions in the last 30 days), this is a known systemic problem and worth codifying. If it's only your current session, it's a one-off — fix it locally and move on.

### Phase 3 — Codify the fix (suggest → review → apply → verify)

Once you have a fix in hand (from `/sio-recall`, from a `session-search` hit, or from `gemini_debug`), the question is whether to write it down. The test: *would future-me, six weeks from now, on a fresh session with no context, re-discover this fix?* If no, codify. If yes, skip — not every fix is rule-worthy.

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

`/sio-review` shows you the generated rule with its source-cluster evidence. Edit the wording if it's too generic or too specific. `/sio-apply` writes it to the right place — global CLAUDE.md for behavioral rules, `~/.claude/rules/tools/<tool>.md` for tool-specific gotchas, project CLAUDE.md if the failure is scoped to one repo. SIO's rule-routing logic picks the tier; don't override it without reason.

#### 7. Verify the rule is biting

```
/sio-velocity --rule "<rule key>"
```

A week later, `/sio-velocity` shows the error rate on the matching pattern *before* and *after* the rule was applied. If errors are dropping, the rule is doing its job. If they're flat, the rule is either being ignored (run `/sio-violations --rule "<key>"` to confirm) or it was the wrong rule for the actual root cause.

---

## Putting it together — a concrete checklist

### When retry-guard fires (or you notice the loop)

```bash
# 1. Recall first — cheapest, highest leverage
/sio-recall "<plain-English description>"

# 2 + 3 (parallel — run in one message)
session-search "<error keywords>" --recent 7 --files
/sio-suggest --grep "<error keywords>" --type repeated_attempt --preview
```

**Gate:** if any of the three returns a credible fix → apply it, do not retry the original approach. If all three are empty → escalate to `gemini_debug` with full context of what was tried.

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

The thing SIO catches that nothing else catches: **the same tool fails the same way in session after session, and each session re-discovers the fix from scratch.** The fix exists. It's in the transcripts. It's just not indexed in a way the next session can find without help.

`/sio-recall` + `session-search` collapse "I have to figure this out again" into "someone already figured this out, here's the answer." `/sio-suggest` + `/sio-apply` close the loop so the *next* session doesn't even have to recall — the rule fires before the broken call happens.

The retry-guard hook is what makes this loop physical. Without it, the agent loops indefinitely. With it, the agent is forced to pause, and SIO is what fills the pause productively.

---

## What this use case is *not*

- Not a substitute for actually reading the error message. Run `session-search` and `/sio-recall`, but also *look at what the tool is telling you* — half the time the error is self-describing and you skipped past it.
- Not a substitute for unit tests on tooling you control. If you own the tool that's flaky, fix the tool — don't paper over it with a CLAUDE.md rule.
- Not a substitute for asking the user when you're genuinely stuck. SIO is for "has this been seen before"; the user is for "I have no idea what this domain even is." Different escalations.
- Not a way to silence the retry-guard. If you find yourself wanting to bypass the hook on the 3rd call, you have already failed this loop — go back to step 1.

---

## Cross-references

- `docs/use-cases/validating-a-config-change.md` — the proactive cousin of this loop (mine *before* the change, not after the failure)
- `docs/SIO_PHILOSOPHY.md` — why SIO is "measured assist, not autonomous override"
- `docs/user-guide.md` — every CLI surface in detail
- `~/.claude/rules/tools/retry.md` — the retry-guard circuit-breaker spec
- `~/.claude/rules/tools/gemini.md` — auto-escalation to `gemini_debug` after 3 failed attempts
- `~/.claude/rules/tools/sio.md` — when to route to which SIO skill
