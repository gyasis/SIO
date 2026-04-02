# Addendum: Available Azure AI Models for Development

**Status**: Development reference — NOT a permanent project dependency
**Date**: 2026-02-25
**Context**: These models are available on Gyasi's Azure AI Services account during SIO development. The SIO project itself is model-agnostic — end users choose their own LLM provider and models at install time.

> **Important**: This addendum documents what's available for development
> and testing. SIO's codebase MUST NOT hard-code any of these models.
> All model references MUST be configurable via `~/.sio/config.toml`.
> Each SIO installation chooses its own models.

---

## Azure Environment

```
Endpoint:        https://admin-m9ihapvr-eastus2.services.ai.azure.com
Resource Group:  prefect
Account:         admin-m9ihapvr-eastus2
Region:          East US 2
```

**Environment variables** (set in shell profile):
- `AZURE_OPENAI_ENDPOINT` — endpoint URL
- `AZURE_OPENAI_API_KEY` — API key
- `AZURE_OPENAI_DEPLOYMENT_NAME` — current default deployment

---

## Deployed Chat/Reasoning Models

For DSPy optimizers (GEPA, MIPROv2, BootstrapFewShot) and RLM corpus mining.

| Deployment Name | Model | Capacity | Recommended Use |
|----------------|-------|----------|-----------------|
| `gpt-5.2` | gpt-5.2 | 500 | Primary optimizer LM — highest capability |
| `gpt-5.1` | gpt-5.1 | 1000 | High-capacity alternative |
| `gpt-5-chat` | gpt-5-chat | 1000 | High-throughput batch work |
| `o4-mini` | o4-mini | 150 | Reasoning-heavy optimization decisions |
| `gpt-5-mini` | gpt-5-mini | 150 | Mid-tier cost/quality balance |
| `gpt-4.1-mini` | gpt-4.1-mini | 250 | Cost-efficient sub-LM for RLM mining |
| `gpt-4o-mini` | gpt-4o-mini | 250 | Budget option for high-volume tasks |
| `DeepSeek-R1-0528` | DeepSeek-R1 | 250 | Current default — strong reasoning |
| `model-router` | model-router | 125 | Auto-routes to best available model |
| `grok-4-fast-reasoning` | grok-4-fast-reasoning | 150 | Fast reasoning alternative |
| `Kimi-K2-Thinking` | Kimi-K2-Thinking | 100 | Alternative reasoning model |
| `Llama-3.3-70B-Instruct` | Llama-3.3-70B | 100 | Open-source option |
| `codex-mini` | codex-mini | 150 | Code-focused tasks |
| `gpt-5-codex` | gpt-5-codex | 150 | Code generation |

## Deployed Embedding Models

For semantic drift detection (FR-011) and trigger collision monitoring (FR-012).

| Deployment Name | Model | Dimensions | Recommended Use |
|----------------|-------|-----------|-----------------|
| `text-embedding-3-small` | text-embedding-3-small | 1536 | Cost-efficient API override for fastembed |
| `text-embedding-3-large` | text-embedding-3-large | 3072 | Higher quality — overkill for SIO |
| `text-embedding-ada-002` | text-embedding-ada-002 | 1536 | Legacy, still functional |

---

## Development Defaults

For SIO development and testing, use these settings:

```toml
# ~/.sio/config.toml (development)

[llm]
provider = "azure"
deployment = "gpt-5.2"          # primary optimizer
sub_deployment = "gpt-4.1-mini" # RLM corpus mining (cost-efficient)

[embedding]
backend = "fastembed"           # local default — no API dependency
model = "all-MiniLM-L6-v2"

# Optional API override for embedding comparison testing:
# backend = "api"
# [embedding.api]
# endpoint = "https://admin-m9ihapvr-eastus2.services.ai.azure.com/openai/deployments/text-embedding-3-small/embeddings?api-version=2024-10-21"
# api_key_env = "AZURE_OPENAI_API_KEY"
# model = "text-embedding-3-small"
# dimension = 1536
```

