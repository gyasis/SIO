# Use Case: Hunting a Cost or Performance Regression

**Scenario class:** Your monthly API bill jumped without an obvious cause, OR a workflow that used to take 10 minutes now takes 40, OR token usage per session has crept up week-over-week. You don't have an APM tool that ties LLM spend to agent intent — but you do have SIO, which has timestamps, tool-call counts, sequences, and content hashes for every prior session. That's enough to do forensic archaeology without grepping logs by hand.

This use case is the inverse of the config-change loop: instead of validating a known change, you're hunting a change you didn't notice you made.

---

## The story

You open your billing dashboard for the month and the number is roughly 2.5× last month. Nothing in your habits felt different. The agent didn't get noticeably more useful. You haven't onboarded any new automated pipelines.

Or — variant — a workflow you run every morning (sync + status + summary) used to wrap in ~10 minutes. This week it's taking closer to 40. Same prompts, same project, same machine. Something in the path got expensive and you don't know which step.

The instinct is to start by reading logs. Don't. SIO already aggregated this. Three questions answer it:

1. **What shifted in the last 30 days vs the 30 before that?**
2. **Which specific workflow or tool is responsible for the shift?**
3. **After you fix it, did the shift actually reverse?**

---

## The SIO surfaces you'd actually use

### Phase 1 — Detect what shifted (the diff)

#### 1. Pattern growth over a long window

```bash
sio trend --windows 30 --daily
sio trend --grep "<expensive tool or model name>" --windows 30 --daily
```

`sio trend` buckets pattern clusters per day (or per week with `--weekly`). The unfiltered view shows which clusters grew. The grep'd view shows whether a specific tool or model class (e.g. "expensive model X" calls, "deep research" calls, retry-heavy patterns) is the one trending up.

> **Rule of thumb:** if `sio trend --windows 30 --daily` shows a cluster doubling in the last 7 days, that cluster is your suspect. Cost regressions almost always show up as a single dominant cluster growing, not a uniform shift.

#### 2. Raw call counts by week

```bash
session-search "<expensive model name>" --recent 30 --count
session-search "<expensive model name>" --recent 60 --count
```

The simplest possible diff. Compare the count for the last 30 days against the count for the 30 before that (run the second query, subtract). If you went from 12 calls/month to 180 calls/month, you have your answer — something is now routing through expensive model X by default.

This also catches the case where the trend is invisible at the cluster level because the calls are spread across many session types — but the *raw count* still doubled.

#### 3. Has any cost-related rule started getting violated?

```
/sio-violations --rule "never-use-gpt-4o"
/sio-violations --rule "<other cost-control rule key>"
```

If you have rules in `~/.claude/rules/domains/cost-control.md` (e.g. "never use expensive model X as default fallback"), `/sio-violations` shows how often those rules were ignored over the last N days. A rule that was at 0 violations/week and is now at 4/week is a smoking gun.

### Phase 2 — Localize the regression (which workflow, which tool)

#### 4. What does the dominant tool sequence for the suspect workflow look like now vs before?

```
/sio-flows --query "<workflow name or surface>"
# e.g. "morning sync workflow" or "code review subagent"
```

`/sio-flows` returns the recurring positive tool sequence for that workflow. Compare what you see today against what you remember (or against what the same query returned a month ago — check `~/.sio/previews/` or your shell history for the prior run).

The common regression: a workflow that used to be `read → edit → bash` (3 calls) is now `read → search → read → search → edit → bash → bash → read` (8 calls). Same outcome, 2-3× the tokens. Usually caused by an over-eager retry rule, a newly-installed hook that fires extra searches, or a subagent being spawned where it didn't used to be.

#### 5. Did error retries spike (more retries = more tokens = more cost)?

```
/sio-scan --grep "<error type or tool name>" --recent 30
```

A cost spike with no behavioral change is often actually a retry spike. If a tool started failing silently and the agent now retries it 3× per session before fallback, every session is paying that tax. `/sio-scan` will surface the error cluster; the cluster count tells you the per-session cost.

#### 6. Raw telemetry — exact tool-call counts per session

```bash
sqlite3 ~/.sio/claude-code/behavior_invocations.db <<EOF
SELECT date(timestamp) as day,
       tool_name,
       count(*) as calls
FROM behavior_invocations
WHERE timestamp > date('now', '-30 days')
GROUP BY day, tool_name
ORDER BY day DESC, calls DESC;
EOF
```

