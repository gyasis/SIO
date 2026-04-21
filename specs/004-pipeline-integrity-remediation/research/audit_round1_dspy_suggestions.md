# Adversarial Audit Round 1 — DSPy + Suggestions

Audit performed: 2026-04-20 (post-120-task Wave closing)
Scope: `src/sio/core/dspy/*`, `src/sio/suggestions/*`, `src/sio/training/*`, `src/sio/core/applier/*`, `src/sio/core/arena/*`, `src/sio/autoresearch/*`, `src/sio/clustering/*`, `src/sio/cli/main.py`
Method: Static read + targeted runtime probe of dspy import surface (no tests executed)
Runtime verified: DSPy `3.1.3` from `/home/gyasisutton/dev/projects/SIO/.venv/`

## CRITICAL findings

### C-R2.1 `dspy.Assert` does not exist in DSPy 3.1.3 — assertions have no backtracking, silently bypass optimization retry
- **File**: `src/sio/core/dspy/assertions.py:32-45` (the compat shim), used by `src/sio/suggestions/dspy_generator.py:952-966` and `src/sio/suggestions/instrumentation.py:93-99`
- **Symptom**: Every `dspy.Assert(...)` call in production code goes to the in-module shim (`_assert_compat`), which just raises plain `AssertionError`. DSPy's backtracking mechanism is never engaged — the LM is never asked to self-correct a malformed rule.
- **Evidence** (runtime-verified from project venv):
  ```
  dspy version: 3.1.3
  has Assert: False
  has Suggest: False
  has assert_transform_module: False
  ```
  Plus a live probe of `assertions.assert_rule_format(pred_with_empty_body)` raised `AssertionError` from the shim, not a DSPy backtrack. DSPy 3.x removed the `dspy.Assert` surface entirely; the project's research.md R-11 note is stale.
- **Impact**: FR-038 (actionable assertion messages) and the advertised "dspy.Assert triggers backtrack" contract are both violated. `SuggestionGenerator.forward` catches the AssertionError, increments `backtrack_count`, and **re-raises** — so every format violation becomes a caller-visible exception instead of a self-healing retry. In `generate_dspy_suggestion`, this is swallowed by the top-level `except Exception` and reported as "DSPy call failed" — the suggestion is lost.
- **Suggested fix**: Migrate to DSPy 3.x assertion idiom. Either (a) use `dspy.Refine(module=..., N=3, reward_fn=..., threshold=...)` to wrap the predictor, or (b) put the format/PHI checks into the metric function used by the teleprompter so optimizers penalize them rather than crash. At minimum, stop advertising "DSPy backtracking" in docstrings and README — the current behavior is "one-shot fail-fast".
- **Labels**: Constitution XI candidate (no stubs) — the shim is cosmetic: it looks like a DSPy asserter but lacks the backtracking contract it claims.

### C-R2.2 MIPROv2 path in `optimize_suggestions` is guaranteed to raise ValueError — dead branch
- **File**: `src/sio/core/dspy/optimizer.py:300-323` (`_run_miprov2_optimization`) and the caller `optimize_suggestions` at lines 398-403
- **Symptom**: `_run_miprov2_optimization` constructs `dspy.MIPROv2(metric=..., auto="medium")` then calls `.compile(module, trainset=corpus, num_trials=10)`. DSPy 3.1.3 explicitly rejects this combination.
- **Evidence**: `MIPROv2.compile` source (from project venv, verified):
  ```python
  # If auto is provided, and either num_candidates or num_trials is not None, raise an error
  if self.auto is not None and (self.num_candidates is not None or num_trials is not None):
      raise ValueError(
          "If auto is not None, num_candidates and num_trials cannot be set, ..."
      )
  ```
  `num_trials=10` is unconditionally passed → ValueError 100% of the time whenever `optimizer='miprov2'` or `'auto'` with corpus ≥ 50 examples (see `_MIPROV2_THRESHOLD` line 66).
