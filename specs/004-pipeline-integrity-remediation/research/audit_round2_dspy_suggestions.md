# Adversarial Audit Round 2 — DSPy + Suggestions (post-fix-pack)

**Auditor**: Adversarial Hunter #2, Round 2
**Audit performed**: 2026-04-21
**Baseline commits**: `3d5c7d9` (fix-pack), `da33196` (principle fix)
**Branch**: `004-pipeline-integrity-remediation`
**DSPy runtime**: 3.1.3 (`/home/gyasisutton/dev/projects/SIO/.venv/`)
**Method**: Static read + live Python repros (`.venv/bin/python -c ...`) on every major claim

---

## Round 1 CRITICAL verification

### C-R2.1 — `dspy.Assert` removed → CLOSED (with a DEAD-HOOK caveat)
- Grep `dspy\.Assert\(` across `src/sio/` returns a single **docstring-only** reference in `src/sio/suggestions/instrumentation.py:121` — no live call sites. `hasattr(dspy, 'Assert') == False` confirmed at runtime.
- Live repro: `from sio.core.dspy.assertions import validate_rule_format, assert_rule_format, validate_no_phi, ValidationError` imports cleanly; `validate_rule_format(pred_with_empty_body)` returns `False`; `assert_rule_format(...)` raises `ValidationError`.
- `SuggestionGenerator.forward` (`dspy_generator.py:955-1005`) now implements self-healing with a 2-retry loop and prompt-hint injection — this is the **correct DSPy 3.x idiom**.
- **Caveat (NEW finding, tracked below as N-R2D.6)**: `sio/suggestions/instrumentation.py::_InstrumentedModule` is now a **dead hook**. Its `__call__` checks `hasattr(dspy, "Assert")` before patching; in DSPy 3.1.3 this is always False, so `backtrack_count` is hardcoded to 0 on every forward. The module's documented purpose — count assertion backtracks for `suggestions.instrumentation_json` — is unreachable.

**Status**: CLOSED for the blocking behaviour. NEW MEDIUM-severity defect at the instrumentation layer (see N-R2D.6).

### C-R2.2 — MIPROv2 `auto` + `num_trials` crash → CLOSED
- Verified `dspy.teleprompt.MIPROv2.compile` signature via `inspect.signature`: the ValueError path still exists (`auto` and `num_trials` mutually exclusive).
- `optimizer._run_miprov2_optimization` (line 300-326) now calls `.compile(module, trainset=corpus)` **without** `num_trials` — inline comment documents the DSPy 3.x constraint.
- `run_optimize` MIPRO branch (line 909-925) uses `auto="light"` similarly without `num_trials`.
- Both branches are now compile-signature-compatible.

**Status**: CLOSED.

### C-R2.3 — `task_type` column missing → CLOSED
- Live repro: `from sio.core.db.schema import init_db; c = init_db(':memory:'); [r[1] for r in c.execute('PRAGMA table_info(gold_standards)')]` returns `['id', 'invocation_id', 'platform', 'skill_name', 'user_message', 'expected_action', 'expected_outcome', 'created_at', 'exempt_from_purge', 'task_type', 'dspy_example_json', 'promoted_by']` — all three new columns present.
- `task_type` has `DEFAULT 'suggestion'`, so legacy INSERTs without it (e.g. `gold_standards.promote_to_gold` at lines 112-129) still work.
- `datasets.load_gold_standards` now query runs against the fresh schema without `OperationalError`.

**Status**: CLOSED.

### C-R2.6 — two divergent SuggestionGenerator surfaces → **PARTIAL; a new critical wiring bug was introduced**
- `grep "class SuggestionGenerator|class SuggestionModule"` on `src/sio/` returns three hits: the **signature** `SuggestionGenerator` at `signatures.py:61`, the **module class** `SuggestionGenerator` at `dspy_generator.py:887`, AND the legacy `SuggestionModule` at `modules.py:17`. `SuggestionModule` is still in use (see live grep of `SuggestionModule` — 10 hits across `dspy_generator.py` and `optimizer.py` docstrings).
- The fix-pack swapped `optimize_suggestions` from using `SuggestionModule` to using `SuggestionGenerator` (optimizer.py:394) — **but left the trainset source unchanged**. The new cross-wiring is strictly worse than the pre-fix state. Details in N-R2D.1 and N-R2D.2 below.

**Status**: PARTIAL. The two class definitions still coexist; the fix created a new signature/trainset mismatch. Treat as **OPEN**.

---

## Round 1 HIGH verification

### H-R2.1 — `optimize_suggestions` missing adapter binding → CLOSED
- `optimizer.optimize_suggestions` at lines 377-381 now computes `adapter = get_adapter(lm)` and passes both to `dspy.configure(lm=lm, adapter=adapter)` — matches the `run_optimize` pattern at lines 872-874.