### DSPy Integration Pattern

```python
import dspy
import os

endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
api_key = os.environ["AZURE_OPENAI_API_KEY"]

# Primary LM — decides optimization strategy (GEPA/MIPROv2)
main_lm = dspy.LM(
    "azure/gpt-5.2",
    api_base=endpoint,
    api_key=api_key,
    api_version="2024-10-21",
)

# Sub-LM — cheap model for RLM's built-in llm_query() calls
sub_lm = dspy.LM(
    "azure/gpt-4.1-mini",
    api_base=endpoint,
    api_key=api_key,
    api_version="2024-10-21",
)

dspy.configure(lm=main_lm)
```

### RLM Corpus Mining Pattern

`dspy.RLM` is an iterative reasoning module with a built-in code
interpreter. Inside the interpreter, two built-in tools are always
available:

- **`llm_query(prompt)`** — single LLM call (uses `sub_lm` if set)
- **`llm_query_batched(prompts)`** — batch LLM calls

The `sub_lm` parameter controls which model these built-in tools
route to. The main LM (from `dspy.configure`) decides the strategy
and writes the interpreter code; the sub-LM handles the extraction
work inside that code.

```python
# RLM for failure context mining
# main_lm (gpt-5.2) decides HOW to analyze the failure
# sub_lm (gpt-4.1-mini) handles the llm_query() calls inside the REPL
rlm = dspy.RLM(
    "conversation_corpus, failure_record -> failure_analysis",
    sub_lm=sub_lm,
    max_iterations=20,
    max_llm_calls=50,
)

result = rlm(
    conversation_corpus="<specstory content>",
    failure_record="<invocation context>",
)
print(result.failure_analysis)
```

**Cost optimization**: The main LM runs 1-2 calls (strategy + code
generation). The sub-LM runs many calls (extraction, analysis). Using
gpt-4.1-mini as sub-LM keeps costs low for the high-volume work while
gpt-5.2 handles the reasoning.

### Sub-LM Cost Tiers (for `llm_query()` inside RLM)

The sub-LM is the cost-sensitive path — it handles 10-50 calls per
mining run. Choose based on budget:

| Tier | Azure Deployment | Est. Cost/Run | Quality |
|------|-----------------|---------------|---------|
| Budget | `gpt-4o-mini` | ~$0.01-0.03 | Good enough for extraction |
| Default | `gpt-4.1-mini` | ~$0.03-0.08 | Recommended balance |
| Free | Ollama (local) | $0.00 | Use n=3-5 voting to compensate |
| Premium | `gpt-5-mini` | ~$0.10-0.20 | Diminishing returns for extraction |

**Ollama as free alternative**: For users without API budgets, local
Ollama models (llama3.3:70b, mistral, phi-3) can serve as sub-LM
with n-pass voting (n=3-5) to match API model quality. SIO's config
supports this via `sub_provider = "ollama"` + `sub_n = 3`.

---

## What This Means for the SIO Project

1. **SIO is model-agnostic.** The codebase takes `provider`, `deployment`,
   and `api_key_env` from config. No Azure-specific code in `core/`.

2. **These models are for development only.** End users bring their own
   LLM access — could be OpenAI direct, Azure, Anthropic, local Ollama,
   or any DSPy-compatible provider.

3. **Embeddings default to local fastembed.** The Azure embedding models
   are available as an API override but the core loop runs offline with
   fastembed (ONNX runtime, all-MiniLM-L6-v2).

4. **Model selection is a user decision.** SIO's job is to optimize
   prompts using whatever LLM the user configures. The optimizer
   quality depends on the chosen model's capability, but SIO works
   with any DSPy-compatible LLM.

5. **Sub-LM can be free.** The RLM sub-LM (high-volume extraction
   calls) can run on local Ollama with n-pass voting, making the
   entire corpus mining pipeline zero-cost beyond the 1-2 root LM
   calls. This is an architectural advantage of the variable-space
   approach (Constitution X).