- **Impact**: Any call of `sio optimize --optimizer miprov2` (and any auto-selection with ≥50 gold examples) immediately fails. Since `optimize_suggestions` wraps this in `except Exception as exc: raise OptimizationError(...)`, the operator sees "DSPy miprov2 optimization failed: If auto is not None, num_candidates and num_trials cannot be set". This is a Constitution XI candidate — the code path exists but never succeeds.
- **Suggested fix**: Either drop `num_trials` from the `.compile()` call when `auto` is set, OR remove `auto="medium"` and pass `num_candidates` + `num_trials` explicitly. The separate `run_optimize` function at line 891-900 gets this right (uses `auto="light"` with NO num_trials) — align `_run_miprov2_optimization` with that pattern.
- **Next actions**: Re-run `tests/integration/test_optimize_suggestions.py` with the miprov2 path — expect it to currently fail or be skipped; add an explicit regression test after fixing.

### C-R2.3 `load_gold_standards` queries a non-existent `task_type` column — `build_trainset_for` silently returns empty on real databases
- **File**: `src/sio/core/dspy/datasets.py:77-88` vs schema `src/sio/core/db/schema.py:60-72`
- **Symptom**: `datasets.py:81` runs `SELECT * FROM gold_standards WHERE task_type = ? ORDER BY id LIMIT ? OFFSET ?`. The base DDL in `_GOLD_STANDARDS_DDL` has columns: `id, invocation_id, platform, skill_name, user_message, expected_action, expected_outcome, created_at, exempt_from_purge`. There is **no** `task_type` column. Grep of `src/` confirms no ALTER TABLE adds it, and `scripts/migrate_004.py` does not add it either.
- **Evidence**:
  - `grep -n "task_type" src/sio/core/db/schema.py` → 0 hits
  - `grep -n "task_type" scripts/migrate_004.py` → 0 hits
  - Only hits in `tests/integration/test_gepa_vs_baseline.py:43` and `tests/integration/test_dspy_idiomatic.py:44` — test fixtures that **manually** declare the column in an in-memory schema, so tests pass while production fails.
- **Impact**: On any real user DB, `load_gold_standards` raises `sqlite3.OperationalError: no such column: task_type`, which the function catches and logs as `gold_standards query failed: %s`, then returns `[]`. Every subsequent call through `build_trainset_for("suggestion_generator")` returns `[]`. `run_optimize` then raises `InsufficientData("trainset has 0 examples; need >= 5")`. The closed-loop DSPy optimization (FR-036, Wave 4 US1) never runs in production — it works only in the synthetic test harness.
- **Suggested fix**:
  1. Add `task_type TEXT NOT NULL DEFAULT 'suggestion'` to `_GOLD_STANDARDS_DDL` and a migration that ALTERs existing DBs. Also add `dspy_example_json TEXT` and `promoted_by TEXT` (both also missing from base schema but referenced at `gold_standards.py:116-117`).
  2. Add a smoke test that runs `sio db migrate` then `sio optimize` against a freshly initialized DB; expect it to reach at least the `InsufficientData` path (not OperationalError).
- **Labels**: HIGH risk, but marked CRITICAL because it is the single point of failure that silently disables Wave-4's flagship feature on every real installation.

## HIGH findings

### H-R2.1 `optimize_suggestions` calls `dspy.configure(lm=lm)` without binding an adapter — drift from `run_optimize` pattern
- **File**: `src/sio/core/dspy/optimizer.py:374-377`
- **Symptom**: The legacy path sets the LM but does not call `get_adapter(lm)` and does not pass `adapter=` to `dspy.configure`. The newer `run_optimize` at lines 846-851 does both correctly. In DSPy 3.1.3 the default adapter is `dspy.ChatAdapter(use_native_function_calling=False)`, so OpenAI/Anthropic models silently lose native function-calling performance.
- **Evidence**: `lm_factory.get_adapter` explicitly encodes the provider-aware choice. Omitting it in `optimize_suggestions` violates FR-040 (provider-aware adapter) and R-12.
- **Impact**: Medium performance regression (JSON parse failures in OpenAI tool_use), but functional. Quiet divergence from the audited `run_optimize` path.
- **Suggested fix**: In `optimize_suggestions`, replace `dspy.configure(lm=lm)` with `dspy.configure(lm=lm, adapter=get_adapter(lm))`.

