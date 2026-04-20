# Contract — DSPy Module API (FR-035, FR-036, FR-038, FR-040, FR-041)

**Branch**: `004-pipeline-integrity-remediation`
**Applies to**: `src/sio/core/dspy/*`, `src/sio/suggestions/dspy_generator.py`, `src/sio/training/recall_trainer.py`
**Reference**: `research/dspy-3.x-reference.md`

---

## 1. LM Factory (FR-041)

**Single-source LM creation.** All `dspy.LM(...)` construction happens inside `src/sio/core/dspy/lm_factory.py`. A grep of the rest of the codebase returns zero direct `dspy.LM(` calls (SC-022).

```python
# src/sio/core/dspy/lm_factory.py

from __future__ import annotations
import os
import dspy

def get_task_lm() -> dspy.LM:
    """LM used for normal module forward passes. Cheap, fast."""
    model = os.environ.get("SIO_TASK_LM", "openai/gpt-4o-mini")
    return dspy.LM(model, cache=True, temperature=0.0, max_tokens=4096)

def get_reflection_lm() -> dspy.LM:
    """Strong LM used by GEPA to critique prompt candidates. Expensive."""
    model = os.environ.get("SIO_REFLECTION_LM", "openai/gpt-5")
    return dspy.LM(model, cache=False, temperature=1.0, max_tokens=32000)

def get_adapter(lm: dspy.LM) -> dspy.Adapter:
    """Provider-aware adapter (FR-040, R-12)."""
    forced = os.environ.get("SIO_FORCE_ADAPTER")
    native = os.environ.get("SIO_FORCE_NATIVE_FC")
    if forced == "json":
        return dspy.JSONAdapter(use_native_function_calling=(native != "0"))
    if forced == "chat":
        return dspy.ChatAdapter(use_native_function_calling=(native != "0"))

    provider = lm.model.split("/", 1)[0]
    if provider in ("openai", "anthropic", "azure"):
        return dspy.ChatAdapter(use_native_function_calling=True)
    if provider == "ollama":
        return dspy.JSONAdapter(use_native_function_calling=False)
    return dspy.ChatAdapter(use_native_function_calling=False)

def configure_default() -> None:
    """Call at process start. Binds task LM + adapter to dspy globally."""
    lm = get_task_lm()
    dspy.configure(lm=lm, adapter=get_adapter(lm))
```

