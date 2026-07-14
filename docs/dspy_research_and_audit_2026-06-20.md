# DSPy / GEPA — Research Brief + SIO Implementation Audit (2026-06-20)

Consolidated from a multi-source research pass (DSPy official docs via context7, web/practitioner
sources, Gemini deep-research, and the memory_lane_v4 corpus) **plus** an audit of SIO's current DSPy
usage (`src/sio/core/dspy/`). Goal: are we using DSPy the right way, and what to improve?

Source tags: `[docs]` dspy.ai · `[web]` practitioner/paper · `[deep]` Gemini · `[corpus]` memory_lane_v4.

---

## Part 1 — Research findings (best practices)

### Optimizer choice (by bottleneck)
- **Instruction wording** → GEPA / MIPROv2 / COPRO. **Demos/format** → BootstrapFewShot(+RandomSearch). **Weights** → BootstrapFinetune. `[docs]`
- **GEPA is sample-efficient**: +10–13% vs MIPROv2, **35× fewer rollouts** than RL, ~9× shorter prompts (paper arxiv:2507.19457). Right for small datasets. `[web]`
- **MIPROv2 wants ~100–200 examples**; below that it overfits — prefer GEPA or BootstrapFewShot+COPRO. A practitioner got +20% on 30–40 examples with Bootstrap+COPRO. `[web][corpus]`

### GEPA config
- **`ScoreWithFeedback` metric** (`dspy.Prediction(score, feedback)`) — feedback must be **instructive**: the failure mode + evidence + the takeaway. A bare score starves the reflector; per-criterion sub-scores let it target the weakest dimension. `[docs][web][deep]`
- **Asymmetric models**: cheap task/student LM + **strong reflection_lm at temp 1.0** (reflection is ~5% of calls → premium reflector is economical). `[web][deep][docs]`
- **Separate train/val** (~70/30; reserve 3–5 for val on tiny sets) — the primary overfit lever; don't `valset=trainset`. `[docs][web][deep]`
- **`reflection_minibatch_size` 3–5**; if ≥ trainset size it memorizes. `[web][deep]`
- **Budget**: `auto="light"` for 10–30 examples, or `max_metric_calls ≈ 5–10× trainset`; exactly one of auto/max_metric_calls/max_full_evals. `[docs][web]`
- `candidate_selection="pareto"` + `use_merge=True` (+2–5%) + `track_stats=True`. `[docs][deep]`

### The gotchas (net-new, beyond docs)
1. **Anti-verbatim overfit (Dropbox, real):** GEPA copies example-specific terms (names, phrases) into the prompt → train-up, generalize-down. **Add a metric/instruction constraint forbidding example-specific content.** `[web]`
2. **Prompt-bloat guard:** length-penalized objective, e.g. `score = quality − 0.01·(tokens/1000)`. `[deep]`
3. **LLM-judge biases** to design against: position, **verbosity** (judges favor longer output >90%), self-preference (mitigated by reflector ≠ task model). `[corpus]`
4. **Deploy safety:** for a judge already in production, prefer constrained optimization (select from a validated instruction library) over a full rewrite, to protect the output contract. `[web]`

### Real-world validation (near-identical to our use case)
- **Dropbox Dash optimized an LLM *relevance judge* with DSPy on `gpt-oss-120b` → 45% error reduction; 1–2 weeks → 1–2 days.** `[web]`
- **Nubank support judge: 68.9% → 88.9%** via GEPA. `[web]`

---

## Part 2 — SIO audit (`src/sio/core/dspy/`)

| Area | Status | Evidence |
|---|---|---|
| Optimizer auto-selection | ✅ | `optimizer.py:66` `<50→Bootstrap`, `≥50→MIPROv2`, explicit GEPA |
| GEPA wiring | ✅ | `optimizer.py:1049` reflection_lm + `auto` (SIO_GEPA_BUDGET) + `reflection_minibatch_size=3` + `track_stats=True` |
| Train/val split | ✅ | `optimizer.py:924-941` offset split + 1/3 fallback (not bare `valset=trainset`) |
| Model defaults | ✅ | `lm_factory.py:424/438` task=`gemini/gemini-flash-latest`, reflection=`gemini/gemini-pro-latest`; **gpt-4o BANNED** (banned-model enforcement) |
| GEPA feedback metric | ✅ (partial) | `optimizer.py:962-1001` `_gepa_metric` returns `dspy.Prediction(score, feedback)` with per-sub-score hints |
| Save/load safety | ✅ | `persistence.py` mismatch detection; `module_store.py` atomic txn |
| Base metric shape | ◐ | `metrics.py:482` `suggestion_quality_metric` returns bare float/bool (fine for Bootstrap/MIPRO, which don't use feedback) |

**Verdict: SIO uses DSPy soundly** — sensible optimizer routing, real GEPA with reflection_lm + feedback + train/val split, gpt-4o banned, robust persistence. The research surfaces *refinements*, not a redesign.

### Recommended improvements (→ tracked as a GitHub issue)
1. **Anti-verbatim guard** in `_gepa_metric` feedback/instruction — forbid baking example-specific tokens (project names, error strings) into the evolved rule. *(Highest value — direct Dropbox lesson; SIO optimizes rule-gen prompts that can memorize project terms.)*
2. **Length/bloat penalty** in the GEPA objective.
3. **Raise the MIPROv2 threshold** (`_MIPROV2_THRESHOLD` 50 → ~100) per the 100–200-example guidance; use GEPA in the 50–100 band.
4. **`add_format_failure_as_feedback=True`** on GEPA (cheap robustness for structured outputs).
5. **Reflection-LM option**: add `gpt-oss:120b` (Ollama Cloud) as a documented reflector — fast, clean, and **validated by Dropbox for judge-optimization**. Keep gemini-pro as default.
6. Fix the **doc/code default mismatch**: `lm_factory.py:8` docstring says reflection default `openai/gpt-5` but code defaults to `gemini-pro-latest`.
7. Ensure GEPA **feedback is instructive** (evidence + takeaway), not just which sub-score is low.

**Sources:** [DSPy GEPA docs](https://dspy.ai/api/optimizers/GEPA/overview/) · [GEPA paper arxiv:2507.19457](https://arxiv.org/abs/2507.19457) · [Dropbox Dash judge w/ DSPy](https://dropbox.tech/machine-learning/optimizing-dropbox-dash-relevance-judge-with-dspy) · [HF DSPy-GEPA cookbook](https://huggingface.co/learn/cookbook/en/dspy_gepa) · [TDS DSPy optimization](https://towardsdatascience.com/systematic-llm-prompt-engineering-using-dspy-optimization/) · `[corpus]` memory_lane_v4.