### H-R2.2 `_run_optimization_run`-style transaction is non-atomic — prior row stays active if the new INSERT fails
- **File**: `src/sio/core/dspy/optimizer.py:685-757` (`_record_optimization_run`)
- **Symptom**: The function issues two separate `UPDATE optimized_modules SET active=0 ...` statements (lines 703-717), then tries the rich INSERT, falling back to a minimal INSERT. Both branches eat exceptions with bare `except Exception: pass`. If the rich INSERT raises and the minimal INSERT **also** raises (schema so minimal that neither fits), the UPDATEs have already deactivated the prior winner — leaving zero active modules in the table. `conn.commit()` at line 756 commits this inconsistent state.
- **Evidence**: Lines 702-717 have two independent try/except blocks wrapping the UPDATEs, plus line 746 catches all exceptions on the first INSERT and re-tries a different INSERT with no rollback. There is no `BEGIN IMMEDIATE` or SAVEPOINT bracketing the UPDATE→INSERT sequence.
- **Impact**: Race-free under normal load, but a malformed schema or disk-full error can leave the system with no active optimized module and no surviving previous one. Subsequent `_load_optimized_or_default` silently falls back to a fresh unoptimized module — silent regression.
- **Suggested fix**: Wrap the entire deactivate-then-insert sequence in a SAVEPOINT (`conn.execute("SAVEPOINT activate_module")`) and rollback to it on any exception before re-raising. Additionally, emit a loud warning when the minimal-schema fallback fires — the silent `except: pass` at 708 and 717 hides schema problems.

### H-R2.3 `load_compiled` catches plain `Exception` and silently partial-loads with `pass` — failures become data corruption
- **File**: `src/sio/core/dspy/persistence.py:182-203`
- **Symptom**: `program.load(str(path))` wrapped in `except (KeyError, Exception) as exc`. On any failure — including corrupted JSON, schema-mismatch of the saved state, or predictor mismatch — it reads the file again and iterates `program.named_predictors()`, calling `predictor.load_state(state[name])` inside a bare `try/except Exception: pass`. This means **every** per-predictor load failure is swallowed and the function returns a partially-loaded module with NO signal to the caller.
- **Evidence**: Lines 198-203 — `for name, predictor in program.named_predictors(): if name in state: try: predictor.load_state(state[name]) except Exception: pass`.
- **Impact**: If a GEPA-compiled artifact gets moved to a schema-incompatible `SuggestionGenerator` (e.g., renamed predictor from `generate` to `predict`), `load_compiled` returns a vanilla (unoptimized) module while `sio recall` / `sio suggest` reports "loaded optimized module" in INFO logs. Optimization effectively did nothing. Same root cause as C-R2.1: the codebase tolerates silent failure where it should surface.
- **Suggested fix**:
  1. Catch the specific exception DSPy raises on schema mismatch (check `program.load` source) and log at WARNING with the path and mismatched keys.
  2. When at least one predictor fails to load, raise (or at minimum, log ERROR and return a fresh instance with a clear `.loaded_successfully=False` sentinel).
  3. Add a SHA-256 of the module's predictor structure at save time (file header) and verify on load — refuse to return a partially-loaded module.

### H-R2.4 `optimize_suggestions` lacks the "mark prior inactive before insert" atomicity that `run_optimize` provides
- **File**: `src/sio/core/dspy/optimizer.py:432-440` (calls `save_module`) vs `src/sio/core/dspy/module_store.py:48-67`
- **Symptom**: `save_module` does `UPDATE ... SET is_active=0` THEN `INSERT ... is_active=1, 1 ...` with a single `conn.commit()` at the end (line 67). Between the UPDATE and the INSERT, another concurrent `save_module` call could: (a) UPDATE again (no-op since already 0), (b) INSERT its own active row before ours, (c) our INSERT runs and now we have **two active rows** with `is_active=1` for the same `module_type`.
- **Evidence**: No `BEGIN IMMEDIATE` or WAL isolation guarantee; default SQLite autocommit with deferred transaction semantics. `conn.commit()` at line 67 occurs once but the UPDATE and INSERT are logically separate statements.
- **Impact**: Race-window is narrow but real under parallel `sio optimize` invocations (e.g., cron + manual). Symptom: `get_active_module` hits the `ORDER BY created_at DESC LIMIT 1` tiebreaker — usually correct, but if created_at collides at 1-second resolution, undefined.
- **Suggested fix**: Wrap UPDATE→INSERT in `conn.execute("BEGIN IMMEDIATE")` and an explicit commit. Make `is_active` a **partial unique index** on `(module_type) WHERE is_active=1` so SQLite enforces the invariant.

