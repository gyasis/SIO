# Quickstart: DSPy Suggestion Engine Development

**Feature**: 003-dspy-suggestion-engine

## Prerequisites

```bash
# Python 3.11+
python --version  # >= 3.11

# Install SIO in dev mode
cd /home/gyasisutton/dev/projects/SIO
pip install -e ".[dev]"

# Verify DSPy 3.1.3+
python -c "import dspy; print(dspy.__version__)"

# Deno (required for RLM corpus mining)
curl -fsSL https://deno.land/install.sh | sh
```

## LLM Configuration

```bash
# Option A: Environment variables (auto-detected)
export AZURE_OPENAI_API_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://your-endpoint.openai.azure.com/"
export AZURE_OPENAI_DEPLOYMENT_NAME="DeepSeek-R1-0528"

# Option B: Config file
cat > ~/.sio/config.toml << 'EOF'
[llm]
model = "azure/DeepSeek-R1-0528"
api_key_env = "AZURE_OPENAI_API_KEY"
api_base_env = "AZURE_OPENAI_ENDPOINT"
temperature = 0.7
max_tokens = 2000

[llm.sub]
model = "azure/gpt-4o-mini"
EOF
```

## Verify DSPy Connection

```python
import dspy
import os

lm = dspy.LM(
    "azure/DeepSeek-R1-0528",
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_base=os.environ["AZURE_OPENAI_ENDPOINT"],
)
dspy.configure(lm=lm)

# Quick test
predictor = dspy.Predict("question -> answer")
result = predictor(question="What is 2+2?")
print(result.answer)
```

## Development Workflow

```bash
# Run tests (TDD — tests written first)
pytest tests/unit/ -v

# Lint
ruff check src/ tests/

# Run specific test file
pytest tests/unit/test_dspy_signatures.py -v

# Run integration tests (requires LLM)
pytest tests/integration/test_dspy_pipeline.py -v
```

## Key Files to Modify

| File | What Changes |
|------|-------------|
| `src/sio/core/config.py` | Add LLM config fields |
| `src/sio/core/db/schema.py` | Add ground_truth + optimized_modules tables |
| `src/sio/core/db/queries.py` | Add ground truth CRUD |
| `src/sio/core/dspy/optimizer.py` | Replace stub with real DSPy |
| `src/sio/core/dspy/rlm_miner.py` | Replace stub with real RLM |
| `src/sio/suggestions/generator.py` | Add DSPy path + template fallback |
| `src/sio/cli/main.py` | Add ground-truth commands |

## Key Files to Create

| File | Purpose |
|------|---------|
| `src/sio/core/dspy/signatures.py` | DSPy Signature definitions |
| `src/sio/core/dspy/modules.py` | ChainOfThought module wrappers |
| `src/sio/core/dspy/metrics.py` | Quality metric function |
| `src/sio/core/dspy/lm_factory.py` | Config → dspy.LM factory |
| `src/sio/core/dspy/module_store.py` | Save/load optimized modules |
| `src/sio/suggestions/dspy_generator.py` | DSPy-powered generation |
| `src/sio/ground_truth/__init__.py` | Package init |
| `src/sio/ground_truth/generator.py` | Agent-generated candidates |
| `src/sio/ground_truth/reviewer.py` | Human review interface |
| `src/sio/ground_truth/corpus.py` | Corpus management |
| `src/sio/ground_truth/seeder.py` | Seed example generation |