### H-R2.2 — non-atomic deactivate→insert in optimizer → **PARTIAL**
- `_record_optimization_run` (optimizer.py:690-780) IS wrapped in SAVEPOINT/ROLLBACK TO — correct for the `run_optimize` call path.
- BUT `optimize_suggestions` (optimizer.py:437) calls `module_store.save_module`, not `_record_optimization_run`. `module_store.save_module` at lines 48-67 still has the exact non-atomic UPDATE→INSERT pattern Round 1 flagged. The fix-pack did not touch this function.
- `recall_trainer.save_trained_module` at lines 450-469 has the same non-atomic pattern.

**Status**: PARTIAL. Flagged as NEW finding N-R2D.3.

### H-R2.3 — silent partial-load in `load_compiled` → **PARTIAL; still silent when predictor keys fully mismatch**
- `persistence.load_compiled` at lines 190-222 DOES raise `ArtifactStructureMismatch` when at least one named_predictor name is in the saved state. Good for renamed-predictor cases.
- **BUT**: when the saved state has NO overlap with the current predictor names, the `for name, predictor in program.named_predictors(): if name in state:` loop never executes, `failed_predictors` stays empty, and the function returns a fresh default module **as if loaded successfully**. This is the exact silent-partial-load symptom Round 1 flagged, still reproducible.
- Live repro:
  ```
  $ python -c "from sio.core.dspy.persistence import load_compiled; \
      import tempfile, json, os; \
      p = tempfile.NamedTemporaryFile(suffix='.json', delete=False).name; \
      open(p,'w').write(json.dumps({'foo.predict': {...}})); \
      prog = load_compiled('suggestion_generator', p); \
      print('SILENT LOAD — predictors:', [n for n,_ in prog.named_predictors()])"
  ```
  Output: `SILENT LOAD — predictors: ['generate.predict']` — the artifact had `foo.predict`, but load_compiled returned a fresh module with the CURRENT predictor name and claimed success.

**Status**: PARTIAL — the common-case rename is now surfaced, but whole-schema mismatch is still silent. Tracked as N-R2D.4.

### H-R2.5 — `RecallEvaluator.forward` returns non-float on coercion → CLOSED
- `recall_trainer.py:76-91` now catches `TypeError, ValueError` and returns `dspy.Prediction(score=0.0, reasoning=f"unparseable score: {result.score!r}")`. Correct.

### H-R2.6 — divergent modules → see C-R2.6 above (**OPEN**, compounded by new findings)