The per-platform DB is the floor of truth. Every tool call the hook layer saw is in there with a timestamp. If `sio trend` and `/sio-flows` give you a suspect, this query confirms it at the raw-call level — and lets you see the exact day the regression started, which is usually within 24 hours of whatever change caused it.

Knowing the start date is the unlock. Once you have it, `git log --since` on `~/.claude/` and `~/.claude.json` will surface the offending commit.

### Phase 3 — Verify the fix

#### 7. After rolling back the change (or adding a new rule), does the trend reverse?

```bash
sio trend --grep "<the cluster that grew>" --windows 14 --daily
/sio-velocity --rule "<the new cost-control rule>"
```

You wrote a rule (or rolled back a config). The fix is real if and only if:

- `sio trend` shows the suspect cluster going flat or shrinking over the next 7 days.
- `/sio-velocity` on any new rule you added shows error/violation rate trending toward zero.

If trend is flat after 7 days, the regression is fixed. If it's still climbing, your fix didn't bind — the rule isn't being enforced, or the rollback missed the actual cause.

---

## Putting it together — a concrete checklist

### Detect (today)

```bash
# 1. What clusters grew?
sio trend --windows 30 --daily

# 2. Which expensive surfaces increased in raw count?
session-search "<expensive model/tool>" --recent 30 --count
session-search "<expensive model/tool>" --recent 60 --count

# 3. Are cost rules being ignored?
/sio-violations --rule "<cost rule key>"
```

**Gate:** identify ONE dominant suspect cluster or surface before moving to Phase 2. Don't fan out yet.

### Localize (same session)

```bash
# 4. Did the workflow shape change?
/sio-flows --query "<suspect workflow>"

# 5. Did retries spike?
/sio-scan --grep "<suspect tool>" --recent 30

# 6. Raw counts per day to find the start date
sqlite3 ~/.sio/claude-code/behavior_invocations.db "..."
```

**Gate:** have a start date and a named cause (config change, new hook, model default flip, rule violation). Now you can fix it.

### Verify (next 7-14 days)

```bash
# 7. Trend reversal
sio trend --grep "<suspect cluster>" --windows 14 --daily
/sio-velocity --rule "<new rule if you added one>"
```

**Gate:** suspect cluster trending flat or down → fix landed. Still climbing → the fix missed.

---

## Why this loop matters

No APM tool tracks LLM token cost by **agent intent**. They track HTTP calls, tokens-in/tokens-out, latency. They don't tell you "the morning-sync workflow doubled in cost because a hook started spawning a code-review subagent on every commit."

SIO does, because SIO tracks **tool sequences tied to session intent**, not just isolated API calls. The unit of analysis is the workflow, not the request. That's what makes a 30-day backward look forensically useful: you can ask "what did sessions look like in week 1 vs week 5" and get a structural answer, not a token-count answer.

The other thing this catches: cost regressions where no individual session is anomalous. Every session is +10% more expensive than it used to be. You'd never spot that in a per-request log. You spot it instantly in `sio trend`.

---

## What this use case is *not*

- **Not a billing dashboard.** SIO doesn't know your prices, your provider's tier, or your actual dollar spend. It tells you *what shifted in agent behavior* — you map that to dollars yourself.
- **Not real-time.** The regression needs at least a few days of post-shift sessions to be visible in the trend. Don't expect to catch a cost spike the same hour it starts.
- **Doesn't catch costs from non-Claude-Code surfaces.** If you're running a separate background daemon, a CI job that calls an LLM API, or an external automation — SIO can't see those calls. Only sessions that route through the Claude Code hook layer get telemetry.
- **Not a substitute for setting hard budget caps.** Detection is reactive. Pair this loop with provider-level spend limits.

---

## Cross-references

- `~/.claude/rules/domains/cost-control.md` — the canonical "never use expensive model X" rules SIO checks against
- `docs/SIO_PHILOSOPHY.md` — why SIO is structural, not request-level
- `docs/user-guide.md` — every CLI surface used here
- `~/.claude/rules/tools/sio.md` — when to route to which SIO skill
- `docs/use-cases/validating-a-config-change.md` — the forward-looking sibling of this loop (validate a change before it lands vs hunt a change that already did)
