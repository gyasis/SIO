# Research: SIO — Self-Improving Organism

**Date**: 2026-02-25
**Status**: Complete (Phase 0)

## 1. DSPy Optimizers

### Decision: DSPy (latest, currently 3.1.3) with GEPA, MIPROv2, BootstrapFewShot

**Rationale**: DSPy provides the three optimizers specified in the PRD.
GEPA reflects on execution traces and maintains the Pareto frontier
(best for skill/tool routing). MIPROv2 optimizes production-grade
instructions. BootstrapFewShot generates few-shot examples from labeled
data. RLM writes Python code to programmatically mine large conversation
corpora.

**Alternatives considered**:
- Manual prompt iteration — too slow, not data-driven
- LangChain/LangGraph — no built-in prompt optimization from labeled data
- Custom optimizer — unnecessary when DSPy provides exactly what's needed

**Key APIs (verified against DSPy 3.1.3 via context7)**:
```python
import dspy

# Configure LM
lm = dspy.LM("anthropic/claude-sonnet-4-20250514")
dspy.configure(lm=lm)

# Define signature for skill routing
class SkillRouter(dspy.Signature):
    """Route user intent to the correct skill."""
    user_message: str = dspy.InputField()
    skill_name: str = dspy.OutputField()

# BootstrapFewShot — generate few-shot demos from labeled data
from dspy.teleprompt import BootstrapFewShot
optimizer = dspy.BootstrapFewShot(
    metric=satisfaction_metric,
    max_bootstrapped_demos=4,
    max_labeled_demos=16,
    max_rounds=1,
    max_errors=10
)
optimized = optimizer.compile(router, trainset=labeled_examples)

# MIPROv2 — optimize instructions + few-shot examples
from dspy.teleprompt import MIPROv2
optimizer = dspy.MIPROv2(
    metric=satisfaction_metric,
    auto="medium"  # light | medium | heavy
)
optimized = optimizer.compile(
    router, trainset=labeled_examples,
    max_bootstrapped_demos=4, max_labeled_demos=4
)
optimized.save("optimized.json")

# GEPA — reflective Pareto-optimal multi-objective
from dspy import GEPA
optimizer = GEPA(
    metric=satisfaction_metric,
    auto="light",
    num_threads=32,
    track_stats=True,
    reflection_minibatch_size=3,
    reflection_lm=dspy.LM(model="anthropic/claude-sonnet-4-20250514",
                           temperature=1.0, max_tokens=32000)
)
optimized = optimizer.compile(
    router, trainset=labeled_examples, valset=val_set
)
```

**Dependencies**: `dspy` (latest, currently 3.1.3), requires
Python 3.11+. Pulls in litellm, pydantic. Does NOT require torch.

**Policy**: Always use the latest DSPy release. Do NOT pin to a
specific version — DSPy evolves rapidly and SIO must track it.

**RLM CONFIRMED** (verified via dspy.ai/api/modules/RLM/ docs):

`dspy.RLM` (Recursive Language Model) is a DSPy 3.x module that
treats large corpora as external symbolic environments explored
via a sandboxed Python REPL. This is exactly what SIO needs for
conversation corpus mining.

**Key architecture insight — Variable Space vs Token Space**:
RLM separates the _variable space_ (data stored in REPL variables)
from the _token space_ (LLM context window). The corpus is loaded
into REPL variables but never sent to the LLM. The LLM only sees
metadata (type, length) and writes code to query it. This means
SIO can process unlimited-size SpecStory corpora without hitting
context limits.

**How RLM works** (peek → search → extract → refine → SUBMIT):
1. Root LM receives metadata about the corpus — NOT the full text
2. Root LM writes Python code to search/filter/aggregate the data
3. Code runs in Deno + Pyodide WASM sandbox (isolated, safe)
4. Root LM can call `llm_query(prompt)` — routes to `sub_lm`
5. Root LM can call `llm_query_batched(prompts)` — batch sub-LM
6. Root LM calls `print()` to surface data from variable space
7. Root LM calls `SUBMIT(result)` with final synthesized output

**Built-in tools available inside the REPL**:
- `llm_query(prompt)` — single LLM call (uses `sub_lm` if set)
- `llm_query_batched(prompts)` — batch LLM calls
- `print()` — surface variable-space data into token space
- `SUBMIT(result)` — finalize and return structured output

**Configuration — NO `.rlm` method exists**:
There is no `dspy.configure(rlm=...)` or `.rlm` method. RLM uses:
- `dspy.configure(lm=main_lm)` — root LM that writes strategy code
- `sub_lm=` parameter per RLM instance — cheap model for
  `llm_query()` calls inside the REPL

