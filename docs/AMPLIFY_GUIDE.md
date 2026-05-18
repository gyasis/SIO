# `sio amplify` — Operator Guide

**Status:** Living document. Last updated 2026-05-18.
**Audience:** Anyone (user or AI agent) deciding HOW to amplify a curated dataset before MIPROv2/GEPA.

## TL;DR

`sio amplify -i <input.jsonl>` takes a curated JSONL (typically from `sio curate`) and synthesizes variants per row via Gemini Flash. Each variant preserves the original `pattern_id` but varies surface features (paths, tool names, line numbers, phrasing). A second Flash call judges each variant and drops those that drift to a different category.

**Why it exists:** MIPROv2 and GEPA need ≥200 / ≥300 rows respectively to perform their Bayesian / reflective search reliably (Constitution XIV). Most curated datasets are ~50-100 rows. Amplification bridges that gap.

**The decision you actually have to make:** `--n-per-row`. Everything else has a sensible default.

## Mechanics

```
INPUT: curated JSONL with rows in PatternToRule shape:
  { "inputs": ["pattern_description", "example_errors", "project_context"],
    "data": { "pattern_description": ..., "example_errors": [...], ... } }

FOR EACH input row:
  Phase 1 — GENERATE:
    Flash prompt: "Here is an error pattern + example. Generate N variants
    that vary surface features (paths, tool names, file lines, phrasing)
    while keeping the SAME pattern_id. Don't drift to a different category."
    → N candidate variants
  Phase 2 — JUDGE (separate Flash call, 1 per row not per variant):
    Flash scores how well each variant stayed in category.
    Variants below --min-judge-score are dropped.

OUTPUT: JSONL with originals + kept variants, auto-registered in `trainsets`
table (source='amplify', parent_dataset_id pointing at the input dataset's id).
```

**Cost shape (per row, --task-mode cheap = Flash):**
- 1 generation call (~2K input + ~2K×N output tokens)
- 1 judge call (~2K input + ~500 output tokens)
- Total ~$0.0006 + $0.0003N per row at Flash pricing

A 50-row dataset at `--n-per-row 5` runs ~50 × ($0.0006 + $0.003) ≈ **$0.18**.

## Levers

| Lever | Default | Effect |
|---|---|---|
| `--n-per-row` | 10 | Variants generated per row. More = more diversity + more cost. **Most important knob.** |
| `--min-judge-score` | 0.6 | Stricter = fewer variants kept, higher quality. Range 0.0-1.0. |
| `--max-workers` | 8 | Threadpool parallelism. Doesn't change cost, only wall-clock. |
| `--task-mode {cheap\|work\|free\|personal\|personal-strong}` | cheap (Flash) | LM used for both generation and judge. Pro is 10x cost, slightly better semantic awareness. Ollama is free but slower + less reliable. |
| `--budget-override` | n/a | Per-invocation 24h budget cap override (escape hatch). |

## Decision tree

**How big should the amplified dataset be?**

| Target optimizer | Minimum dataset rows (post-amplify) | Recommended | Why |
|---|---|---|---|
| Bootstrap only | n/a (Bootstrap uses ≤4 demos, see Constitution XIV) | DON'T amplify for Bootstrap | Wastes budget |
| MIPROv2 | 200 | 250-400 | Bayesian search needs candidate signal |
| GEPA | 300 | 400-600 | Reflection needs diverse examples to converge |
| Both MIPROv2 then GEPA | 300 | 400+ | Same data feeds both ladder rungs |

**What's the right `--n-per-row`?**

```
ceil((target_rows - input_rows) / input_rows)
```

E.g. 51 input rows, target 300 → ceil((300-51)/51) = 5. Use `--n-per-row 5`.

Add 1-2 extra for headroom (judge drops some variants): `--n-per-row 6-7` to safely clear 300.

**What `--min-judge-score`?**

