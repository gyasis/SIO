# Research: DSPy Suggestion Engine

**Feature**: 003-dspy-suggestion-engine
**Date**: 2026-02-26

## R1: DSPy 3.1.3 API Patterns (Verified)

**Decision**: Use DSPy 3.1.3+ Signature/Module/Optimizer/RLM APIs as documented in `specs/001-self-improving-organism/research.md`.

**Verified API Surface**:

### Signature Definition
```python
import dspy

class SuggestionGenerator(dspy.Signature):
    """Generate a targeted improvement from error patterns."""
    error_examples: str = dspy.InputField(desc="JSON array of error examples with error_text, tool_name, user_message")
    error_type: str = dspy.InputField(desc="Error category: tool_failure, user_correction, agent_admission, repeated_attempt, undo")
    pattern_summary: str = dspy.InputField(desc="Description of the recurring pattern")

    target_surface: str = dspy.OutputField(desc="Target: claude_md_rule, skill_update, hook_config, mcp_config, settings_config, agent_profile, project_config")
    rule_title: str = dspy.OutputField(desc="Concise title for the improvement")
    prevention_instructions: str = dspy.OutputField(desc="Specific, actionable prevention/improvement text")
    rationale: str = dspy.OutputField(desc="Why this improvement addresses the error pattern")
```

### Module (ChainOfThought)
```python
class SuggestionModule(dspy.Module):
    def __init__(self):
        self.generate = dspy.ChainOfThought(SuggestionGenerator)

    def forward(self, error_examples, error_type, pattern_summary):
        return self.generate(
            error_examples=error_examples,
            error_type=error_type,
            pattern_summary=pattern_summary,
        )
```

### Metric Function
```python
def suggestion_quality_metric(example, pred, trace=None):
    """Score 0-1 based on specificity, actionability, surface accuracy."""
    score = 0.0
    # Specificity: references actual error content
    # Actionability: contains concrete steps
    # Surface accuracy: correct target_surface for error type
    # Return float 0.0-1.0
    return score
```

### Optimizers
```python
# BootstrapFewShot (10+ examples)
optimizer = dspy.BootstrapFewShot(
    metric=suggestion_quality_metric,
    max_bootstrapped_demos=4,
    max_labeled_demos=16,
    max_rounds=1,
    max_errors=10,
)
optimized = optimizer.compile(SuggestionModule(), trainset=ground_truth_examples)
optimized.save("~/.sio/optimized/suggestion_module.json")

# MIPROv2 (50+ examples)
optimizer = dspy.MIPROv2(
    metric=suggestion_quality_metric,
    auto="medium",
)
optimized = optimizer.compile(SuggestionModule(), trainset=ground_truth_examples,
    max_bootstrapped_demos=4, max_labeled_demos=4)

# Load saved module
loaded = SuggestionModule()
loaded.load("~/.sio/optimized/suggestion_module.json")
```

### RLM (Corpus Mining)
```python
rlm = dspy.RLM(
    signature="conversation_corpus, failure_record -> failure_analysis",
    max_iterations=20,
    max_llm_calls=50,
    max_output_chars=10_000,
    sub_lm=dspy.LM("azure/gpt-4o-mini"),  # cheap model for extraction
    verbose=True,
)
result = rlm(conversation_corpus=corpus_content, failure_record=failure_dict)
# result.failure_analysis = text analysis
# result.trajectory = list of {code, output} steps
```

**Rationale**: All APIs confirmed available in DSPy 3.1.3 via live import testing. Azure OpenAI with DeepSeek-R1-0528 confirmed working with `dspy.LM('azure/DeepSeek-R1-0528')`.

**Alternatives Considered**:
- Raw LLM calls without DSPy → rejected: loses optimization loop, no metric-driven improvement
- LangChain → rejected: too heavy, no built-in optimization primitives
- Manual prompt engineering → rejected: static prompts don't improve over time

---

## R2: LM Factory Configuration Design

**Decision**: Extend existing `SIOConfig` dataclass with `[llm]` TOML section. Factory function creates `dspy.LM` from config.

**Config Format** (`~/.sio/config.toml`):
```toml
[llm]
model = "azure/DeepSeek-R1-0528"
api_key_env = "AZURE_OPENAI_API_KEY"  # env var name, not the key itself
api_base_env = "AZURE_OPENAI_ENDPOINT"
temperature = 0.7
max_tokens = 2000

[llm.sub]  # Cheap model for RLM sub_lm and metric evaluation
model = "azure/gpt-4o-mini"
```

**Auto-Detection Priority** (when no config):
1. `AZURE_OPENAI_API_KEY` → `azure/DeepSeek-R1-0528`
2. `ANTHROPIC_API_KEY` → `anthropic/claude-sonnet-4-20250514`
3. `OPENAI_API_KEY` → `openai/gpt-4o`
4. None → template fallback mode

**Rationale**: DSPy's `dspy.LM()` accepts provider-prefixed model names (`azure/`, `anthropic/`, `openai/`). Config stores env var names rather than raw keys for security. Sub-LM for cheap operations (RLM, metric eval) keeps costs low.

