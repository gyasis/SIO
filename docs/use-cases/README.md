# SIO Use Cases

Eight scenario-driven walkthroughs showing how to wire SIO's CLI surfaces into the work you already do. Each doc is generic (no project names, no PII) and follows the same shape: **story → phased SIO surfaces → checklist with gates → why it matters → what it's NOT → cross-references**.

| Use Case | When to reach for it |
|---|---|
| [Validating a config change](validating-a-config-change.md) | You're about to `--apply` a script that mutates always-loaded infra (`~/.claude.json`, hooks, rc files, CI workflows). |
| [Debugging a flaky tool](debugging-flaky-tool.md) | The agent has hit the same failure 3+ times. Time to stop retrying and check if this was solved before. |
| [Pre-merge PR safety](pre-merge-pr-safety.md) | A PR touches shared infrastructure. SIO is the *prior-art* layer; pair it with `/adversarial-audit` for the active-bug layer. |
| [Onboarding to a codebase](onboarding-to-codebase.md) | Returning to a project after weeks. SIO answers "what's been tried here and what stuck." |
| [Cost / performance regression hunt](cost-performance-regression-hunt.md) | Token bill or wall-clock spiked. Find which workflow drifted without manual log archaeology. |
| [Rule lifecycle](rule-lifecycle.md) | When to codify a CLAUDE.md rule, when to audit it, when to retire it. Rules aren't free; SIO is the audit layer. |
| [Cross-session continuity](cross-session-continuity.md) | Resume work after `/compact`, `/clear`, or a fresh shell — without re-explaining everything. |
| [Workflow discovery & promotion](workflow-discovery-and-promotion.md) | Turn a recurring tool sequence into a reusable skill. Data picks the skill, not your guess. |

## Reading order

If you're new to SIO, read in this order:

1. **Validating a config change** — establishes the three-phase loop (before / immediately after / steady-state) that every other doc reuses.
2. **Debugging a flaky tool** — the most common day-to-day surface.
3. **Rule lifecycle** — the meta-loop that governs all the rules SIO suggests.

The rest are domain-specific — read when the scenario hits.

## Common thread

Every use case follows the same operational pattern:

```
1. Mine prior sessions for ground truth (session-search, /sio-recall, /sio-flows)
2. Take the action (apply, retry, codify, promote)
3. Mine the next sessions for outcome (/sio-scan, /sio-velocity, sio trend)
```

SIO isn't real-time. Its value lands as sessions accumulate — usually within 24 hours of the action.

## Cross-references

- [`../SIO_PHILOSOPHY.md`](../SIO_PHILOSOPHY.md) — why SIO is "measured assist, not autonomous override"
- [`../user-guide.md`](../user-guide.md) — full CLI surface reference
- [`../cookbook.md`](../cookbook.md) — recipe-style task playbooks
- `~/.claude/rules/tools/sio.md` — when to route the user to which SIO skill
