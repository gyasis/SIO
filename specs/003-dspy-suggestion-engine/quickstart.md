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

## Running SIO on Itself (Self-Test)

SIO is designed to improve itself. The self-test validates the full pipeline
end-to-end using SIO's own SpecStory development history as input data.

### Quick Self-Test (automated script)

```bash
# From the SIO project root
./scripts/self_test.sh

# With a custom time window
./scripts/self_test.sh --since "7 days"
```

The script runs through all four pipeline stages and validates output quality.
Exit code 0 means all checks passed.

### Manual Self-Test (step-by-step)

#### Step 1: Mine errors from your development history

```bash
sio mine --since "30 days"
```

This scans `~/.specstory/history` and `~/.claude/projects` for session
files, extracts error records (tool failures, user corrections, agent
admissions, repeated attempts, undos), and stores them in `~/.sio/sio.db`.

Expected output: `Scanned N files / Found M errors`

#### Step 2: View discovered patterns

```bash
sio patterns
```

This clusters the mined errors by semantic similarity (using fastembed
embeddings) and ranks them by frequency x recency. You should see a
table with pattern descriptions, error counts, and rank scores.

Optional filters:

```bash
# Only tool_failure patterns
sio patterns --type tool_failure

# Only user_correction patterns
sio patterns --type user_correction
```

#### Step 3: Generate suggestions

```bash
sio suggest --verbose
```

This runs the full pipeline: cluster -> persist -> dataset build -> suggestion
generation. When an LLM backend is configured (see LLM Configuration above),
suggestions are generated via DSPy. Otherwise, deterministic templates produce
targeted rules based on actual error content.

Optional filters:

```bash
# Only analyze a specific error type
sio suggest --type tool_failure

# Filter by keyword in error content
sio suggest --grep "snowflake"

# Lower the dataset threshold for small datasets
sio suggest --min-examples 1
```

#### Step 4: Review suggestions

```bash
sio suggest-review
```

Interactive review loop: accept, reject, or edit each pending suggestion.
Accepted suggestions are ready for application.

#### Step 5: Apply an accepted suggestion

```bash
sio apply <suggestion-id>
```

Writes the proposed change to the target file (e.g., CLAUDE.md) with a
diff preview and optional git commit.

### Integration Tests

```bash
# Run the self-pipeline integration test (no LLM required)
pytest tests/integration/test_self_pipeline.py -v

# Run all integration tests
pytest tests/integration/ -v

# Run with the DSPy pipeline test (requires LLM config)
pytest tests/integration/test_dspy_pipeline.py -v
```

### Verifying the Database

```bash
# Check current state
sio status

# Direct DB inspection
sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM error_records;"
sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM patterns;"
sqlite3 ~/.sio/sio.db "SELECT COUNT(*) FROM suggestions WHERE status='pending';"

# View ground truth stats
sio ground-truth status
```

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `No source directories found` | Ensure `~/.specstory/history` or `~/.claude/projects` exist with session files |
| `No errors mined yet` | Run `sio mine --since "30 days"` first |
| `No suggestions generated` | Lower `--min-examples` to 1, or mine a longer time window |
| `DSPy path unavailable` | Set LLM env vars (see LLM Configuration above); template fallback still works |
| `fastembed model download fails` | Check internet connectivity; model is cached after first download |
