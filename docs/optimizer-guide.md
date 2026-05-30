# SIO Optimizer Guide

**Version:** current (SIO 0.3.x)
**Cross-references:** `docs/AMPLIFY_GUIDE.md` (amplify mechanics), `CHANGELOG.md` [0.3.0]
(empirical results), `src/sio/core/dspy/optimizer.py`, `src/sio/core/dspy/lm_factory.py`

---

## Overview

SIO uses three DSPy optimizers in a fixed ladder to improve the `SuggestionGenerator`
module — the DSPy program that turns clustered error patterns into actionable CLAUDE.md
rules. The module signature is `PatternToRule`:

```
inputs:  pattern_description (str)
         example_errors      (list[str])
         project_context     (str)
outputs: rule_title   (str)
         rule_body    (str)
         rule_rationale (str)
```

Metric: `suggestion_quality_metric` — weighted sum of three sub-scores:
`0.35 × specificity + 0.35 × actionability + 0.30 × surface_accuracy` (range 0–1).

---

## The three optimizers

### 1. BootstrapFewShot (`bootstrap`)

**What it does:** Selects up to `max_labeled_demos` demonstrations from the training
set and optionally bootstraps additional demos by running the module on training
examples and keeping those that score above the metric threshold. Produces a compiled
module with a richer few-shot prompt.

**Configuration in SIO** (from `optimizer.py`):
```python
dspy.BootstrapFewShot(
    metric=_standard_metric,
    max_bootstrapped_demos=min(2, len(trainset)),
    max_labeled_demos=min(4, len(trainset)),
    max_rounds=1,
)
```