### H-R2.5 `RecallEvaluator.forward` returns raw `result` on score coercion failure — bypasses clamping
- **File**: `src/sio/training/recall_trainer.py:68-77`
- **Symptom**: The `try/except (TypeError, ValueError)` on line 76-77 catches numeric coercion errors of `float(result.score)` and returns the **raw** `result` (not a clamped Prediction). The docstring promises `score (float): Recall score in [0, 1]` — this contract is violated whenever the LM returns a non-numeric string (e.g., "high", "0.8 out of 1", an empty string). Downstream consumers that call `.score` then fail with TypeError downstream, having defeated the point of defensive clamping here.
- **Evidence**: Lines 56-77 — `return dspy.Prediction(score=clamped, reasoning=result.reasoning)` in the happy path, but `except ... return result` in the failure path. If `result.score = "high"`, the caller gets `"high"` back when it expected a float.
- **Impact**: Medium. Metric calls that use RecallEvaluator's output (e.g., GEPA feedback during a `run_optimize` cycle targeting `recall_evaluator`) will receive a non-numeric score, likely crash the trainset loop, and the optimizer aborts.
- **Suggested fix**: On coercion failure, return `dspy.Prediction(score=0.0, reasoning=f"unparseable: {result.score!r}")` so the contract is preserved.

### H-R2.6 `dspy_generator.generate_dspy_suggestion` uses `SuggestionModule` (4-input) but `SuggestionGenerator.forward` is 3-input — two divergent modules in one namespace
- **File**: `src/sio/suggestions/dspy_generator.py:461-466` invokes `module.forward(error_examples=, error_type=, pattern_summary=, tool_input_context=)`, and `_load_optimized_or_default` returns `SuggestionModule` (`src/sio/core/dspy/modules.py:17-40`). Meanwhile `SuggestionGenerator` at `dspy_generator.py:882-970` has a completely different 3-input signature and is NOT used by `generate_dspy_suggestion` at all.
- **Symptom**: Two classes named in the "suggestion generator" role, each using a different DSPy signature (`SuggestionGenerator` sig from signatures.py:61-113 for the 4-input `SuggestionModule`; `PatternToRule` sig from signatures.py:8-46 for the 3-input `SuggestionGenerator` class). The `MODULE_REGISTRY` in persistence.py registers only `suggestion_generator → SuggestionGenerator` (the 3-input one). An artifact saved from the 4-input `SuggestionModule` will not round-trip through `load_compiled("suggestion_generator", path)` — it will load into the wrong class and partially-load (see H-R2.3).
- **Evidence**:
  - `modules.py:26` — `self.generate = _dspy.ChainOfThought(SuggestionGenerator)` — the **signature** `SuggestionGenerator`, not the `SuggestionGenerator` *class* from `dspy_generator.py:882`.
  - `dspy_generator.py:910` — `self.generate = dspy.ChainOfThought(PatternToRule)` (different signature)
  - `persistence.py:33-67` — `_lazy_load_suggestion_generator` imports the class from `sio.suggestions.dspy_generator` (which is the 3-input PatternToRule-based one).
  - `optimizer.py:389` — `optimize_suggestions` creates `SuggestionModule()` (4-input) and saves it via `save_module` at line 432. Later at runtime `_load_optimized_or_default` calls `load_module(SuggestionModule, ...)` — so save+load are in sync **within** `optimize_suggestions`.
  - BUT `run_optimize` in the same file (lines 774-958) uses `dspy.ChainOfThought(PatternToRule)` and the MODULE_REGISTRY 3-input shim. So the two optimizer entry points produce incompatible artifacts that cannot be hot-swapped.
