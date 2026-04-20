# Contract — Optimizer Selection (FR-037, SC-017, SC-018)

**Branch**: `004-pipeline-integrity-remediation`
**Applies to**: `src/sio/core/dspy/optimizer.py`, `sio optimize` CLI

---

## 1. Selectable Optimizers

```python
# src/sio/core/dspy/optimizer.py

from __future__ import annotations
import dspy
from dspy.teleprompt import MIPROv2

OPTIMIZER_REGISTRY = {
    "gepa":      "build_gepa",
    "mipro":     "build_mipro",
    "bootstrap": "build_bootstrap",
}

def build_gepa(metric, *, reflection_lm=None, max_full_evals=2, reflection_minibatch_size=3, num_threads=8):
    return dspy.GEPA(
        max_full_evals=max_full_evals,
        metric=metric,
        num_threads=num_threads,
        reflection_minibatch_size=reflection_minibatch_size,
        track_stats=True,
        reflection_lm=reflection_lm,
    )

def build_mipro(metric, *, auto="medium", num_threads=8):
    return MIPROv2(metric=metric, auto=auto, num_threads=num_threads)

def build_bootstrap(metric, *, max_bootstrapped_demos=4, max_labeled_demos=4, max_rounds=5):
    return dspy.BootstrapFewShot(
        metric=metric,
        max_bootstrapped_demos=max_bootstrapped_demos,
        max_labeled_demos=max_labeled_demos,
        max_rounds=max_rounds,
    )
```

---

## 2. CLI Contract

```
sio optimize --module <name> [--optimizer gepa|mipro|bootstrap] [options]
```

| Flag | Applies to | Default | Notes |
|---|---|---|---|
| `--module` | all | *required* | `suggestion_generator` \| `recall_evaluator` |
| `--optimizer` | all | `gepa` | FR-037 default; operator overridable |
| `--metric` | all | module default from registry | Must exist in `METRIC_REGISTRY` |
| `--trainset-size` | all | 200 | < 20 raises `InsufficientData` |
| `--valset-size` | gepa, mipro | 50 | ignored for bootstrap |
| `--reflection-lm` | gepa | from `SIO_REFLECTION_LM` env | override only |
| `--max-full-evals` | gepa | 2 | |
| `--reflection-minibatch-size` | gepa | 3 | |
| `--auto` | mipro | `medium` | `light` \| `medium` \| `heavy` |
| `--max-bootstrapped-demos` | bootstrap | 4 | |
| `--max-labeled-demos` | bootstrap | 4 | |
| `--max-rounds` | bootstrap | 5 | |
| `--dry-run` | all | off | prints config, writes nothing |

---

## 3. Execution Flow

```python
# src/sio/core/dspy/optimizer.py (cont.)

from sio.core.dspy.lm_factory  import configure_default, get_reflection_lm
from sio.core.dspy.metrics     import METRIC_REGISTRY
from sio.core.dspy.datasets    import build_trainset_for
from sio.core.dspy.persistence import MODULE_REGISTRY, save_compiled
from sio.core.db.queries       import record_optimization_run, mark_prior_inactive
from pathlib import Path
import time

OPTIMIZED_ROOT = Path.home() / ".sio" / "optimized"

def run_optimize(
    module_name: str,
    optimizer_name: str = "gepa",
    metric_name: str | None = None,
    trainset_size: int = 200,
    valset_size: int = 50,
    **kwargs,
) -> dict:
    configure_default()
    cls = MODULE_REGISTRY[module_name]
    program = cls()

    metric_fn = METRIC_REGISTRY[metric_name or cls.DEFAULT_METRIC]
    trainset = build_trainset_for(module_name, limit=trainset_size)
    if len(trainset) < 20:
        raise InsufficientData(f"trainset has {len(trainset)} examples; need ≥ 20")

    if optimizer_name == "gepa":
        reflection_lm = kwargs.pop("reflection_lm", None) or get_reflection_lm()
        valset = build_trainset_for(module_name, limit=valset_size, offset=trainset_size)
        optimizer = build_gepa(metric_fn, reflection_lm=reflection_lm, **kwargs)
        compiled = optimizer.compile(program, trainset=trainset, valset=valset)
    elif optimizer_name == "mipro":
        valset = build_trainset_for(module_name, limit=valset_size, offset=trainset_size)
        optimizer = build_mipro(metric_fn, **kwargs)
        compiled = optimizer.compile(program, trainset=trainset, valset=valset)
    elif optimizer_name == "bootstrap":
        optimizer = build_bootstrap(metric_fn, **kwargs)
        compiled = optimizer.compile(program, trainset=trainset)
    else:
        raise UnknownOptimizer(optimizer_name)

    # Evaluate compiled program against held-out set for the recorded score
    eval_set = build_trainset_for(module_name, limit=50, offset=trainset_size + valset_size)
    evaluator = dspy.Evaluate(devset=eval_set, metric=metric_fn, display_progress=False)
    score = evaluator(compiled)

    # Persist artifact
    artifact_path = OPTIMIZED_ROOT / f"{module_name}__{optimizer_name}__{int(time.time())}.json"
    save_compiled(compiled, artifact_path)

    # Record run + transition prior active row
    mark_prior_inactive(module_name=module_name)
    record_optimization_run(
        module_name=module_name,
        optimizer_name=optimizer_name,
        metric_name=metric_name or cls.DEFAULT_METRIC,
        trainset_size=len(trainset),
        valset_size=valset_size if optimizer_name in ("gepa", "mipro") else None,
        score=score,
        task_lm=dspy.settings.lm.model,
        reflection_lm=(reflection_lm.model if optimizer_name == "gepa" else None),
        artifact_path=str(artifact_path),
    )

    return {"artifact": str(artifact_path), "score": score, "optimizer": optimizer_name}
```

---

## 4. Default Optimizer per Module (FR-037)

| Module | Default | Rationale |
|---|---|---|
| `suggestion_generator` | **gepa** | Complex multi-field output; benefits from reflection |
| `recall_evaluator` | **bootstrap** | Simple scoring task; fast teleprompter sufficient |
| `routing_decider` (future) | **mipro** | Short classification; MIPROv2's instruction+demo joint search ideal |
| `flow_predictor` (future) | **mipro** | Similar to routing |

Operator can override per invocation via `--optimizer <name>`.

---

## 5. Test Coverage

| Test | Asserts |
|---|---|
| `tests/unit/dspy/test_optimizer_registry.py` | All three optimizers in registry; CLI flag mapping correct |
| `tests/integration/test_dspy_idiomatic.py` | `sio optimize --module suggestion_generator` with each of `gepa`, `mipro`, `bootstrap` produces a loadable artifact on a tiny fixture trainset (SC-017) |
| `tests/integration/test_gepa_vs_baseline.py` | GEPA-optimized suggestion_generator scores strictly higher than unoptimized baseline on held-out devset (SC-018) |
| `tests/unit/dspy/test_insufficient_data.py` | Trainset < 20 raises `InsufficientData` with clear message |
| `tests/unit/dspy/test_artifact_registry.py` | `optimized_modules` row fields match CLI invocation; `active=1` transition correct |
