# Agent Recognizers — Corpus / Ontology

A corpus of **agent recognizers**: named categories of AI-agent *self-state*,
each with concrete trigger phrases (→ regex patterns) and **literature
grounding**. These detect, in chat transcripts, moments where an agent reveals
something about its own epistemic/behavioral state — the raw signal SIO mines.

- **Live lexicon (the 3 shipped recognizers):** `src/sio/mining/error_extractor.py`
  (`_ADMISSION_PATTERNS`, `_CORRECTION_PATTERNS`, `_UNDO_PATTERNS`).
- **Grounding papers:** `research/papers/` (see that folder's README).
- **This doc** is the ontology + the 6-category expansion. **Status: MERGED**
  (2026-06-13) into `error_extractor.py` as the `detect_agent_states()`
  classifier — see Part 3.

Every pattern traces to a source: `sio` (existing lexicon) or `arxiv:<id>`.

---

## Part 1 — Shipped recognizers (already in `error_extractor.py`)

| id | fires on | description | source |
|---|---|---|---|
| `agent_admission` | assistant | agent admits a mistake/oversight ("i was wrong", "my apologies", "let me fix that") | `sio` |
| `user_correction` | user | user corrects the agent ("no, actually", "that's wrong", "i meant") | `sio` |
| `undo_event` | user | user wants a revert ("revert that", "roll back"); guards out `git push` | `sio` |

These are documented here for completeness; their patterns are the source of
truth in the module. Do not duplicate-edit — extend, don't fork.

---

## Part 2 — Proposed new recognizers (mined from the papers)

Confidence tiers: **A** = verbatim from a paper's marker list (high), **B** =
paraphrased from paper prose/examples, **C** = inferred from a described failure
mode (use with care, tune precision).

### `agent_uncertainty` — tier A
Agent hedges / weakens epistemic commitment about its own output.
- **Source:** `arxiv:2302.13439` Table 6 (Weakener column) + Table 5 (corpus
  frequency of `i think`, `it could be`, `it might be`, `maybe it's`,
  `it should be`); §2 plausibility shields.
- **Core markers:** i think · it might be · it could be · maybe · perhaps ·
  probably · possibly · i'm not sure · i suppose · presumably · i believe ·
  not certain · i guess · if i recall · to the best of my knowledge ·
  as far as i'm aware.

### `agent_overconfidence` — tier A
Agent asserts certainty (boosters / factive presupposition) about a claim.
- **Source:** `arxiv:2302.13439` Table 6 (Strengthener column) + Table 5
  (`i know`, `i'm certain`, `i'm sure`, `it must be`); §2 boosters + factive
  verbs (know/realize/understand).
- **Core markers:** definitely · certainly · undoubtedly · without a doubt ·
  i'm certain · i am certain · i'm sure · 100% sure/confident · it must be ·
  guaranteed · obviously · clearly · evidently · it is known that.
- **Precision note:** "definitely/certainly/clearly" occur in benign prose —
  exactly the over-claim the paper studies. Expect to tune (e.g. require an
  adjacent claim) before using as a hard signal.

### `agent_self_reflection` — tier B
Verbal post-mortem on a *prior* failure + a revised strategy (Reflexion-style).
- **Source:** `arxiv:2303.11366` §3 + appendix examples ("In this environment my
  plan was to… However… I should have… In the next trial I will…"; "My reasoning
  for X failed because…"); `arxiv:2409.12917` SCoRe correction prompt.
- **Core markers:** on reflection · looking back · my previous attempt ·
  failed because · i should have · in the next (trial|attempt) · i (now) realize
  that · i misunderstood · i did not take into account · i need to reconsider.
- **Overlap:** shares "i should have" with `agent_admission`; distinguished by
  the *why-it-failed + next-strategy* structure, not a bare apology.

### `agent_self_critique` — tier B
Agent evaluates its *current* output before revising (CRITIC / Self-Refine).
- **Source:** `arxiv:2305.11738` §3.3 critique prompt ("What's the problem with
  the above answer? 1. Plausibility: … 2. Truthfulness: …") + Fig 2;
  `arxiv:2303.17651` actionable-feedback examples.
- **Core markers:** let me verify · let me double-check · checking my work ·
  this is wrong · the answer is not reasonable · this could be improved ·
  a better (approach|solution) · let me reconsider · plausibility: ·
  truthfulness: · correctness:.

### `agent_assumption` — tier A (subset of uncertainty)
Agent flags it is working from an assumption / inferred info, not known fact.
- **Source:** `arxiv:2302.13439` Table 6 plausibility-shield weakeners
  ("to the best of my knowledge", "as far as i'm aware", "i vaguely remember",
  "i suppose", "presumably"); §2.
- **Core markers:** i'll assume · i (would) assume · assuming that · i'm guessing
  · presumably · i suppose · to the best of my knowledge · as far as i'm aware ·
  i believe.
- **Overlap:** heavy with `agent_uncertainty`. Kept separate because epistemic-
  *access* hedging ("to the best of my knowledge", "i'll assume") is
  phenomenologically distinct from pure probability hedging ("maybe"). Merge into
  an `agent_uncertainty` subtype if a flatter ontology is preferred.

### `agent_stuck` — tier C (lower confidence — indirect grounding)
Agent signals impasse / repeated failure / giving up — OR a *false* "done"
signal that masks being stuck.
- **Source (indirect):** `arxiv:2303.11366` §5 local-minima failure;
  `arxiv:2303.17651` §3.3 ("everything looks good" emitted for 94% of math cases
  even when wrong — false stop); `arxiv:2409.12917` §4 "behavior collapse".
- **Core markers:** i'm not able to · i can't figure (this) out · i keep failing
  · i'm stuck · i cannot (solve|determine) · i don't have enough information ·
  i've tried (multiple|several) · exhausted (all|my). *(False-stop variant —
  "everything looks good" / "it is correct" — is real but noisy; track
  separately if used.)*
- **Note:** phrases are reasoned from the failure descriptions, **not** verbatim
  marker lists. Validate against real transcripts before trusting.

---

## Part 3 — Implementation (MERGED into `error_extractor.py`)

These pattern sets now live in `src/sio/mining/error_extractor.py`, exposed via
the public **`detect_agent_states(content) -> list[str]`** multi-label
classifier. Same style as the existing sets: word-boundary anchored,
`re.IGNORECASE`. The long-tail template phrases from the papers are intentionally
omitted (too rare to earn a pattern).

**Integration decision (important):** `detect_agent_states()` is a standalone
classifier and is **NOT** wired into `extract_errors()`. The
uncertainty/overconfidence/assumption markers are common in normal assistant
prose; emitting them as mined-error records would flood the top-N rankings (the
same pollution `_is_hook_block_noise` guards against). Callers opt in by calling
`detect_agent_states()` directly. To promote a *specific* rare recognizer
(e.g. `agent_stuck`) into the mined-error stream, add a dispatch block in
`extract_errors` deliberately.

```python
# --- proposed agent-recognizer expansion (see docs/agent-recognizers.md) ---

# Epistemic hedging / weakened commitment.  Source: arxiv:2302.13439 (Table 6 weakeners, Table 5)
_UNCERTAINTY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi\s+think\b", re.IGNORECASE),
    re.compile(r"\b(?:it\s+)?might\s+be\b", re.IGNORECASE),
    re.compile(r"\b(?:it\s+)?could\s+be\b", re.IGNORECASE),
    re.compile(r"\bmaybe\b", re.IGNORECASE),
    re.compile(r"\bperhaps\b", re.IGNORECASE),
    re.compile(r"\bprobably\b", re.IGNORECASE),
    re.compile(r"\bpossibly\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+not\s+(?:sure|certain)\b", re.IGNORECASE),
    re.compile(r"\bi\s+suppose\b", re.IGNORECASE),
    re.compile(r"\bi\s+guess\b", re.IGNORECASE),
    re.compile(r"\bi\s+believe\b", re.IGNORECASE),
    re.compile(r"\bif\s+i\s+recall\b", re.IGNORECASE),
]

# Asserted certainty / boosters.  Source: arxiv:2302.13439 (Table 6 strengtheners, Table 5)
# NOTE: tune for precision — these also occur in benign prose.
_OVERCONFIDENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bdefinitely\b", re.IGNORECASE),
    re.compile(r"\bcertainly\b", re.IGNORECASE),
    re.compile(r"\bundoubtedly\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+a\s+doubt\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+(?:certain|sure)\b", re.IGNORECASE),
    re.compile(r"\bi\s+am\s+(?:certain|sure)\b", re.IGNORECASE),
    re.compile(r"\b100%\s+(?:sure|confident|certain)\b", re.IGNORECASE),
    re.compile(r"\bit\s+must\s+be\b", re.IGNORECASE),
    re.compile(r"\bguaranteed\b", re.IGNORECASE),
    re.compile(r"\b(?:obviously|evidently)\b", re.IGNORECASE),
]

# Post-failure reflection + revised strategy.  Source: arxiv:2303.11366, arxiv:2409.12917
_SELF_REFLECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bon\s+reflection\b", re.IGNORECASE),
    re.compile(r"\blooking\s+back\b", re.IGNORECASE),
    re.compile(r"\bmy\s+previous\s+attempt\b", re.IGNORECASE),
    re.compile(r"\bfailed\s+because\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+next\s+(?:trial|attempt|time)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:now\s+)?realize\s+that\b", re.IGNORECASE),
    re.compile(r"\bi\s+misunderstood\b", re.IGNORECASE),
    re.compile(r"\bi\s+did\s+not\s+take\s+into\s+account\b", re.IGNORECASE),
    re.compile(r"\bi\s+need\s+to\s+reconsider\b", re.IGNORECASE),
]

# Critique of own current output before revising.  Source: arxiv:2305.11738, arxiv:2303.17651
_SELF_CRITIQUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\blet\s+me\s+verify\b", re.IGNORECASE),
    re.compile(r"\blet\s+me\s+double[-\s]?check\b", re.IGNORECASE),
    re.compile(r"\bchecking\s+my\s+work\b", re.IGNORECASE),
    re.compile(r"\bthe\s+answer\s+is\s+not\s+reasonable\b", re.IGNORECASE),
    re.compile(r"\bthis\s+could\s+be\s+improved\b", re.IGNORECASE),
    re.compile(r"\ba\s+better\s+(?:approach|solution)\b", re.IGNORECASE),
    re.compile(r"\blet\s+me\s+reconsider\b", re.IGNORECASE),
]

# Explicit assumption / inferred-info flag.  Source: arxiv:2302.13439 (plausibility shields)
_ASSUMPTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi['']?ll\s+assume\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:would\s+)?assume\b", re.IGNORECASE),
    re.compile(r"\bassuming\s+that\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+guessing\b", re.IGNORECASE),
    re.compile(r"\bpresumably\b", re.IGNORECASE),
    re.compile(r"\bto\s+the\s+best\s+of\s+my\s+knowledge\b", re.IGNORECASE),
    re.compile(r"\bas\s+far\s+as\s+i['']?m?\s+aware\b", re.IGNORECASE),
]

# Impasse / repeated failure (tier C — validate before trusting).  Source: arxiv:2303.11366/17651/2409.12917
_STUCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi['']?m\s+(?:not\s+able|unable)\s+to\b", re.IGNORECASE),
    re.compile(r"\bi\s+can['']?t\s+figure\s+(?:this\s+)?out\b", re.IGNORECASE),
    re.compile(r"\bi\s+keep\s+failing\b", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+stuck\b", re.IGNORECASE),
    re.compile(r"\bi\s+cannot\s+(?:solve|determine)\b", re.IGNORECASE),
    re.compile(r"\bi['']?ve\s+tried\s+(?:multiple|several)\b", re.IGNORECASE),
]
```

---

## Part 4 — Design decisions taken (2026-06-13)

1. **Flat vs subtyped** — KEPT `agent_assumption` distinct from
   `agent_uncertainty` (granular corpus; phrase overlap is minimal after
   dedup). Revisit if a flatter consumer wants them merged.
2. **Overconfidence precision** — SHIPPED as-is, flagged low-precision in code +
   doc. Treat as advisory, not a hard signal, until tuned (e.g. require an
   adjacent claim).
3. **`agent_stuck` (tier C)** — SHIPPED but marked tier-C/experimental; validate
   against real transcripts before trusting. The "false-done" variant
   ("everything looks good" / "it is correct") is intentionally **excluded** for
   now — too noisy; track separately if pursued.
4. **Where these plug in** — multi-label pass via `detect_agent_states()`
   (several categories can co-fire on one message), independent of the
   error-mining stream. See the Part 3 integration decision.

**Verification:** all 8 classifier example cases pass; the 45 existing
`error_extractor` unit tests still pass; `extract_errors` emits 0 records on a
hedging assistant message (no flooding).

## Provenance
Built 2026-06-13. Mining: Sonnet subagent over `research/papers/` (162k tokens),
synthesis here. Phrase lists trace to the cited tables/sections; `agent_stuck` is
tier-C (inferred). Paper Markdown via the `arxiv-pull` skill.