**RLM API (DSPy 3.1.3)**:
```python
import dspy

rlm = dspy.RLM(
    signature="conversation_corpus, failure_record -> failure_analysis",
    max_iterations=20,       # Max REPL loops
    max_llm_calls=50,        # Max sub-LM queries
    max_output_chars=10_000, # Max REPL output chars
    sub_lm=dspy.LM("anthropic/claude-haiku-4-5"),  # Cheap sub-queries
    verbose=True
)

# RLM writes Python code to mine conversation history
# Only pulls relevant snippets into token space
# Outputs structured failure analysis for GEPA
result = rlm(
    conversation_corpus=specstory_content,
    failure_record=failure_context
)
print(result.failure_analysis)

# Inspect execution trace — full audit trail
for step in result.trajectory:
    print(f"Code:\n{step['code']}")
    print(f"Output:\n{step['output']}")
```

**Trajectory inspection**: `result.trajectory` returns the full
execution trace — every code block the root LM wrote and every
output the REPL produced. SIO should log this for debugging
optimization quality and auditing the miner's behavior.

**Requirements**: Deno must be installed for the WASM sandbox:
`curl -fsSL https://deno.land/install.sh | sh`
SIO's installer should check for Deno and warn if missing.

**Sub-LM Cost Optimization Strategy**:
The `sub_lm` handles all `llm_query()` calls inside the REPL — this
is the high-volume, cost-sensitive path. Options ranked by cost:

| Option | Model | Cost | Quality | Notes |
|--------|-------|------|---------|-------|
| **Budget Azure** | gpt-4o-mini, gpt-4.1-mini | ~$0.15/1M tokens | Good | Cheapest API option, sufficient for extraction |
| **Local Ollama** | llama3.3:70b, mistral, phi-3 | Free | Varies | Zero API cost; use n-pass voting (n=3-5) to match API quality |
| **Mid-tier Azure** | gpt-5-mini | ~$0.60/1M tokens | Better | Mid-range balance |
| **High-tier** | gpt-5.2, claude-haiku | ~$1-4/1M tokens | Best | Overkill for extraction work |

**N-pass voting for local models**: When using Ollama as `sub_lm`,
run each `llm_query()` call n times (3-5) and take the majority
answer. This compensates for lower per-call quality while keeping
costs at zero. DSPy's `temperature` and `n` parameters support
this natively:
```python
# Local Ollama sub-LM with n-pass voting
sub_lm = dspy.LM(
    "ollama_chat/llama3.3:70b",
    api_base="http://localhost:11434",
    temperature=0.7,
    n=3,  # Generate 3 completions, DSPy picks best
)
```

**Recommendation for SIO config**: Make `sub_lm` fully configurable
via `~/.sio/config.toml`. Default to the cheapest Azure model
available. Document Ollama as a free alternative with n-pass voting.
The root LM (strategy code) should remain high-quality since it
makes only 1-2 calls per mining run.

**RL-Based Optimization (Experimental)**:
DSPy 3.1.3 also includes experimental RL via `arbor-ai` library:
- `ArborGRPO` implements Multi-Module Group Relative Policy
  Optimization for weight fine-tuning
- Currently "extremely EXPERIMENTAL" per DSPy docs
- GEPA is recommended over RL for most cases (30-35x fewer
  rollouts, no weight fine-tuning needed)
- SIO V0.1 should use GEPA/MIPROv2; RL via Arbor is a V1.0+
  consideration when prompt optimization hits a ceiling

**The "BetterTogether" Loop** (future):
1. MIPROv2/GEPA optimize *prompts* (prompt optimization)
2. Arbor/MMGRPO fine-tunes *weights* (RL optimization)
This dual approach is the most advanced DSPy configuration.

## 2. Claude Code Hooks System

### Decision: PostToolUse (command type) as primary telemetry hook

**Rationale**: PostToolUse fires after every tool call, provides
tool_name, tool_input, tool_output, error, duration_ms, and
session_id. Zero interference with user's session. Shell command
handler has no token cost.

**Alternatives considered**:
- PreToolUse only — adds latency, risk of blocking
- Prompt/Agent hook types — too expensive for telemetry (token cost)
- Both Pre+Post — V0.2+ for active correction; V0.1 uses Post only

**Hook configuration** (settings.json):
```json
{
  "hooks": {
    "PostToolUse": [{
      "type": "command",
      "command": "python3 ~/.sio/claude-code/hooks/post_tool_use.py"
    }],
    "Stop": [{
      "type": "command",
      "command": "python3 ~/.sio/claude-code/hooks/session_end.py"
    }]
  }
}
```

**Key stdin JSON for PostToolUse**:
```json
{
  "hook": "PostToolUse",
  "session_id": "uuid",
  "tool_name": "Read",
  "tool_input": { "file_path": "..." },
  "tool_output": { "content": "..." },
  "error": null,
  "duration_ms": 150
}
```

