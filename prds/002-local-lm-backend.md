# PRD 002 — Local LM backend (Ollama)

**Status:** draft
**Created:** 2026-05-02
**Owner:** unassigned

## Problem

The standalone `sio` CLI (cron-driven `sio suggest`, `sio optimize`,
batch `--auto` runs) currently needs a paid provider key to do any
LLM work. The default `[llm]` block in the installer template points
at `openai/gpt-4o-mini`; the GEPA reflection LM defaults to
`openai/gpt-5`, which is uncached at 32k tokens and meaningfully
expensive per optimize run.

For users who want SIO to mine their sessions continuously without
metering API spend — or who simply want an air-gapped/local
deployment — there is no documented zero-cost path today. `litellm`
already speaks Ollama, but:

- The installer template only lists `ollama/llama3` as a
  commented-out alternative with no setup guidance.
- `lm_factory.py` has a hardcoded `openai/...` default for
  `SIO_TASK_LM` / `SIO_REFLECTION_LM`; there is no
  `SIO_LM_PROVIDER=ollama` shortcut.
- The provider-aware adapter selection
  (`get_adapter` in `lm_factory.py:48-75`) already routes Ollama to
  `JSONAdapter(use_native_function_calling=False)`, so the wiring
  is half-done — but it is undocumented and unverified end-to-end.
- GEPA's reflection-LM expectations (long context, high
  reasoning) are unrealistic for most local 7B–13B models; we need
  a clear "what works" matrix.

## Proposal

Make Ollama a first-class, documented, smoke-tested backend for the
non-optimize subset of the SIO pipeline.

### Configuration UX

Allow the user to pick local mode with a single switch in
`~/.sio/config.toml`:

```toml
[llm]
backend = "ollama"            # new key — chooses preset bundle
model   = "qwen2.5-coder:7b"  # overrides preset task model
```

Setting `backend = "ollama"` does three things:

1. Sets `[llm].model` default to the preset task model.
2. Sets `[llm.sub].model` to a smaller preset (e.g. `qwen2.5:3b`).
3. Exports `SIO_TASK_LM` / `SIO_REFLECTION_LM` so the new
   `lm_factory` path picks up the same defaults without the user
   editing two surfaces.

### CLI surface

Add `sio doctor lm` — pings the configured backend with a 1-token
prompt and reports model name, latency, and adapter choice. Catches
"forgot to start ollama serve" and "model not pulled" before they
manifest as cryptic litellm errors deep inside `sio suggest`.

### What works / what doesn't (the matrix)

Document explicitly:

| Path | Local LM viable? | Notes |
|---|---|---|
| `sio suggest` (rule generation) | ✅ | qwen2.5-coder:7b acceptable |
| `sio distill` | ✅ | same as above |
| `sio refine` (Hop-2) | ✅ | filtering, low LM demand |
| `sio optimize` (GEPA) | ⚠ | reflection LM needs 32k context + strong reasoning; only Llama-3.1-70B-class works locally, and only on serious GPUs |
| `sio export` (DSPy datasets) | n/a | no LM call |

The matrix lives in `docs/configuration.md` and links from the
installer template comment block.

## Why it matters

- **Zero-cost continuous mining.** Cron-driven SIO becomes
  feasible without an OpenAI bill.
- **Privacy.** No session text leaves the machine. Material for
  users mining work-sensitive transcripts.
- **Determinism for tests.** A pinned local model gives
  reproducible suggestion text in CI.

## Out of scope

- Bundling/managing the Ollama process itself. SIO assumes
  `ollama serve` is already running and the model is pulled.
- LM-Studio, vLLM, llama.cpp servers. They speak the OpenAI API
  shape, so users can already point `[llm]` at their endpoint via
  `api_base_env`. We don't need first-class presets for them; a
  one-paragraph docs note is enough.
- Replacing OpenAI as the *default* in `sio init`. Default stays
  cloud; local is opt-in.

## Open questions

1. Does `dspy.JSONAdapter` actually work end-to-end with our
   suggestion `Signature`s on Ollama, or do we need to relax some
   field types? Needs an integration test before promising in docs.
2. What's the right preset task model for a 16 GB MacBook user vs.
   a Linux box with a 24 GB GPU? Two presets (`small` / `large`)
   keyed off `backend = "ollama"` plus a `tier` field?
3. Should `sio optimize` *refuse* to run with a local backend
   below a known-good capability bar (parameter count, context
   window)? Soft warn vs. hard block.

## Effort estimate

Small. Roughly:

- 0.5d: `backend = "ollama"` config plumbing + presets in
  `lm_factory.py` and `config.py`.
- 0.5d: `sio doctor lm` CLI verb.
- 0.5d: integration smoke test on a CI runner with a tiny model
  (or a mock that exercises the JSONAdapter wiring).
- 0.5d: docs update — `configuration.md` matrix + installer
  template comment block + `getting-started.md` "running locally"
  section.

## References

- `src/sio/core/dspy/lm_factory.py:48-75` — `get_adapter` already
  routes Ollama to `JSONAdapter`; this PRD makes that path
  reachable from configuration.
- `src/sio/adapters/claude_code/installer.py:16-45` (post-update) —
  the installer template that surfaces the `[llm]` choice.
- See PRD 001 for the in-harness path that doesn't need any LM
  backend at all; this PRD covers the standalone-CLI complement.