**When to use it:**
- You have fewer than ~200 rows in your trainset (below MIPROv2's floor)
- You need a fast baseline in ~1 minute at near-zero cost (~$0.01)
- You want to validate that the pipeline runs before committing to a full ladder run
- `--rungs bootstrap` on `sio optimize-ladder` for the express lane

**Does NOT accept a validation set** — the compile call omits `valset`.

**Do NOT amplify for Bootstrap.** Bootstrap caps demonstrations at 2–4 regardless
of trainset size (Constitution XIV). Amplifying before Bootstrap wastes budget.

---

### 2. MIPROv2 (`mipro`)

**What it does:** Bayesian search over the prompt-instruction space. Proposes instruction
candidates, evaluates them on the training set, and picks the combination that maximizes
the metric. Uses `auto="light"` by default (budget-controlled).

**Configuration in SIO:**
```python
MIPROv2(metric=_standard_metric, auto="light", num_threads=1)
compiled = optimizer.compile(program, trainset=trainset, valset=valset)
```

**When to use it:**
- You have 200+ amplified rows (hard floor; see gates below)
- You want instruction-level optimization (not just better demos)
- Typical improvement: moderate; MIPROv2 is the "earn your way to GEPA" rung

**Empirical data point** (from CHANGELOG [0.2.0] / [0.3.0]):
- MIPROv2 #17 with `valset=5` scored **0.6970** (below Bootstrap #16's **0.7154**) —
  this is the motivation for the data-size gate; small valsets produce noise, not signal.

---

### 3. GEPA (`gepa`)

**What it does:** Gradient-free Evolutionary Prompt Architecture. Iteratively mutates
prompt instructions using a reflection LM (`get_reflection_lm()`) that critiques
candidates and proposes targeted improvements. The metric must return `(score, feedback)`
so the reflection LM knows which dimension to improve — SIO's `_gepa_metric` wrapper
does this, naming the weakest sub-score explicitly.

**Configuration in SIO:**
```python
dspy.GEPA(
    metric=_gepa_metric,              # returns dspy.Prediction(score=..., feedback=...)
    reflection_lm=reflection_lm_obj, # get_reflection_lm() — strong model
    auto=os.environ.get("SIO_GEPA_BUDGET", "light"),
    reflection_minibatch_size=3,
    num_threads=8,
    track_stats=True,
    seed=0,
)
```

**Two-LM architecture:**
- `task_lm` (cheap, fast, cached) — `get_task_lm()` — runs forward passes
- `reflection_lm` (strong, uncached) — `get_reflection_lm()` — critiques prompt candidates

Both are resolved through `lm_factory.py`. Configure via env or `~/.sio/config.toml`:
```toml
[llm.task]
model = "gemini/gemini-flash-latest"

[llm.reflection]
model = "gemini/gemini-pro-latest"
```

Or via environment:
```bash
SIO_TASK_LM=gemini/gemini-flash-latest
SIO_REFLECTION_LM=gemini/gemini-pro-latest
```

**Cost by budget tier:**

| `SIO_GEPA_BUDGET` | Est. cost | Est. time |
|---|---|---|
| `light` (default) | $5–8 | 30–60 min |
| `medium` | $15–25 | 90–150 min |
| `heavy` | $40–80 | 3–5 hours |

SIO prints a banner before GEPA starts so there are no cost surprises.

**Empirical results** (from CHANGELOG [0.2.0]):
- GEPA #14 on amplified 372-row trainset: **0.7224**
- GEPA #15 on same trainset: **0.8653** vs baseline 0.6768 — **+27.9%**
- GEPA on un-amplified 93-row curate: timed out at 60 min, **$1.11 wasted** with zero
  useful output (reflection LM never reached evaluation — this is the cautionary tale
  that motivated the amplify-first gate)

**When to use it:**
- After MIPROv2 has already run on the same trainset (ladder gate enforces this)
- You have 300+ amplified rows (data floor)
- You want maximum quality and are willing to pay the reflection LM cost

---

## Ladder discipline (Constitution XIV)

The three optimizers form a **ladder** — you cannot skip rungs without explicitly
bypassing the gates. The ladder order is:

```
Bootstrap → AMPLIFY → MIPROv2 → GEPA
```

Three gates protect the ladder:

### Gate 1: Ladder gate
GEPA refuses to run on a trainset unless a prior MIPROv2 run exists in
`optimized_modules` for the same module + dataset.

**Why:** GEPA's reflection-LM critiques assume a baseline prompt that was already
refined by Bayesian search. Skipping MIPROv2 means GEPA is critiquing the raw
unoptimized prompt — wasted reflection calls.

Bypass (logged as `LADDER_SKIP` in run log):
```bash
sio optimize --optimizer gepa --skip-ladder --trainset-file <file>
```

### Gate 2: Data-size gate
MIPROv2 refuses when `valset_size < max(25, trainset_size × 0.2)`.

**Why:** MIPROv2 #17 with valset=5 underperformed Bootstrap (#16). A tiny valset
gives Bayesian search a noisy signal — it cannot distinguish good instruction
candidates from lucky ones.

Bypass (logged as `DATA_SIZE_SKIP`):
```bash
sio optimize --optimizer mipro --skip-data-gate --trainset-file <file>
```

### Gate 3: Amplify-first gate
MIPROv2 and GEPA refuse when:
- The trainset source is `'curate'` (raw, unamplified output), OR
- Row count is below the per-optimizer floor (MIPROv2 ≥ 200, GEPA ≥ 300)

**Why:** The $1.11 wasted-GEPA-without-amplify incident (2026-05-18) — GEPA's
reflection LM needs enough diverse examples to propose meaningful mutations.
93 rows is not enough for convergence.

Bypass (logged as `AMPLIFY_SKIP`):
```bash
sio optimize --optimizer gepa --skip-amplify-gate --trainset-file <file>
```

---

## Data-size decision table

| Trainset rows | What you can run | Notes |
|---|---|---|
| < 10 | Nothing (quality gates block all optimizers) | Collect more sessions first |
| 10–49 | Bootstrap only | `--rungs bootstrap` express lane |
| 50–199 | Bootstrap only | Amplify before attempting MIPROv2 |
| 200–299 | Bootstrap + MIPROv2 | Clears MIPROv2 floor; GEPA still blocked |
| 300+ | Full ladder | Recommended target: 400+ for GEPA headroom |

To calculate `--n-per-row` for amplification:
```
n_per_row = ceil((target_rows - input_rows) / input_rows)
```
E.g. 51 input rows, target 300 → `ceil((300-51)/51)` = 5. Add 1–2 extra for judge drops.

---

## Recommended entry point: `sio optimize-ladder`

Use `sio optimize-ladder` rather than calling each optimizer individually. It:

1. Resolves the input trainset and registers it in the `trainsets` table
2. Checks which rungs already have scored rows in `optimized_modules` (idempotent on
   re-run — safe for cron crash recovery)
3. Estimates total cost and asks for confirmation (or `--yes` to skip)
4. Executes each rung via subprocess so the discipline gates fire normally
5. Writes `~/.sio/state/ladder_status.json` after each rung for monitoring
6. Emits a `LADDER_VERDICT` at completion

```bash
# Standard full run
sio optimize-ladder \
  --trainset-file ~/.sio/datasets/my_curated.jsonl \
  --yes

# Express lane — Bootstrap only, no GEPA cost
sio optimize-ladder \
  --trainset-file ~/.sio/datasets/my_curated.jsonl \
  --rungs bootstrap \
  --yes

# Dry run — plan + cost estimate, no execution
sio optimize-ladder \
  --trainset-file ~/.sio/datasets/my_curated.jsonl \
  --dry-run

# Resume after crash (re-running skips completed rungs)
sio optimize-ladder \
  --trainset-file ~/.sio/datasets/my_curated.jsonl \
  --yes
```

### Ladder verdict codes

| Verdict | Meaning |
|---|---|
| `gepa_justified` | GEPA − MIPROv2 ≥ 0.03 → ship GEPA |
| `mipro_wins_on_economics` | Within 0.03 → ship MIPROv2 (30× cheaper) |
| `both_fail` | Neither passes quality bars → fix trainset upstream |
| `gepa_no_score` | GEPA aborted / stuck → ship MIPROv2 |
| `mipro_dead_weight` (overlay) | MIPROv2 < Bootstrap → investigate trainset |

---

## Live monitoring

During a long GEPA run, use `sio gepa-status` to see current progress:

```bash
sio gepa-status          # one-shot
sio gepa-status --watch  # refresh every 5 seconds
```

Output shows iteration count, current best score, trend arrow (↑↓→), and any
abort-tier warnings:

| Abort tier | Condition | Signal |
|---|---|---|
| T1 | Iter idle 8 min | `GEPA_ITER_STALL_WARN` |
| T2 | Iter idle 15 min | `GEPA_ITER_STALLED_CRITICAL` |
| T3 | ≥ 3 AdapterParseErrors / 5 min | `GEPA_ADAPTER_PARSE_STREAK` |
| T4 | ≥ 3 max_tokens truncations / 5 min | `GEPA_TRUNCATION_STREAK` |
| T5 | Reflection stuck 40 min | `REFLECTION_STUCK_CRITICAL` |

All warnings appear in `run.warns` and as `[CRITICAL]` stderr lines.
SIO does not auto-SIGTERM (Python signals across threads are fragile) — the
operator decides.

Check recent run artifacts:
```bash
sio runs                 # list recent runs with timings
ls ~/.sio/runs/          # raw JSONL run logs + _dspy.jsonl sidecars
```

---

## Cost awareness

The `[budget]` block in `~/.sio/config.toml` sets a hard 24h cap. The `[llm.banned]`
block refuses specific model IDs at construction time (enforced by `lm_factory.py`).
Per the project's cost-control rule, `gpt-4o` is permanently banned — never appears
as a default.

`sio optimize-ladder` always prints a cost estimate and requires confirmation (or
`--yes`) before executing. For high-cost tiers (`medium`, `heavy`) GEPA emits an
additional `⚠ HIGH-COST BUDGET TIER` warning.

```bash
SIO_GEPA_BUDGET=heavy sio optimize ...   # opt in to expensive tier explicitly
```

---

## Anti-patterns

- **Amplifying for Bootstrap** — wasted budget. Bootstrap caps demos at 2–4 regardless
  of trainset size.
- **Running GEPA on raw `sio curate` output** — the 93-row/60-min/$1.11 incident.
  Always amplify to ≥ 300 rows first.
- **Setting `--n-per-row` above 15** — diminishing returns; Flash produces near-duplicates
  the judge discards. Signal is in spread, not count.
- **Setting `--min-judge-score 0.0`** — category drift poisons the trainset;
  MIPROv2 will optimize against off-category examples.
- **Re-amplifying an already-amplified dataset** — compounding hallucinations.
  If you need more rows, re-amplify the original curate output at higher `--n-per-row`.
- **Calling `dspy.LM(...)` directly** — use `get_task_lm()` / `get_reflection_lm()`.
  The grep test in `test_lm_factory.py` will catch bare instantiations.

---

## Full pipeline reference

The standard end-to-end flow before calling `optimize-ladder`:

```bash
# 1. Mine recent sessions
sio mine --since "7 days ago"

# 2. Cluster errors into patterns
sio patterns

# 3. Review and approve examples as ground truth
sio suggest-review
sio approve <id>

# 4. Curate a JSONL trainset from approved ground truth
sio datasets collect --since "30 days ago"

# 5. Amplify (handled automatically by optimize-ladder, but can run standalone)
sio amplify -i ~/.sio/datasets/my_curated.jsonl --n-per-row 6

# 6. Full ladder
sio optimize-ladder --trainset-file ~/.sio/datasets/my_curated.jsonl --yes

# 7. Apply the winning module's suggestions
sio apply <suggestion_id>
```