**Invariants**:
- No other file constructs `dspy.LM`. Enforced by an import-time assertion in `tests/unit/dspy/test_lm_factory.py` that greps `src/` for forbidden patterns.
- `SIO_TASK_LM` and `SIO_REFLECTION_LM` env vars are the only operator-facing overrides.
- `cache=True` for task LM is the default (reproducibility, cost). `cache=False` for reflection LM (GEPA's reflection must not be cached across runs).

---

## 2. Signature Conventions (FR-035)

Class-based signatures only. Each signature has:
- A docstring used by DSPy as the task instruction.
- Type-hinted `InputField` / `OutputField` with `desc=` text.
- No dict-based "string signatures" (e.g., `dspy.Predict("q -> a")`) in production paths — those are permitted only in ad-hoc scripts.

```python
# src/sio/core/dspy/signatures.py

from __future__ import annotations
import dspy

class PatternToRule(dspy.Signature):
    """Generate a concise CLAUDE.md rule that prevents the given error pattern.
    The rule must be actionable, file-path-safe, and ≤ 3 sentences."""

    pattern_description: str = dspy.InputField(desc="Human-readable cluster name")
    example_errors: list[str] = dspy.InputField(desc="3–5 representative error messages")
    project_context: str = dspy.InputField(desc="Short description of the project or platform")

    rule_title: str = dspy.OutputField(desc="Title of the generated rule")
    rule_body: str = dspy.OutputField(desc="Rule body in Markdown, ≤ 3 sentences")
    rule_rationale: str = dspy.OutputField(desc="Why this rule prevents the pattern")


class RuleRecallScore(dspy.Signature):
    """Given a gold-standard rule and a candidate rule, score how well the candidate
    captures the same preventive intent. Returns a float in [0, 1]."""

    gold_rule: str = dspy.InputField()
    candidate_rule: str = dspy.InputField()
    score: float = dspy.OutputField(desc="Recall score in [0, 1]")
    reasoning: str = dspy.OutputField(desc="Brief justification")
```

---

## 3. Module Conventions (FR-035)

```python
# src/sio/suggestions/dspy_generator.py

from __future__ import annotations
import dspy
from sio.core.dspy.signatures import PatternToRule
from sio.core.dspy.assertions import assert_rule_format, assert_no_phi
from sio.core.dspy.lm_factory import configure_default

class SuggestionGenerator(dspy.Module):
    """DSPy module that turns a clustered error pattern into a candidate rule.
    Optimizable via GEPA / MIPROv2 / BootstrapFewShot (FR-037)."""

    def __init__(self) -> None:
        super().__init__()
        self.generate = dspy.ChainOfThought(PatternToRule)

    def forward(
        self,
        pattern_description: str,
        example_errors: list[str],
        project_context: str,
    ) -> dspy.Prediction:
        pred = self.generate(
            pattern_description=pattern_description,
            example_errors=example_errors,
            project_context=project_context,
        )
        # Runtime format guardrail (FR-038)
        assert_rule_format(pred)
        assert_no_phi(pred)
        return pred
```

**Invariants**:
- Every reasoning module is a `dspy.Module` subclass with one or more `dspy.Predict` / `dspy.ChainOfThought` / `dspy.ReAct` predictors as attributes (so DSPy's teleprompters can discover and optimize them).
- `forward()` signature matches the `Signature` class fields.
- Asserts are placed AFTER the prediction, BEFORE return.

---

## 4. Training-Example Contract (FR-036)

```python
# src/sio/core/dspy/datasets.py

from __future__ import annotations
import dspy
from sio.core.db.queries import load_gold_standards

def build_trainset_suggestion_generator(limit: int = 500) -> list[dspy.Example]:
    rows = load_gold_standards(task_type="suggestion", limit=limit)
    return [
        dspy.Example(
            pattern_description=r.pattern_description,
            example_errors=r.example_errors,
            project_context=r.project_context,
            rule_title=r.gold_rule_title,
            rule_body=r.gold_rule_body,
            rule_rationale=r.gold_rule_rationale,
        ).with_inputs("pattern_description", "example_errors", "project_context")
        for r in rows
    ]
```

**Invariants**:
- `.with_inputs(...)` is MANDATORY on every example (enforced by `assert_examples_wrapped(examples)` helper called by all optimizer entry points).
- Raw dicts or tuples never reach a teleprompter (SC-020).

---

## 5. Metric Contract (FR-018)

```python
# src/sio/core/dspy/metrics.py

from __future__ import annotations
import dspy

METRIC_REGISTRY: dict[str, callable] = {}

def register(name: str):
    def decorator(fn):
        METRIC_REGISTRY[name] = fn
        return fn
    return decorator

@register("exact_match")
def exact_match(gold: dspy.Example, pred: dspy.Prediction, trace=None) -> bool:
    return getattr(gold, "label", None) == getattr(pred, "label", None)

@register("embedding_similarity")
def embedding_similarity(gold: dspy.Example, pred: dspy.Prediction, trace=None) -> float:
    # fastembed ONNX cosine; threshold configurable
    ...

@register("llm_judge_recall")
def llm_judge_recall(gold: dspy.Example, pred: dspy.Prediction, trace=None) -> float:
    # Uses an internal dspy.Module judge; returns [0, 1]
    ...
```

**Invariants**:
- Every metric function MUST match `(gold, pred, trace=None) -> bool | float | int`.
- Higher score = better (DSPy convention).
- Metric name is passed to `sio optimize --metric <name>` (falls back to module default).
- Registry enforces single source of truth (no scattered `def my_metric(...)` across modules).

---

## 6. Assertion Helpers (FR-038)

```python
# src/sio/core/dspy/assertions.py

import dspy

_PHI_TOKENS = ("SSN", "MRN", "patient_id", ...)  # non-exhaustive

def assert_rule_format(pred: dspy.Prediction) -> None:
    dspy.Assert(
        bool(pred.rule_title and pred.rule_body),
        "rule_title and rule_body must be non-empty",
    )
    dspy.Assert(
        len(pred.rule_body.split(".")) <= 4,
        "rule_body must be ≤ 3 sentences (observed: {n})".format(n=len(pred.rule_body.split("."))),
    )

def assert_no_phi(pred: dspy.Prediction) -> None:
    blob = f"{pred.rule_title}\n{pred.rule_body}\n{pred.rule_rationale}"
    for token in _PHI_TOKENS:
        dspy.Assert(token not in blob, f"rule body must not contain PHI token: {token}")
```

**Invariants**:
- All assertions use `dspy.Assert` (not Python `assert`) so DSPy handles backtracking (R-11).
- Every assertion message is actionable (tells the LM how to fix on retry).
- Backtrack counts are logged via the module's instrumentation to `suggestions` table (FR-029).

---

## 7. Save / Load Contract (FR-039)

```python
# src/sio/core/dspy/persistence.py

from pathlib import Path
from sio.suggestions.dspy_generator import SuggestionGenerator
from sio.training.recall_trainer import RecallEvaluator

MODULE_REGISTRY = {
    "suggestion_generator": SuggestionGenerator,
    "recall_evaluator":     RecallEvaluator,
}

def save_compiled(program: dspy.Module, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    program.save(str(path))

def load_compiled(module_name: str, path: Path) -> dspy.Module:
    cls = MODULE_REGISTRY[module_name]
    program = cls()
    program.load(str(path))
    return program
```

**Invariants**:
- `optimized_modules.artifact_path` stores the absolute path.
- Round-trip test (`tests/unit/dspy/test_save_load.py`) asserts that a compiled program produces identical output to its reloaded twin on a fixed input.
- JSON format: whatever DSPy 3.1.3 emits. SIO does not post-process the file.

---

## 8. Public API Summary

```python
from sio.core.dspy.lm_factory  import configure_default, get_task_lm, get_reflection_lm, get_adapter
from sio.core.dspy.signatures  import PatternToRule, RuleRecallScore
from sio.core.dspy.metrics     import METRIC_REGISTRY, register
from sio.core.dspy.assertions  import assert_rule_format, assert_no_phi
from sio.core.dspy.datasets    import build_trainset_suggestion_generator
from sio.core.dspy.persistence import save_compiled, load_compiled, MODULE_REGISTRY
```

Test coverage obligations: SC-016, SC-019, SC-020, SC-021, SC-022.