**Alternatives Considered**:
- Separate config file for LLM → rejected: users already have config.toml, adding another file is confusing
- Hardcode Azure → rejected: users have different providers
- Store API keys in config → rejected: security risk; env vars are standard practice

---

## R3: Ground Truth Storage Format

**Decision**: Store ground truth in SQLite `ground_truth` table + JSON files at `~/.sio/ground_truth/`.

**Why Both**:
- SQLite for metadata, labels, and fast queries (which examples are approved? how many per surface?)
- JSON files for the full content (error pattern + ideal output) — compatible with `dspy.Example` loading
- Same pattern as existing dataset builder (metadata in DB, content in JSON)

**dspy.Example Compatibility**:
```python
# Each ground truth entry becomes:
example = dspy.Example(
    error_examples=json.dumps(error_list),
    error_type="tool_failure",
    pattern_summary="Bash permission denied on /etc/*",
    target_surface="claude_md_rule",
    rule_title="Check file permissions before editing system files",
    prevention_instructions="Before using Bash to edit files in /etc/, verify...",
    rationale="Users repeatedly encounter permission denied when...",
).with_inputs("error_examples", "error_type", "pattern_summary")
```

**Rationale**: DSPy optimizers require `dspy.Example` objects with `.with_inputs()` to separate input/output fields. Ground truth must be loadable as a trainset directly.

**Alternatives Considered**:
- JSON-only storage → rejected: no fast querying for label distribution, surface coverage stats
- SQLite-only (storing full text in DB) → rejected: large text blobs in SQLite are inefficient; JSON files are easier to inspect/edit
- CSV → rejected: multi-line content doesn't serialize well

---

## R4: Existing Code That Needs Real Implementation

**Decision**: Replace 3 stub functions with real DSPy code. Enhance 1 existing module.

| File | Function | Current State | Target State |
|------|----------|---------------|--------------|
| `core/dspy/optimizer.py` | `_run_dspy_optimization()` | Counts failures, returns markdown | Real DSPy compile with BootstrapFewShot/MIPROv2 |
| `core/dspy/rlm_miner.py` | `mine_failure_context()` | Returns hardcoded trajectory | Real `dspy.RLM()` call with corpus |
| `core/dspy/corpus_indexer.py` | `search_embedding()` | Falls back to keyword (line 57: `return self.search_keyword(query, top_k)`) | Real fastembed vector search using existing `sio.core.embeddings.provider` |
| `suggestions/generator.py` | `generate_suggestions()` | String template builders | Delegates to DSPy module when LLM available |

**Rationale**: These are all Principle XI violations — functions that pretend to do DSPy work but don't. The plan replaces each with real implementation while preserving the existing public API signatures.

---

## R5: Multi-Surface Target Routing

**Decision**: Surface routing is part of the DSPy Signature output, not a hardcoded heuristic. The ChainOfThought module reasons about which surface to target.

**Routing Signals** (training data teaches these patterns):
| Error Pattern | Expected Surface | Signal |
|---------------|-----------------|--------|
| MCP server timeout | `settings_config` or `mcp_config` | tool_name contains MCP server name |
| Skill budget exceeded | `skill_update` | error_text mentions budget/limit |
| Tool routing mismatch | `skill_update` or `agent_profile` | user corrects tool choice |
| Permission/access error | `claude_md_rule` | recurring system-level errors |
| Repeated undo | `claude_md_rule` or `project_config` | behavioral pattern |
| Hook cascade failure | `hook_config` | tool_name is a hook |
| Settings mismatch | `settings_config` | error references timeout/permission values |

**Rationale**: Hardcoded heuristics would miss novel patterns. DSPy's ChainOfThought reasons about the error context and learns from ground truth examples which surface is correct. The metric penalizes wrong-surface routing.

---

## R6: Pipeline Mode Design (Auto vs HITL)

**Decision**: Mode selection based on confidence score + surface impact. Both modes share the same DSPy module.

**Mode Selection Logic**:
```
if --auto flag: force automated mode
elif --analyze flag: force HITL mode
else:  # default: auto-select per pattern
    if confidence >= 0.8 AND surface in (claude_md_rule, project_config):
        automated mode
    else:
        HITL mode
```

**High-Impact Surfaces** (always default to HITL):
- `hook_config` — can break tool calls
- `mcp_config` — can break MCP servers
- `settings_config` — can change global behavior
- `agent_profile` — can change agent specialization

**Low-Impact Surfaces** (eligible for auto mode):
- `claude_md_rule` — additive behavioral rules
- `project_config` — project-scoped changes
- `skill_update` — skill-specific changes

**Rationale**: High-impact changes can break the entire agent; they need human review. Low-impact additions (like new CLAUDE.md rules) are safer to auto-approve. The confidence threshold (0.8) ensures strong evidence before automation.

---

## R7: Dependency Version Alignment

**Decision**: Update `pyproject.toml` to `dspy>=3.1.3` (from `>=2.5`).

**Rationale**: Research verified all APIs against DSPy 3.1.3. The `>=2.5` floor is too low — it would allow installing older versions that lack `dspy.RLM`, `GEPA`, and the current Signature syntax. Bump to `>=3.1.3` to match verified APIs.

**No new dependencies needed** — DSPy, Click, Rich, fastembed, numpy all already in pyproject.toml.
