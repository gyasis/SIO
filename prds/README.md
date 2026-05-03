# PRDs (backlog)

Lightweight problem statements for work that is *not yet* a numbered
SpecKit spec under `specs/`. PRDs here are intentionally short — one
page each — and are meant to be picked up later, promoted to a full
spec, or rejected.

Naming: `prd-<slug>.md` (no leading `NNN-` so we don't collide with
SpecKit numbering — `specs/NNN-…` is the formal numbered surface;
`prds/prd-…` is the unnumbered backlog).

A PRD graduates by being copied into `specs/NNN-<slug>/spec.md` and
filled out via the SpecKit `/speckit.specify` flow. Until then, it
lives here.

## Contents

| Title | Status |
|---|---|
| [Skill-side LLM delegation](prd-skill-side-llm-delegation.md) | draft |
| [Local LM backend (Ollama)](prd-local-lm-backend.md) | draft |
| [Install-orchestration regression after harness refactor](prd-install-orchestration-regression.md) | draft 🔴 high |
| [Promote violated rule → PreToolUse hook](prd-violated-rule-to-pretooluse-hook.md) | draft (blocked by install-orchestration) |
| [Fresh-machine SIO install test report + session handoff](prd-fresh-machine-install-handoff.md) | report (handoff doc, not a proposal) |