- **Impact**: Operators who run `sio optimize ...` using the CLI wrapper that currently calls `run_optimize` will produce 3-input PatternToRule artifacts. But `generate_dspy_suggestion` at inference time loads and expects 4-input `SuggestionModule` artifacts. Artifacts silently go unused OR fail to load (see H-R2.3 silent partial-load). This is the same symptom class as C-R2.1: silent no-op where an error should surface.
- **Suggested fix**:
  1. Pick ONE canonical `suggestion_generator` module. The 3-input `SuggestionGenerator` (class) + `PatternToRule` signature is the documented FR-036 contract — keep that, delete `SuggestionModule`.
  2. Retire or migrate `generate_dspy_suggestion` to use the 3-input module: extract 3 fields from the pattern / dataset and drop `tool_input_context` or push it into `pattern_description`. The 4-input signature's extra `tool_input_context` input is a nice-to-have that does not justify two parallel module surfaces.
- **Labels**: HIGH — this is the regression surface where save/load round-trip (Constitution XI, FR-039) silently diverges.

### H-R2.7 Centroid reuse compares `description` — but `_load_stored_centroids` keys by `description`, not `pattern_id`, so slug collisions cross clusters
- **File**: `src/sio/clustering/pattern_clusterer.py:266-310` and `441-446`
- **Symptom**: The centroid cache maps `description → vector`. A pattern's `description` is the first error's `error_text` (line 479). Two distinct clusters from different patterns that happen to share the same first error text (which happens any time two sessions log the same error string) will collide in the cache and reuse the wrong centroid. Additionally, if any two patterns share a description key, the second's centroid overwrites the first at line 306 (`cache[description] = vec`).
- **Evidence**: Line 304: `cache[description] = vec` — no de-duplication.
- **Impact**: Low-frequency but real. After enough cycles, two unrelated clusters with identical leading-error text can share one centroid, effectively merging their embeddings. Detection would require running the same dataset twice and checking that pattern-to-centroid mapping is stable.
- **Suggested fix**: Key the cache by `pattern_id` (the slug), not by `description`. Change `_load_stored_centroids` to `SELECT pattern_id, centroid_embedding, description ...` and return `{(pattern_id_or_description_key): vec}`. The existing call site at lines 442-446 uses `err["error_text"]` as the lookup key — align that with the chosen key.

## MEDIUM findings

### M-R2.1 `autoresearch_run_once` declares but never increments `rejected_metric`
- **File**: `src/sio/autoresearch/scheduler.py:97, 115-130`
- **Symptom**: The `counts["rejected_metric"]` counter is initialized at 0 and returned in the summary dict, but no code path increments it. The docstring claims that `arena_passed=1 AND metric_score < auto_approve_above` is a `rejected_metric` outcome — but the code instead increments `pending_approval` in that branch (line 127).
- **Evidence**: Static read. Grep confirms no `counts["rejected_metric"] += 1` or equivalent assignment.
- **Impact**: Metrics dashboard always shows zero `rejected_metric`, hiding whether the metric gate ever fires. Operators cannot tell whether the threshold is too lax or too strict.
- **Suggested fix**: In the `arena_passed == 1` branch where `metric_score < auto_approve_above`, increment `rejected_metric` instead of `pending_approval`. Or: update docstring to match code (metric below threshold = pending, requires manual review).

### M-R2.2 `_ts()` in atomic_write relies on `utc_now_iso()` producing `.` before timezone — brittle string slice
- **File**: `src/sio/core/applier/writer.py:145-155`
- **Symptom**: `iso[:19]` strips the subsecond component, then `.replace("-", "").replace(":", "")` compacts to `"20260420T143211"`. This assumes `utc_now_iso()` always returns at least 19 chars up to seconds. If `utc_now_iso()` ever returns `"2026-04-20T14:32"` (no seconds), `iso[:19]` includes leading chars of the next field, producing `"20260420T1432+00"`. The `+` breaks backup filename conventions on some filesystems.
- **Evidence**: Static read only — would require `utc_now_iso()` source inspection to confirm it is robust.
- **Impact**: Low — unlikely to fire unless clock lib regresses.
- **Suggested fix**: Use `datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")` directly rather than slicing an ISO string.

