# DSPy 3.1.3 Reference — Pipeline Integrity & Training Data Remediation

**Source**: Context7 `/stanfordnlp/dspy/3.1.3` (official repo, versioned, 881 snippets)
**Fetched**: 2026-04-20
**Purpose**: Canonical DSPy reference for `/speckit.plan` and downstream implementation. Cite this file in plans instead of re-fetching context7.

---

## 1. Core Programming Primitives

### 1.1 Signatures (class-based is the 3.x standard)

```python
class BasicQA(dspy.Signature):
    """Answer questions with short factoid answers."""
    question: str = dspy.InputField()
    answer: str = dspy.OutputField(desc="often between 1 and 5 words")
```

- Docstring is used as the task instruction by DSPy prompt compilation.
- `InputField` / `OutputField` take `desc=`, `prefix=`, `format=`.
- Type hints are first-class in 3.x (drives adapter behavior).

### 1.2 Modules and Predictors

- `dspy.Predict(Signature)` — basic single-turn predictor.
- `dspy.ChainOfThought(Signature)` — auto-adds a `reasoning` field.
- `dspy.ReAct(signature, tools=[...], max_iters=10)` — tool-using agent.
- `dspy.ProgramOfThought(Signature)` — code-generating reasoning.
- Custom `dspy.Module` subclass with `forward()` for composition.

### 1.3 LM Configuration

```python
lm = dspy.LM("openai/gpt-4o-mini", api_key=..., cache=False, temperature=1.0, max_tokens=20000)
dspy.configure(lm=lm)
# OR temporary override:
with dspy.context(lm=other_lm): ...
```

- `cache=False` is explicit at LM-level; disables disk cache for that LM.
- Model names use litellm format (`openai/…`, `anthropic/…`, `ollama/…`).

### 1.4 Adapters (NEW surface in 3.x)

```python
chat_native = dspy.ChatAdapter(use_native_function_calling=True)
json_manual = dspy.JSONAdapter(use_native_function_calling=False)
dspy.configure(lm=..., adapter=chat_native)
```

- `ChatAdapter` = default, message-style.
- `JSONAdapter` = structured JSON outputs.
- `use_native_function_calling` toggles provider tool-calling vs DSPy-managed.

---

## 2. Tool Use

### 2.1 Managed via ReAct

```python
react_agent = dspy.ReAct(
    signature="question -> answer",
    tools=[tool1, tool2, tool3],
    max_iters=10,
)
```

### 2.2 Manual tool-call loop (full control)

```python
class ToolSignature(dspy.Signature):
    question: str = dspy.InputField()
    tools: list[dspy.Tool] = dspy.InputField()
    outputs: dspy.ToolCalls = dspy.OutputField()

tools = {"weather": dspy.Tool(weather), "calculator": dspy.Tool(calculator)}
predictor = dspy.Predict(ToolSignature)
response = predictor(question="...", tools=list(tools.values()))

for call in response.outputs.tool_calls:
    result = call.execute()  # requires dspy >= 3.0.4b2
    # fallback pre-3.0.4b2: result = tools[call.name](**call.args)
```

---

## 3. Streaming

```python
predict = dspy.Predict("question->answer")
stream_predict = dspy.streamify(
    predict,
    stream_listeners=[dspy.streaming.StreamListener(signature_field_name="answer")],
)
# For duplicate field names across predictors, pass predict= and predict_name=.
```

- Out of scope for this PRD (no UI changes) but the hook writer path could use `streamify` for long-running optimization progress reports.

---

## 4. Data — `dspy.Example`

```python
# Constructing from a dict
ex = dspy.Example(question="What is 2+2?", answer="4").with_inputs("question")

# Converting a built-in dataset
trainset = [x.with_inputs("question") for x in dataset.train]
devset   = [x.with_inputs("question") for x in dataset.dev]
```

**Rule:** `with_inputs(...)` is MANDATORY to mark which fields are inputs. Remaining fields are labels.

---

## 5. Metrics

A metric is a plain function:

```python
def gsm8k_metric(gold: dspy.Example, pred: dspy.Prediction, trace=None) -> bool | float:
    return gold.answer == pred.answer
```

- Return `bool`, `float`, or `int`.
- `trace` is only passed during optimization (used by some teleprompters).
- For DSPy optimizers, a higher score = better.

### Common metric patterns

- **Exact match** (routing/flow): `return gold.label == pred.label`
- **Substring / contains** (QA): `return gold.answer.lower() in pred.answer.lower()`
- **Embedding similarity** (semantic): use a sentence transformer, threshold on cosine.
- **LLM-as-judge**: define a second `dspy.Predict` signature whose output is a score; call it inside the metric.

### `dspy.Evaluate` — running a metric against a devset

```python
evaluator = dspy.Evaluate(
    devset=testset,
    metric=my_metric,
    display_progress=True,
    num_threads=10,
    display_table=True,
)
score = evaluator(my_program)
```