| Dataset character | Recommended | Why |
|---|---|---|
| Highly specific errors (Cube startup, Zeno port collision, dbt compile failures) | 0.7-0.8 | Stricter category-drift filter — these patterns have crisp boundaries |
| Broad errors (generic tool failures, "command not found") | 0.5-0.6 | Looser — boundaries are fuzzy anyway |
| First-time amplify on an unfamiliar dataset | 0.5 (today's default smoke test value) | See what gets dropped, tune up |
| Production-grade run feeding GEPA | 0.65-0.7 | Quality matters more than yield |

**When to use `--task-mode work` (Pro instead of Flash)?**

Rarely. Reasons to upgrade:
- The Flash variants are obviously generic / wrong / hallucinated when you spot-check the output
- The pattern is subtle (e.g. domain-specific Cube macro errors) and Flash can't reliably preserve the category
- You have budget headroom and want to be sure

**Note (2026-05-18):** Pro for amplify is ~10x cost. Flash is the right default. If your curated set has clear error_text + clear pattern_id, Flash handles it.

## Anti-patterns

1. **Amplifying for Bootstrap.** Bootstrap caps demos at 2-4 regardless of trainset size (Constitution XIV). Amplifying then running Bootstrap is structurally pointless. The `amplify-first` gate on `sio optimize` refuses MIPROv2/GEPA on un-amplified data; Bootstrap is fine on raw curate.

2. **Cranking `--n-per-row` to 50+ "for more data".** Diminishing returns: Flash will produce near-duplicates that the judge drops anyway. The signal MIPROv2 needs is in the *spread* of the pattern, not the *count*. Stay under 15.

3. **Setting `--min-judge-score 0.0`.** You're telling the system "I don't care about category drift." Then MIPROv2 optimizes a prompt against off-category examples. Don't.

4. **Re-amplifying an already-amplified dataset.** Compounding hallucinations. If you need more rows, amplify the ORIGINAL curate output with a higher `--n-per-row`, not the amplified output.

## Recipes

### Recipe 1 — first time amplifying a small (50-100 row) dataset
```bash
# Defaults are fine; aim for ~250 rows (clears MIPROv2 floor with margin)
sio amplify -i ~/.sio/datasets/<your_curated>.jsonl --n-per-row 5
```

### Recipe 2 — feeding GEPA (need 300+ rows)
```bash
# n=6 gives 7x growth, safely clearing the 300 floor after judge drops
sio amplify -i ~/.sio/datasets/<your_curated>.jsonl --n-per-row 6
# Or use the compound:
sio optimize-ladder --trainset-file ~/.sio/datasets/<your_curated>.jsonl --yes
# (auto-amplifies if rows < target-amplified-rows; defaults to --n-per-row 3)
```

### Recipe 3 — stricter quality for production
```bash
# Higher judge threshold, fewer variants kept, higher quality per variant
sio amplify -i <input> --n-per-row 8 --min-judge-score 0.7
```

### Recipe 4 — free-tier exploration
```bash
# Use local Ollama instead of paid Flash. Slower, but $0.
sio amplify -i <input> --n-per-row 5 --task-mode free
```

## Observability

- **Generated rows** → `~/.sio/amplified/<input_stem>_amplified.jsonl`
- **Auto-registered** in `trainsets` table with `source='amplify'` + `parent_dataset_id` pointing at input
- **Run-log** at `~/.sio/runs/<UTC>_amplify_<id>.json` with stage timings + LLM call counts
- **DSPy sidecar** at `~/.sio/runs/<UTC>_amplify_<id>_dspy.jsonl` with full prompt + completion for every Flash call
- **Cost log** at `~/.sio/usage.log` with per-call cost
- **Judge drop diagnostic** in stderr: `JUDGE_DROPPED: N of M variants dropped`

If JUDGE_DROPPED rate is high (>50%):
- The judge is rejecting Flash's output — either `--min-judge-score` is too strict OR the input rows aren't well-categorized in the first place
- Try `--min-judge-score 0.5` to see what the dropped variants looked like
- Or improve the input by running `sio curate --classified` to drop unclassified records before amplifying

## Cross-references

- [`docs/SIO_PHILOSOPHY.md`](SIO_PHILOSOPHY.md) — why measured assist matters; amplify is a *measurement-aided* step, not autonomous
- `.specify/memory/constitution.md` Article XIV — optimizer-specific amplification applicability (NON-NEGOTIABLE — don't amplify for Bootstrap)
- `.specify/memory/constitution.md` Article XV — every amplify output gets a `trainsets` row for reproducibility
- `prd/scratch/sio_dataset_versioning_2026-05-16.md` — the trainsets table design

## Revision log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-05-18 | Initial draft. Captures mechanics + levers + decision tree based on today's first full end-to-end run (HH-specific 51-row curated → amplify --n-per-row 5 → ~306 rows → MIPROv2/GEPA). |