### M-R2.3 `generate_dspy_suggestion` retry-on-content-filter does not re-log instrumentation — counters lost on retry
- **File**: `src/sio/suggestions/dspy_generator.py:467-497`
- **Symptom**: When the first `module.forward(...)` raises a content-filter exception, the function retries with aggressive sanitization. But the retry call replaces `result` — the backtrack counters from the first forward (if any) are discarded. `SuggestionGenerator.forward` builds an `instrumentation` dict locally and returns it attached to `pred`, so a successful retry attaches only the retry's counters.
- **Impact**: Minor — observability only. The `suggestions.instrumentation_json` under-counts genuine format/PHI violations on retried calls.
- **Suggested fix**: On retry, accumulate `instrumentation.forward_count` across attempts, or emit a separate `retry_count` field.

### M-R2.4 `assert_rule_format` uses `pred.rule_body.split(".")` which miscounts common abbreviations
- **File**: `src/sio/core/dspy/assertions.py:87-93`
- **Symptom**: "e.g. do X. Also do Y. Done." splits as `["e", "g", " do X", " Also do Y", " Done", ""]` — 6 parts → n_parts=6, so n_parts-1=5. Assertion fails even though the rule has only 3 sentences. Any rule with "e.g.", "i.e.", "etc.", "No. 1", URLs, or decimals triggers a false failure.
- **Impact**: With the shim only raising AssertionError, every rule containing a common abbreviation is rejected at generation time. This is a direct candidate cause of the pre-existing "92% rejection rate" finding (M8 in the prior audit).
- **Suggested fix**: Use a proper sentence tokenizer (e.g., NLTK `sent_tokenize` or a regex like `r'(?<=[.!?])\s+[A-Z]'`) or soft-fail (warn) instead of hard assertion. Also count actual sentences: `len([s for s in text.split('.') if s.strip()])`.

### M-R2.5 `_PHI_TOKENS` is case-sensitive — "ssn" or "Mrn" pass the filter
- **File**: `src/sio/core/dspy/assertions.py:52-59, 110-117`
- **Symptom**: `if token not in blob` is a case-sensitive substring check. `"SSN"` is in the list, but `"ssn"` or `"My social security number is 123"` pass through.
- **Impact**: Low (PHI in SIO training data is unlikely). But the assertion advertises "no PHI tokens" — the contract is weaker than the docstring implies.
- **Suggested fix**: Lower-case both the blob and token list, or use a case-insensitive regex.

### M-R2.6 `promote_to_gold` silently swallows `sqlite3.OperationalError` on older schemas
- **File**: `src/sio/core/arena/gold_standards.py:111-146`
- **Symptom**: The fallback INSERT when the rich schema INSERT fails uses `str(row["correct_outcome"])` — but `expected_outcome` is supposed to be the outcome description, not the integer 1/0. Passing `"1"` to expected_outcome is semantically incorrect and will poison the GEPA trainset (which in turn will confuse PatternToRule generation).
- **Evidence**: Lines 123-124 and 143-144 — `str(row["correct_outcome"]) if row["correct_outcome"] is not None else None` is stuffed into `expected_outcome`.
- **Impact**: Minor — `expected_outcome` is not widely read in the current pipeline, but if FR-041 extensions make it an input, trainset quality degrades.
- **Suggested fix**: Use `row.get("expected_outcome_text")` or an explicit description column; do not stuff a boolean into a text-description field.

### M-R2.7 `metrics.llm_judge_recall` lazy-init of `_JUDGE_PREDICTOR` has no thread safety
- **File**: `src/sio/core/dspy/metrics.py:151-193`
- **Symptom**: Lines 171-185 check/set `_JUDGE_PREDICTOR` without a lock. Two concurrent threads entering first-call simultaneously both assign. Harmless in practice (idempotent), but DSPy's `dspy.Predict(...)` may create side-effects (cache init, request adapter) that racing would make inconsistent.
- **Impact**: Very low — SIO is not multi-threaded at the metric layer today.
- **Suggested fix**: Use a threading.Lock or `functools.lru_cache` on a zero-arg factory.