---

## 6. Optimizers (Teleprompters)

### 6.1 BootstrapFewShot (the workhorse)

```python
optimizer = dspy.BootstrapFewShot(
    metric=gsm8k_metric,
    max_bootstrapped_demos=4,
    max_labeled_demos=4,
    max_rounds=5,
)
compiled = optimizer.compile(dspy_program, trainset=trainset)
```

### 6.2 BootstrapFewShotWithRandomSearch

Same as above with a random search layer over candidate demo sets.

### 6.3 MIPROv2 (instruction + demo joint optimization)

```python
from dspy.teleprompt import MIPROv2

teleprompter = MIPROv2(
    metric=gsm8k_metric,
    auto="medium",  # "light" | "medium" | "heavy"
)
optimized = teleprompter.compile(
    dspy.ChainOfThought("question -> answer"),
    trainset=trainset,
)
optimized.save("optimized.json")
```

### 6.4 GEPA (NEW in 3.x — reflection-based optimization)

```python
gepa = dspy.GEPA(
    max_full_evals=2,
    metric=metric_for_example,
    num_threads=8,
    reflection_minibatch_size=3,
    track_stats=True,
    reflection_lm=dspy.LM("openai/gpt-5", temperature=1.0, max_tokens=32000),
)
optimized = gepa.compile(program, trainset=train_examples, valset=val_examples)
optimized.save(path)
```

- **Separate `reflection_lm`** (typically larger/stronger) critiques candidate prompts.
- Takes `trainset` AND `valset`.
- `track_stats=True` captures per-candidate scores.

### 6.5 LabeledFewShot / KNNFewShot / COPRO / BetterTogether / BootstrapFinetune

```python
# KNNFewShot with sentence embeddings
from dspy.teleprompt import KNNFewShot
from dspy import Embedder, ChainOfThought
from sentence_transformers import SentenceTransformer

knn = KNNFewShot(k=3, trainset=trainset, vectorizer=Embedder(SentenceTransformer("all-MiniLM-L6-v2").encode))
compiled = knn.compile(student=ChainOfThought("question -> answer"))
```

```python
# BootstrapFinetune (fine-tune the underlying LM)
optimizer = dspy.BootstrapFinetune(metric=lambda x, y, t=None: x.label == y.label, num_threads=24)
optimized = optimizer.compile(classify, trainset=trainset)
```

---

## 7. Save / Load

```python
# Save
optimized.save("path/to/prog.json")

# Load (into a matching-structure program)
loaded = dspy.ChainOfThought("question -> answer")
loaded.load("path/to/prog.json")
```

- JSON format contains demos, instructions, optimizer metadata.
- You MUST reconstruct the program structure before calling `.load()`.

---

## 8. Assertions — `dspy.Assert` and `dspy.Suggest`

- Runtime constraints inside `forward()`.
- On failure, DSPy backtracks and re-runs with the failure message fed back to the LM.
- Use for: format validation, factual-consistency gates, tool-output validation.

(DSPy 3.x is moving some assertion logic into adapters; exact API depends on minor version — reconfirm at implementation time.)

---

## 9. Parallel / History / Cache

- `dspy.Parallel` — batch-execute independent predictors.
- `dspy.History` — carries multi-turn conversation into the signature.
- `dspy.configure(cache=...)` — global cache toggle; LM-level `cache=False` overrides.
- Default cache is disk-backed (LiteLLM-compatible).

---

## 10. How this maps to SIO spec FRs

| Spec FR | DSPy 3.x tool |
|---|---|
| FR-005 (gold-standard promotion) | Insert `dspy.Example(...).with_inputs(...)` into trainset; persist examples, re-load on optimize |
| FR-008 / FR-014 (optimizer) | `BootstrapFewShot` for baseline; `MIPROv2 auto="medium"` for instruction+demo; `GEPA` if reflection-based quality is desired |
| FR-018 (replace trivial recall_metric) | Embedding-similarity metric function OR exact-match (per task) OR LLM-as-judge; scored via `dspy.Evaluate` |
| FR-029 (instrument suggestion generator) | Wrap generator with `dspy.Evaluate` for per-stage scores; consider `dspy.Assert` for format guardrails |
| Optimization persistence | `optimized.save(...)` → store path in `optimized_modules` table; `.load(...)` on next invocation |
| Adapter choice | `ChatAdapter(use_native_function_calling=True)` for providers that support it; `JSONAdapter` for structured outputs when strict schema required |

---

## 11. Findings that MAY affect spec scope — see addendum note

See companion file: `research/dspy-scope-impact-findings.md` (to be written only if the team decides to absorb any of these into the current PRD; otherwise they are out-of-scope follow-ons).

**Short answer:** Nothing in DSPy 3.1.3 invalidates the spec's user stories or success criteria. GEPA, Assert/Suggest, and native function-calling adapters are **implementation-choice** expansions, not spec redefinitions.