**Skills format** (SKILL.md):
```yaml
---
name: "sio-feedback"
description: "Rate the AI's last action (++ or --)"
tools: ["Bash"]
---
```

**CLAUDE.md loading**: Global (`~/.claude/CLAUDE.md`) → project root →
project `.claude/` → subdirectories. All concatenated, later takes
precedence. SIO instructions go in global CLAUDE.md for always-active
telemetry.

**Gaps to verify during implementation**:
- Exact event count (reported ~14, may vary)
- Whether MCP tool calls fire the same hook events
- Default hook timeout value
- Hook error handling (crash → tool proceeds or fails?)

## 3. Embedding Model

### Decision: fastembed (ONNX-based) as default, configurable to API

**Rationale**: fastembed uses ONNX runtime (~200MB installed) vs
sentence-transformers requiring PyTorch (~2.3GB). Identical embedding
quality for all-MiniLM-L6-v2. SIO is a CLI tool — 2GB dependency
is not justified for infrequent embedding operations.

**Alternatives considered**:
- sentence-transformers (full torch) — 10x larger install, optional extra
- OpenAI embeddings API — requires network + API key, offered as override
- optimum[onnxruntime] — more control but more boilerplate than fastembed

**Model**: all-MiniLM-L6-v2
- Embedding dimension: 384
- Model size: ~80MB
- Speed: ~500 sentences/sec on CPU
- STS Benchmark: 0.849

**Architecture pattern**:
```python
# Abstract backend
class EmbeddingBackend(ABC):
    def encode(self, texts: list[str]) -> np.ndarray: ...
    def encode_single(self, text: str) -> np.ndarray: ...

# Implementations
class FastEmbedBackend(EmbeddingBackend): ...   # Default (ONNX runtime)
class ApiEmbedBackend(EmbeddingBackend): ...     # External API override (FR-024)

# Factory
def create_embedder(config) -> CachedEmbedder: ...
```

**Caching**: SQLite-backed, keyed on `(sha256(text), model_name)`.
Swapping models auto-invalidates cache. ~1.5KB per embedding.

**Thresholds**:
- Drift detection: >0.40 cosine distance requires manual approval
- Collision detection: >0.85 cosine similarity flags trigger overlap

**Dependencies**:
```toml
[project]
dependencies = ["numpy>=1.24", "fastembed>=0.3"]

[project.optional-dependencies]
openai = ["openai>=1.0"]
```

## 4. Storage & Database

### Decision: SQLite with WAL mode, per-platform

**Rationale**: SQLite is stdlib (zero dependency), handles concurrent
WAL writes, and is more than sufficient for single-user ~500
invocations/day. Per constitution: separate DB per platform.

**Location**: `~/.sio/<platform>/behavior_invocations.db`

**WAL mode**: Enables concurrent reader/writer access from multiple
CLI sessions without locking.

**Retention**: 90-day rolling purge. Gold standards exempt.

**Volume**: ~9,000-45,000 rows/platform at steady state. <50MB.

## 5. CLI Framework

### Decision: Click + Rich

**Rationale**: Click provides clean command-group CLI structure with
type validation, help generation, and shell completion. Rich provides
terminal UI for tables (health dashboard), diffs (optimization review),
and interactive prompts (batch review). Both are lightweight.

**Alternatives considered**:
- argparse — too verbose for multi-command CLI
- Typer — Click wrapper, adds unnecessary layer
- Textual — full TUI framework, overkill for SIO's needs

## 6. Testing

### Decision: pytest + pytest-cov

**Rationale**: Standard Python testing. Constitutional Principle IV
(Test-First) requires TDD — pytest's fixture system and parametrize
decorator support the Red-Green-Refactor cycle well.

**Test structure**:
- `tests/unit/` — isolated function tests with mocked DB
- `tests/integration/` — end-to-end pipeline tests with real SQLite
- `tests/contract/` — hook JSON schema validation, CLI command contracts

## 7. Package Management

### Decision: uv + pyproject.toml

**Rationale**: uv is fast, handles virtual environments, and supports
optional dependency groups (`[project.optional-dependencies]`). The
user's system already uses uv.

**Dependency policy**: Always use the latest DSPy release. Pin with
`>=` not `==` to allow upgrades. Run `uv lock --upgrade-package dspy`
regularly to stay current.

**Install experience**:
```bash
uv pip install sio              # Core + fastembed (~250MB)
uv pip install "sio[openai]"    # + OpenAI API backend
uv pip install "sio[all]"       # Everything
```

**pyproject.toml dependencies**:
```toml
[project]
dependencies = [
    "dspy",                    # Always latest (currently 3.1.3)
    "numpy>=1.24",
    "fastembed>=0.3",
    "click>=8.0",
    "rich>=13.0",
]
```