## LOW findings

### L-R2.1 `ranker.rank_patterns` falls back to `now` on malformed timestamp, boosting bad data to the top
- **File**: `src/sio/clustering/ranker.py:74-92`
- **Symptom**: A pattern with `last_seen = ""` or `last_seen = "garbage"` is treated as maximally recent (days_since=0, recency_weight=1.0). This creates an incentive: malformed data gets the **highest** rank. A pattern with a real timestamp 1 day ago scores 0.5×error_count, but a broken one scores 1.0×error_count.
- **Suggested fix**: Fall back to the median timestamp across siblings, or rank malformed timestamps LAST (recency_weight = 0.0) rather than first.

### L-R2.2 `assertions.py` module-level monkeypatch of `dspy.Assert` is a side effect at import time
- **File**: `src/sio/core/dspy/assertions.py:32-45`
- **Symptom**: Importing `sio.core.dspy.assertions` installs a global attribute on the `dspy` module. Other DSPy-using packages (e.g., the `dspy.teleprompt.GEPA` internals) now see a third-party `Assert`. In the current code this is harmless because DSPy 3.1.3 has no internal `dspy.Assert` user, but it is fragile.
- **Suggested fix**: Install the shim lazily on first call, OR inside a context manager the test suite can scope.

### L-R2.3 `confidence` score blending in `generate_dspy_suggestion` uses literal 0.5 weights, not configurable
- **File**: `src/sio/suggestions/dspy_generator.py:554-555`
- **Symptom**: `confidence = 0.5 * pattern_confidence + 0.5 * quality_score` — magic numbers. Operators tuning the auto-approval gate (`_AUTO_CONFIDENCE_THRESHOLD = 0.8`) cannot re-balance without editing code.
- **Suggested fix**: Expose weights in `SIOConfig`.

### L-R2.4 `embedding_similarity` falls back to token-overlap when fastembed import fails — identity not advertised
- **File**: `src/sio/core/dspy/metrics.py:113-116`
- **Symptom**: `except (ImportError, Exception)` is redundant (Exception covers ImportError) and silently degrades from cosine-similarity to Jaccard without logging. The caller cannot tell from the returned float whether it got a real embedding similarity or a token overlap.
- **Suggested fix**: Narrow the except to `ImportError`; on other exceptions log a warning; add a `source` field to a named tuple return or log the fallback event.

### L-R2.5 `_compute_similarity` in merger.py redundantly catches `Exception` — masks ImportError from `numpy`
- **File**: `src/sio/core/applier/merger.py:88-112`
- **Symptom**: Same pattern — `except (ImportError, AttributeError, Exception)` is equivalent to `except Exception`. Combined with the nested try/except on line 99-107, any failure at all (including `OSError` from a broken fastembed model file) degrades silently to Jaccard without logging.
- **Suggested fix**: Narrow the except blocks and log fallback reason.

## Pre-existing finding verifications

### H11 (centroid_embedding BLOB + model_hash validation) — VERIFIED & CORRECT
- Implementation in `pattern_clusterer.py:100-152` packs `[uint32 dim][8 bytes model_hash][float32[dim]]` per R-9. Unpack validates size (`if len(blob) != expected_size: raise ValueError`). Mismatch hashes are excluded from cache (`if stored_hash == current_model_hash: cache[description] = vec`, line 305).
- However, see H-R2.7 above — cache keying by description is still a correctness risk.

### M8 (92% rejection rate after DSPy assertions) — ROOT CAUSE DEEPENED, NOT FIXED
- The pre-existing finding said the assertion gate was too strict. Investigation here reveals **two compounding causes**:
  1. C-R2.1: `dspy.Assert` doesn't backtrack — every violation is a hard failure, never self-corrected.
  2. M-R2.4: `rule_body.split(".")` miscounts abbreviations, triggering false `>3 sentence` rejections on valid rules.