### H-R2.7 — centroid cache keyed by description → **PARTIAL**
- `pattern_clusterer._load_stored_centroids` (lines 266-320) now returns a tuple: `(by_pattern_id, by_description)`. Primary lookup in `cluster_errors` is by `pattern_id` — correct.
- HOWEVER, the `by_desc` secondary map is still used at lines 452 and 468-469 as a text-exact-match SHORTCUT that skips re-encoding. The original collision risk remains: if two patterns have the same `description` (e.g. both clusters' first error is `"Tool timed out"`), the second pattern's centroid overwrites the first in `by_desc` at line 316, and any error sharing that text reuses the wrong pattern's centroid.
- **Impact**: the desc_cache shortcut is **correctness-hostile for hot-text patterns**. Safer fix: drop the shortcut entirely, or key it by `(description, pattern_id)` and require exact pattern_id match in the lookup.

**Status**: PARTIAL — primary lookup is correct, but the description-keyed shortcut reintroduces the Round 1 risk at a smaller surface. Downgraded severity from HIGH to MEDIUM.

---

## NEW findings (introduced or overlooked by fix-pack)

### N-R2D.1 — `optimize_suggestions` signature mismatch: `SuggestionGenerator.forward()` gets unexpected kwargs → **CRITICAL**

```json
{
  "title": "optimize_suggestions feeds 4-input corpus into 3-input SuggestionGenerator.forward — raises TypeError on every example",
  "severity": "Critical",
  "confidence": "High",
  "category": "Contract",
  "location": [
    {"file": "src/sio/core/dspy/optimizer.py", "line": 254, "symbol": "_evaluate_metric"},
    {"file": "src/sio/core/dspy/optimizer.py", "line": 329, "symbol": "optimize_suggestions"},
    {"file": "src/sio/suggestions/dspy_generator.py", "line": 917, "symbol": "SuggestionGenerator.forward"},
    {"file": "src/sio/ground_truth/corpus.py", "line": 34, "symbol": "load_training_corpus"}
  ],
  "evidence": {
    "type": "RuntimeTrace",
    "details": "load_training_corpus() builds dspy.Example objects with inputs {error_examples, error_type, pattern_summary}. optimize_suggestions uses SuggestionGenerator() (the 3-input PatternToRule-based class). SuggestionGenerator.forward() expects {pattern_description, example_errors, project_context}. Every call in _evaluate_metric fails with TypeError, which is swallowed by a bare except (line 264-266) and scored 0.0.",
    "repro_steps": [
      "python -c \"from sio.suggestions.dspy_generator import SuggestionGenerator; m = SuggestionGenerator(); m(error_examples='e', error_type='t', pattern_summary='p')\"",
      "Expected: successful forward pass",
      "Actual: TypeError: SuggestionGenerator.forward() got an unexpected keyword argument 'error_examples'"
    ]
  },
  "root_cause_hypothesis": "The fix for C-R2.6 swapped the MODULE class (SuggestionModule -> SuggestionGenerator) but left the trainset SOURCE unchanged (still load_training_corpus, which was shaped for the old 4-input SuggestionModule signature). Signatures diverged without a compile-time check.",
  "blast_radius": "Every sio optimize-suggestions invocation: (a) scores metric_before = 0.0, (b) passes the broken module to BootstrapFewShot/MIPROv2 which internally calls the same signature-incompatible forward, (c) either crashes inside compile or produces an artifact that was never actually optimized. The operator sees a 0.000 -> 0.000 metrics display and/or an OptimizationError. The entire FR-036/US1 closed-loop path is dead on the CLI `sio optimize-suggestions` entry point.",
  "fix_suggestion": [
    "Option A (recommended): Replace load_training_corpus with datasets.build_trainset_for('suggestion_generator'), which produces the correct 3-input examples.",
    "Option B: Add a SuggestionGenerator signature-adapter shim that accepts 4-input kwargs and reshapes to the PatternToRule input triple.",
    "Option C: Restore the 4-input SuggestionModule and revert optimize_suggestions to use it — but this re-opens C-R2.6.",
    "Add a compile-time/startup check: assert first example's .inputs() == module.forward signature keys."
  ],
  "next_actions": [
    "Add an integration test: seed gold_standards with 5+ rows -> run `sio optimize-suggestions` -> assert metric_before and metric_after are both floats AND at least one of them is > 0 when the LM is mocked to return non-empty rule_body.",
    "Grep-test: add a regression assertion that SuggestionGenerator.forward input kwargs are a SUPERSET of load_training_corpus output inputs."
  ],
  "links": ["FR-036", "FR-011", "contracts/dspy-module-api.md §3"]
}
```

### N-R2D.2 — `suggestion_quality_metric` reads fields that `SuggestionGenerator` doesn't emit → **HIGH**

```json
{
  "title": "suggestion_quality_metric expects pred.prevention_instructions + pred.target_surface but SuggestionGenerator returns rule_title/rule_body/rule_rationale — metric always returns ~0.3",
  "severity": "High",
  "confidence": "High",
  "category": "Contract",
  "location": [
    {"file": "src/sio/core/dspy/metrics.py", "line": 286, "symbol": "_score_specificity"},
    {"file": "src/sio/core/dspy/metrics.py", "line": 320, "symbol": "_score_actionability"},
    {"file": "src/sio/core/dspy/metrics.py", "line": 360, "symbol": "_score_surface_accuracy"},
    {"file": "src/sio/suggestions/dspy_generator.py", "line": 887, "symbol": "SuggestionGenerator"}
  ],
  "evidence": {
    "type": "RuntimeTrace",
    "details": "Live repro: calling suggestion_quality_metric(example_with_SG_inputs, pred_with_SG_outputs, trace=None) returns 0.3 because _score_specificity returns 0.0 (no prevention_instructions attribute), _score_actionability returns 0.0 (same), and _score_surface_accuracy returns 1.0 by accident (default claude_md_rule happens to match tool_failure's accepted set). Weighted total = 0.35*0 + 0.35*0 + 0.30*1 = 0.3.",
    "repro_steps": [
      "from types import SimpleNamespace",
      "example = SimpleNamespace(error_examples='[{\"tool_name\":\"Bash\",\"error_text\":\"foo bar baz\"}]', error_type='tool_failure')",
      "pred = SimpleNamespace(rule_title='R', rule_body='Y', rule_rationale='Z')  # SG output shape",
      "from sio.core.dspy.metrics import suggestion_quality_metric",
      "print(suggestion_quality_metric(example, pred, trace=None))  # -> 0.3"
    ]
  },
  "root_cause_hypothesis": "Same root cause as N-R2D.1: the fix swapped SuggestionModule (emits prevention_instructions + target_surface) for SuggestionGenerator (emits rule_title + rule_body + rule_rationale) but did not update the metric to read the new field names.",
  "blast_radius": "Metric-driven optimization (BootstrapFewShot with metric=suggestion_quality_metric, optimizer.py:411) has no signal: every candidate scores ~0.3 regardless of quality. MIPROv2 likewise sees flat reward. Optimizer runs to completion but produces statistically indistinguishable candidates. metric_before ~= metric_after ~= 0.3, and the operator is told 'optimization complete' when nothing optimized.",
  "fix_suggestion": [
    "Add a rule_body-based scorer: derive specificity from whether rule_body references tool_name/error_text keywords, actionability from action-verb + backticked token presence in rule_body, and rationale alignment as a new signal.",
    "Or: use METRIC_REGISTRY['embedding_similarity'] or ['llm_judge_recall'] as the metric_fn in optimize_suggestions — those already read rule_body via _get_text()."
  ],
  "next_actions": [
    "Add a unit test: feed suggestion_quality_metric a well-formed SG prediction and assert score > 0.5.",
    "Add a compile-time check in optimize_suggestions: first(corpus) with metric_fn should score > 0 on the gold example itself."
  ]
}
```

### N-R2D.3 — `module_store.save_module` retains the exact non-atomic UPDATE→INSERT pattern Round 1 flagged → **HIGH**

```json
{
  "title": "module_store.save_module and recall_trainer.save_trained_module still perform UPDATE-then-INSERT without BEGIN IMMEDIATE or SAVEPOINT",
  "severity": "High",
  "confidence": "High",
  "category": "Integration",
  "location": [
    {"file": "src/sio/core/dspy/module_store.py", "line": 48, "symbol": "save_module"},
    {"file": "src/sio/training/recall_trainer.py", "line": 450, "symbol": "save_trained_module"}
  ],
  "evidence": {
    "type": "Static",
    "details": "fix_log_round1.md claims H-R2.4 CLOSED by wrapping _record_optimization_run in SAVEPOINT activate_module. Verified true for that function. But optimize_suggestions (the CLI-facing entry point) calls save_module at optimizer.py:437, NOT _record_optimization_run. save_module has UPDATE optimized_modules SET is_active=0 ... followed by INSERT ... VALUES(..., 1, ...) with a single commit at line 67 — identical to the pre-fix pattern. Concurrent `sio optimize-suggestions` invocations (e.g. cron + manual) can produce two active rows.",
    "repro_steps": [
      "Open sqlite3 .schema optimized_modules — confirm no 'CREATE UNIQUE INDEX ... WHERE is_active=1' partial index",
      "grep -n 'CREATE UNIQUE INDEX' src/sio/core/db/ — only one hit in queries.py (different table)",
      "Start two `sio optimize-suggestions` invocations in parallel (tmux) → race window at ~1ms between UPDATE and INSERT"
    ]
  },
  "root_cause_hypothesis": "The fix-pack applied SAVEPOINT only to _record_optimization_run (the run_optimize entry point), missing save_module (the optimize_suggestions entry point). Same for recall_trainer.save_trained_module. The two functions are structurally identical but maintained separately.",
  "blast_radius": "Under parallel optimize invocations or crash between UPDATE and INSERT: (a) zero active rows if INSERT fails after UPDATE; (b) two active rows if concurrent INSERTs interleave. get_active_module then returns the wrong module via ORDER BY created_at DESC LIMIT 1 tiebreak.",
  "fix_suggestion": [
    "Wrap both save_module and save_trained_module in SAVEPOINT/RELEASE/ROLLBACK TO, matching _record_optimization_run.",
    "Add partial unique index: CREATE UNIQUE INDEX idx_active_module ON optimized_modules(module_type) WHERE is_active=1; SQLite will then enforce the invariant at the engine level.",
    "Consolidate the three save-module implementations into one shared helper."
  ],
  "next_actions": [
    "Add regression test: thread_pool.map(save_module, [dup_args]*5, workers=5) — assert final state has exactly 1 active row."
  ]
}
```

### N-R2D.4 — `load_compiled` still silently returns a fresh module when saved state has zero overlap with current predictors → **HIGH**

```json
{
  "title": "load_compiled returns a default-init module without error when saved state keys have no overlap with current predictor names",
  "severity": "High",
  "confidence": "High",
  "category": "I/O",
  "location": [
    {"file": "src/sio/core/dspy/persistence.py", "line": 207, "symbol": "load_compiled"}
  ],
  "evidence": {
    "type": "RuntimeTrace",
    "details": "ArtifactStructureMismatch is only raised when at least one predictor name is in state. If state={'foo.predict': ...} and the module has 'generate.predict', the for-loop body never executes, failed_predictors stays [], and the function returns a fresh module.",
    "repro_steps": [
      "p = tmpfile.json",
      "open(p,'w').write(json.dumps({'foo.predict': {'signature': {'instructions': 'I was a different module'}, 'demos': []}}))",
      "prog = load_compiled('suggestion_generator', p)  # returns without exception",
      "assert prog.generate.predict.signature.instructions != 'I was a different module'  # this passes — it's the default instructions",
      "Operator sees INFO 'Loaded compiled DSPy module from /tmp/x.json' but the optimization is effectively lost."
    ]
  },
  "root_cause_hypothesis": "The fix-pack checked 'at-least-one predictor failed' but missed 'zero predictors matched'. Empty failed_predictors is a valid success signal in the current code — but structurally it indicates the artifact is incompatible.",
  "blast_radius": "Any refactor that renames a predictor (e.g. self.generate → self.predict) invalidates all prior artifacts — but load_compiled claims success. Operators see optimized-module INFO logs while running an unoptimized baseline. Same regression class as the original H-R2.3.",
  "fix_suggestion": [
    "After the load_state loop, add: if not any(name in state for name,_ in program.named_predictors()): raise ArtifactStructureMismatch(...)",
    "OR: require that the union of state.keys() ⊆ predictor_names — if state has orphan keys, raise.",
    "Additionally: add a SHA of the program structure (sorted predictor names + signature class repr) into the saved JSON 'metadata' block; verify on load."
  ],
  "next_actions": [
    "Add failing regression test: save with foo.predict, load into SuggestionGenerator, assert ArtifactStructureMismatch raises."
  ]
}
```

### N-R2D.5 — `SuggestionGenerator.forward` loses `instrumentation_json` on hard-fail → **MEDIUM**

```json
{
  "title": "Instrumentation counters dropped when all retries exhaust (the case that needs them most)",
  "severity": "Medium",
  "confidence": "High",
  "category": "Logic",
  "location": [
    {"file": "src/sio/suggestions/dspy_generator.py", "line": 985, "symbol": "SuggestionGenerator.forward"},
    {"file": "src/sio/suggestions/dspy_generator.py", "line": 1001, "symbol": "SuggestionGenerator.forward"},
    {"file": "src/sio/suggestions/dspy_generator.py", "line": 1008, "symbol": "SuggestionGenerator.forward"}
  ],
  "evidence": {
    "type": "Static",
    "details": "The `raise Exception(last_format_error)` at line 985 and `raise Exception(last_phi_error)` at line 1001 happen BEFORE `pred.instrumentation_json = json.dumps(instrumentation)` at line 1008. Every hard-fail loses the backtrack_count and rejection_reasons that could have been persisted.",
    "repro_steps": [
      "Verified via source position inspection:",
      "idx_attach=3338, idx_raise_format=2462, idx_raise_phi=3121",
      "raise statements appear at lower char offsets than the attachment line, confirming raise paths exit without attaching instrumentation."
    ]
  },
  "root_cause_hypothesis": "The instrumentation attachment was added at the success path but not at the failure paths. The dict itself is held in a local variable that goes out of scope when the exception propagates. Caller (`generate_dspy_suggestion`) then re-raises RuntimeError with no access to the counters.",
  "blast_radius": "Telemetry for the 92%-rejection-rate M8 issue is now unrecoverable for the rejected cases — exactly the population operators need to tune format/PHI checks against. `suggestions.instrumentation_json` will only record successful runs. Declining-grade grader will falsely conclude 'assertion backtracks are rare' because hard-fails never write.",
  "fix_suggestion": [
    "Raise a custom exception class carrying the instrumentation dict: `raise SuggestionFormatError(last_format_error, instrumentation=instrumentation)`.",
    "Or: attach instrumentation to an output param / thread-local before raising.",
    "Caller catches and persists instrumentation regardless of success/failure."
  ],
  "next_actions": [
    "Unit test: pass a predictor that always returns an empty rule_body; assert that caller can access instrumentation_json with backtrack_count == _MAX_RETRIES + 1."
  ]
}
```

### N-R2D.6 — `instrumentation._InstrumentedModule` is a dead hook in DSPy 3.x → **MEDIUM**

```json
{
  "title": "instrument_module wrapper hardcodes backtrack_count=0 because hasattr(dspy, 'Assert') is always False in DSPy 3.x",
  "severity": "Medium",
  "confidence": "High",
  "category": "Logic",
  "location": [
    {"file": "src/sio/suggestions/instrumentation.py", "line": 95, "symbol": "_InstrumentedModule.__call__"}
  ],
  "evidence": {
    "type": "Static",
    "details": "Line 95: `if hasattr(_dspy, 'Assert'):` gates the mock.patch that intercepts assertions. In DSPy 3.1.3, hasattr(dspy, 'Assert') is False, so the code path at line 97 (patching) never runs; instead line 99 runs the module directly with no wrapping. `_counting_assert` is never called, `_assert_failures_this_call` stays 0, `self._backtrack_count` never increments.",
    "repro_steps": [
      ".venv/bin/python -c 'import dspy; print(hasattr(dspy, \"Assert\"))'  # False",
      "Any wrapped.instrumentation_json() call will always return {'backtrack_count': 0, 'forward_count': N, 'suggestion_id': ?}."
    ]
  },
  "root_cause_hypothesis": "This file was written for DSPy 2.x where dspy.Assert existed. The fix-pack migrated the assertions.py shim but left instrument_module as a no-op in DSPy 3.x. It remains a public API (imported from suggestions.instrumentation) but is silently neutered.",
  "blast_radius": "Downstream consumers of `suggestions.instrumentation_json.backtrack_count` (the declining-grade grader, the quality dashboard) see uniform zeros. Observability claim in the T108 docstring (`instrumentation.backtrack_count tracks dspy.Assert failures`) is false.",
  "fix_suggestion": [
    "Delete instrument_module + _InstrumentedModule entirely; rely on SuggestionGenerator.forward to emit instrumentation_json on the prediction object (which it does, per N-R2D.5's same code path).",
    "OR: rewrite as a dspy.Module subclass that counts `ValidationError` raises from assertions.assert_rule_format/assert_no_phi via a try/except wrapper."
  ],
  "next_actions": [
    "Grep call sites of instrument_module: if there are consumers, migrate them to read `pred.instrumentation_json` from SuggestionGenerator.forward."
  ]
}
```

### N-R2D.7 — `autoresearch_run_once.rejected_metric` counter still never increments → **LOW** (Round 1 M-R2.1 carried over; not on fix-pack list)

```json
{
  "title": "rejected_metric counter declared in autoresearch_run_once return dict but never incremented",
  "severity": "Low",
  "confidence": "High",
  "category": "Logic",
  "location": [
    {"file": "src/sio/autoresearch/scheduler.py", "line": 97, "symbol": "autoresearch_run_once"}
  ],
  "evidence": {
    "type": "Static",
    "details": "counts['rejected_metric'] initialized to 0 at line 97. Grep across src/sio/autoresearch/ for 'rejected_metric' shows only two hits: the dict init (line 97) and the docstring mention (line 89). No increment in the for loop at 115-130.",
    "repro_steps": [
      "grep -rn 'rejected_metric' src/sio/autoresearch/"
    ]
  },
  "root_cause_hypothesis": "The docstring says metric_score < auto_approve_above should yield rejected_metric, but the implementation at line 124-127 increments pending_approval instead. Round 1 flagged this as M-R2.1 but the fix-pack did not address it (not on the CRITICAL/HIGH list).",
  "blast_radius": "Metrics dashboards always show 0 rejected-by-metric, hiding whether auto_approve_above threshold is correctly calibrated. Operators see 'N pending' where some were rejected by metric score specifically.",
  "fix_suggestion": [
    "At line 124-127, split into three branches: metric_score >= auto_approve_above -> promoted; metric_score < auto_approve_above -> rejected_metric; no auto_approve_above set -> pending_approval.",
    "OR: update the docstring and rejected_metric doc comment to reflect the current behaviour (i.e. 'metric below threshold counts as pending, not rejected')."
  ],
  "next_actions": [
    "Add a test: seed a suggestion with arena_passed=1 + metric_score=0.1, call autoresearch_run_once(auto_approve_above=0.5), assert returned dict has rejected_metric >= 1."
  ]
}
```

### N-R2D.8 — Template-fallback principle violation at `generator.py:803-809` (the user's own flagged pattern) → **MEDIUM**

```json
{
  "title": "Silent template-fallback when DSPy fails — user's own 'validate reality' principle is violated",
  "severity": "Medium",
  "confidence": "High",
  "category": "Logic",
  "location": [
    {"file": "src/sio/suggestions/generator.py", "line": 803, "symbol": "generate_suggestions (except block)"}
  ],
  "evidence": {
    "type": "Static",
    "details": "On any Exception in the DSPy path, the code logs a warning and falls through to the template path (line 810-832). The resulting suggestion has _using_dspy=False so it is technically DETECTABLE, but there is no counter, no metric, no alarm. A broken DSPy install silently degrades to deterministic templates without halting the pipeline.",
    "repro_steps": [
      "Break DSPy (e.g., revoke API key); run `sio suggest`; observe 'DSPy generation failed ... falling back to template' in logs; every suggestion emerges with _using_dspy=False.",
      "Nothing in the CLI output, exit code, or metrics surface signals that the expensive optimized path was not used."
    ]
  },
  "root_cause_hypothesis": "Historical tolerance for missing LLM backends (useful when running offline tests) has been kept even when LLM is configured and then fails mid-run. The failure mode 'LLM was configured but 100% of calls failed' is indistinguishable from 'LLM was never configured' at the suggestion level.",
  "blast_radius": "Operator cannot tell from cli output that DSPy failed for EVERY pattern. Metrics show suggestions delivered, dashboards show green, but quality has silently degraded to deterministic string templates. Matches the exact anti-pattern called out in `~/.claude/projects/.../memory/feedback_tests_validate_reality.md`.",
  "fix_suggestion": [
    "Count DSPy failures: sis += 1 in the except block; if sis == len(patterns) AND config has an LM configured, escalate to WARNING or exit code != 0.",
    "OR: respect a new SIOConfig.strict_dspy flag — when True, propagate the exception instead of falling through to template.",
    "Emit a structured metric: suggestions_generator.dspy_success_rate — the declining-grade grader can then detect full-degradation."
  ],
  "next_actions": [
    "Add logging.error (not warning) on template fallback; rely on existing log-aggregation to alert.",
    "Surface a suggestion-level field _dspy_fallback_reason so downstream audit can count hard-fails vs configured-off-fails."
  ]
}
```

### N-R2D.9 — Redundant `except (ImportError, Exception)` blocks mask ImportError diagnostics → **LOW** (Round 1 L-R2.4/L-R2.5 carried over)

```json
{
  "title": "except (ImportError, Exception) is equivalent to except Exception — fastembed fallback silently hides non-import errors",
  "severity": "Low",
  "confidence": "High",
  "category": "Logic",
  "location": [
    {"file": "src/sio/core/dspy/metrics.py", "line": 114, "symbol": "embedding_similarity"},
    {"file": "src/sio/core/applier/merger.py", "line": 98, "symbol": "_fastembed_similarity"},
    {"file": "src/sio/core/dspy/skill_module.py", "line": 206, "symbol": "(unknown function at that line)"}
  ],
  "evidence": {
    "type": "Static",
    "details": "grep finds three sites with `except (ImportError, Exception)` or `except (ImportError, AttributeError, Exception)`. Python's exception hierarchy makes all narrow classes redundant with `Exception`. Any non-import failure (e.g., fastembed model file corrupt, OSError reading ONNX weights) degrades to token-overlap without logging.",
    "repro_steps": [
      "Live repro: `sys.modules['fastembed'] = None` causes embedding_similarity to return 1.0 for identical strings via _text_overlap fallback — no WARNING log.",
      "Caller cannot tell whether it received a true fastembed cosine or a token-overlap Jaccard."
    ]
  },
  "root_cause_hypothesis": "Defensive programming pattern inherited from earlier phases. Narrow except clauses intended to be 'only import failures' but Exception inclusion makes the narrow entries cosmetic.",
  "blast_radius": "Low — metric quality degrades silently; operator cannot debug why embedding scores look weird.",
  "fix_suggestion": [
    "Narrow to `except ImportError:` only; on other exceptions, logger.warning('fastembed available but failed: %s — falling back to token overlap', exc) before returning the fallback.",
    "Add a `source` field to the return (e.g., named tuple (score, source='embedding'|'overlap')), or log at INFO on first fallback per process."
  ],
  "next_actions": [
    "Narrow the except clauses; re-run the test suite; add an `embedding_similarity_fallback_total` counter to metrics."
  ]
}
```

### N-R2D.10 — `_score_specificity` neutral-0.5 floor still present → **LOW** (Round 1 H9 partial)

```json
{
  "title": "_score_specificity returns 0.5 when no details can be extracted — biases quality upward for empty examples",
  "severity": "Low",
  "confidence": "High",
  "category": "Logic",
  "location": [
    {"file": "src/sio/core/dspy/metrics.py", "line": 306, "symbol": "_score_specificity"}
  ],
  "evidence": {
    "type": "Static",
    "details": "When _extract_details_from_examples returns an empty set (malformed JSON, empty examples list, examples without tool_name/error_text), the function returns 0.5 regardless of pred quality. Combined with N-R2D.2 (metric reading wrong fields), a suggestion referencing nothing specific scores 0.35*0.5 + 0.35*0 + 0.30*1 = 0.475 — above the 0.5 auto-approval threshold when the LM happens to pick claude_md_rule.",
    "repro_steps": [
      "Pass example with error_examples='[]' or invalid JSON — score = 0.5 regardless of pred."
    ]
  },
  "root_cause_hypothesis": "0.5 was chosen as 'neutral' for the no-details case to avoid penalizing honest examples without tool-metadata. Preferable: return 0.0 (cannot verify) so actionability+surface do the work.",
  "blast_radius": "Low — but combined with N-R2D.2 the metric has almost no discriminating power; 0.5 floor biases everything toward 'pass' when details are missing.",
  "fix_suggestion": [
    "Return 0.0 when no details available — let actionability subsignal carry the load.",
    "OR: require BOTH non-empty details AND non-empty instructions to score > 0 — skip specificity as a hard signal when details absent."
  ],
  "next_actions": [
    "Change the neutral floor to 0.0 and re-measure fix-pack test pass rate."
  ]
}
```

### N-R2D.11 — `gold_standards.promote_to_gold` stuffs `str(correct_outcome)` into `expected_outcome` text field → **LOW** (Round 1 M-R2.6 carried over)

```json
{
  "title": "expected_outcome field receives '1'/'0' boolean string instead of outcome description",
  "severity": "Low",
  "confidence": "High",
  "category": "Contract",
  "location": [
    {"file": "src/sio/core/arena/gold_standards.py", "line": 124, "symbol": "promote_to_gold"},
    {"file": "src/sio/core/arena/gold_standards.py", "line": 143, "symbol": "promote_to_gold (fallback)"}
  ],
  "evidence": {
    "type": "Static",
    "details": "Both the rich INSERT (line 112-129) and the fallback INSERT (line 132-146) pass `str(row['correct_outcome'])` as the expected_outcome column value. correct_outcome is a boolean integer (0/1), so expected_outcome becomes the string '1' or '0' instead of an outcome description. Since expected_outcome is used as an LLM training target in downstream GEPA/MIPRO runs (see datasets.py:128), the trainset target is corrupted.",
    "repro_steps": [
      "Check gold_standards.expected_outcome values after promote_to_gold: 'SELECT DISTINCT expected_outcome FROM gold_standards' returns ['0','1'] rather than descriptions."
    ]
  },
  "root_cause_hypothesis": "Schema confusion: expected_outcome was renamed/repurposed without updating promote_to_gold. Round 1 flagged this as M-R2.6; fix-pack did not address it (not on blocking list).",
  "blast_radius": "Trainset quality — GEPA's reflection LM receives nonsensical target strings, which biases rule generation toward empty or single-token outputs.",
  "fix_suggestion": [
    "Pass row.get('expected_outcome_text') or a human-readable derivative ('success'/'failure') instead of stringified boolean.",
    "Better: add a new column expected_outcome_description and migrate."
  ],
  "next_actions": [
    "SELECT COUNT(*) FROM gold_standards WHERE expected_outcome IN ('0','1') — quantify impact; add a migration that converts to 'success'/'failure'."
  ]
}
```

---

## Summary

| Category | Count |
|----------|-------|
| **Round 1 CRITICAL closed**: | 3 of 4 (C-R2.1 with caveat, C-R2.2, C-R2.3) |
| **Round 1 CRITICAL PARTIAL / OPEN**: | 1 (C-R2.6 — partial; replaced with new critical wiring bug) |
| **Round 1 HIGH closed**: | 3 of 7 (H-R2.1, H-R2.5, and H-R2.2/H-R2.4 partially for run_optimize path) |
| **Round 1 HIGH PARTIAL / OPEN**: | 4 (H-R2.2, H-R2.3, H-R2.6, H-R2.7 — all partial) |
| **NEW CRITICAL**: | 1 (N-R2D.1 — optimize_suggestions signature mismatch) |
| **NEW HIGH**: | 3 (N-R2D.2, N-R2D.3, N-R2D.4) |
| **NEW MEDIUM**: | 3 (N-R2D.5, N-R2D.6, N-R2D.8) |
| **NEW LOW**: | 4 (N-R2D.7, N-R2D.9, N-R2D.10, N-R2D.11 — carry-overs from Round 1 MEDIUM/LOW bucket) |

**Total remaining CRITICAL + HIGH: 5** (PRD §8 bar: 0)

Blocking items for §8 gate:

1. **N-R2D.1** (CRITICAL) — `sio optimize-suggestions` is completely broken on DSPy 3.x. Every corpus example raises TypeError inside `_evaluate_metric`, scored 0.0; optimizer compile likely also fails. The FR-036 closed-loop cannot run via the CLI entry point. This is a regression directly caused by the fix-pack's C-R2.6 fix.

2. **N-R2D.2** (HIGH) — metric cannot discriminate candidate quality because it reads field names the module no longer emits. Scores always fall in the 0.3±0.05 band; optimizer runs without signal.

3. **N-R2D.3** (HIGH) — non-atomic save on the CLI path (`module_store.save_module`). Race-window for parallel invocations.

4. **N-R2D.4** (HIGH) — silent partial-load when saved state has zero overlap with current predictors. Operators see "optimized module loaded" INFO while running the baseline.

5. **C-R2.6 still OPEN** — two class definitions (`SuggestionModule`, `SuggestionGenerator`) coexist; unclear which is canonical. The fix-pack chose `SuggestionGenerator` but failed to rewire the metric or corpus. Recommend committing to one and deleting the other.

**Regression risk rating**: the fix-pack closed 3/4 literal CRITICAL issues but introduced a NEW critical by incomplete wiring of the C-R2.6 swap. The net CRITICAL count moves from 4 → 2 (C-R2.6 still open + N-R2D.1 new). HIGH count changes from 7 → 7 (same numeric total, different composition — mostly carry-overs plus three new).

**Trust calibration**: the fix-pack did meaningful work on schema DDL (C-R2.3) and on the single-module assertions rewrite (C-R2.1). The weak zone is cross-file wiring: changing the module class without updating the corpus builder and the metric function was a textbook contract-drift bug. Recommended remediation order:

1. Fix N-R2D.1 + N-R2D.2 together — they share root cause (corpus/metric/module shape mismatch). Pick a canonical contract (the 3-input PatternToRule contract), delete `SuggestionModule`, align `load_training_corpus` to emit `pattern_description/example_errors/project_context`, and update `suggestion_quality_metric` to read `rule_body` via `_get_text()`.
2. Fix N-R2D.3 — wrap `save_module` and `save_trained_module` in SAVEPOINT matching `_record_optimization_run`. Add partial unique index as a belt-and-suspenders guarantee.
3. Fix N-R2D.4 — require state-keys ⊆ predictor-names after load, else raise. Consider adding a predictor-structure SHA to the saved artifact.
4. Fix N-R2D.5 — raise a custom exception with instrumentation, or persist before raise.
5. Address medium/low carryovers in a followup wave.

After these five items, re-run the acceptance suite and expect the previously-masked regressions to surface (esp. any test that shelled out to `sio optimize-suggestions` with a real corpus — that would have failed if it existed; the fact that it passed suggests no end-to-end integration test exists on this path).