- Combined effect: a rule saying "Use dspy.Refine, i.e., wrap the module. No more than 3 sentences." is rejected as 5 sentences. Operators sees a false 92% rejection rate.
- Recommendation: fix M-R2.4 first (sentence counter), then fix C-R2.1 (move checks into metric). Expect the rejection rate to drop substantially.

### H9 (trivial recall_metric) — PARTIALLY REPLACED
- `metrics.exact_match` + `metrics.embedding_similarity` + `metrics.llm_judge_recall` are all real implementations (verified by static read). `suggestion_quality_metric` decomposes into specificity, actionability, surface_accuracy — all real sub-scorers.
- However:
  - `_score_specificity` returns `0.5` as a floor when it cannot extract details from `error_examples` (line 306), which is a partial stub — it biases the score upward by default.
  - `train_recall_module` at `recall_trainer.py:341-349` uses a **trivial** metric that just counts non-empty output fields. This is the original H9 finding and has NOT been replaced in the recall training pipeline (only the suggestion metric was upgraded).
- Recommendation: Replace `recall_metric` in `recall_trainer.py` with a real recall scorer (e.g., token overlap with the expected runbook, or a RecallEvaluator-driven LLM judge).

### Constitution XI (no stubs) verification
- **Real implementations**: `atomic_write`, `apply_change`, `rollback_applied_change`, `merge_rules`, `promote_to_gold`, `cluster_errors`, `rank_patterns`, `grade_pattern`, `run_grading`, `autoresearch_run_once` — all perform real, testable work.
- **Soft stubs identified**:
  - `assertions.dspy.Assert` shim (C-R2.1) — looks real but lacks backtracking.
  - `_run_miprov2_optimization` (C-R2.2) — always raises, never succeeds.
  - `recall_metric` in `recall_trainer.py:341-349` — counts non-empty fields, not actual recall quality (H9).
  - `_score_specificity` (metrics.py:306) — returns 0.5 floor on no-details.
  - `load_compiled` silent-partial-load (H-R2.3) — cannot fail, returns a sometimes-unusable module.
  - `MODULE_REGISTRY` shims in persistence.py (`_SuggestionGeneratorShim`, `_RecallEvaluatorShim`) — used as forward-compat but instantiated silently when the real class import fails. Caller cannot tell which they got.

## Summary

**4 CRITICAL** / **7 HIGH** / **7 MEDIUM** / **5 LOW** findings.

Blocking issues for real-world deployment:
1. **C-R2.1** — `dspy.Assert` shim doesn't backtrack → every format/PHI violation is a hard failure in production.
2. **C-R2.2** — MIPROv2 path in `optimize_suggestions` unconditionally raises → `sio optimize --optimizer miprov2` and auto-selection ≥50 examples are both dead branches.
3. **C-R2.3** — `task_type` column missing from schema → `build_trainset_for` always returns `[]` on a fresh DB → `run_optimize` never runs in production, only in tests.
4. **H-R2.6** — two divergent "SuggestionGenerator" surfaces → save/load artifacts silently incompatible between `optimize_suggestions` and `run_optimize` paths.

Until these are fixed, the closed-loop DSPy optimization (FR-036, FR-039, Wave 4 US1) is not actually functional on a real user installation, regardless of what the test suite reports. The test fixtures hide C-R2.3 by manually declaring `task_type` in an in-memory schema; the CLI test lens does not exercise the MIPROv2 branch.

Recommend remediation priority:
1. Add migration + schema update for `task_type`, `dspy_example_json`, `promoted_by` (fixes C-R2.3 and M-R2.6).
2. Fix MIPROv2 compile signature (drop `num_trials` when `auto` is set) — fixes C-R2.2.
3. Replace `dspy.Assert` usage with `dspy.Refine` or metric-driven penalties (fixes C-R2.1).
4. Consolidate the two SuggestionGenerator surfaces into one canonical module (fixes H-R2.6).
5. Then re-run the acceptance suite — expect several previously-passing-by-accident tests to regress and need real fixtures.
