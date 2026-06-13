# Research Papers — Self-Correction & Agent-Admission Detection

Literature backing SIO's friction signals — specifically `agent_admission`
(an agent acknowledging a mistake), `user_correction`, and the broader
self-correction loop SIO mines for. Each paper is stored as `<id>.pdf` (source),
`<id>.md` (pymupdf4llm Markdown, RAG-ready), and `<id>.json` (metadata).

Pulled 2026-06-13 via the `arxiv-pull` skill/CLI (`~/.local/bin/arxiv-pull`).

| arXiv | Title | Why it's here |
|---|---|---|
| [2302.13439](https://arxiv.org/abs/2302.13439) | Navigating the Grey Area: How Expressions of Uncertainty and Overconfidence Affect Language Models (Zhou, Jurafsky, Hashimoto) | The epistemic-marker / hedging-vs-confidence taxonomy — closest academic grounding for expanding `error_extractor.py`'s admission lexicon beyond outright "I was wrong" into uncertainty markers. |
| [2305.11738](https://arxiv.org/abs/2305.11738) | CRITIC: LLMs Can Self-Correct with Tool-Interactive Critiquing (Gou et al.) | Self-correction *with external tools*; key finding — LLMs are unreliable at self-verification without them. Frames why SIO mines real corrections rather than trusting self-reports. |
| [2409.12917](https://arxiv.org/abs/2409.12917) | Training Language Models to Self-Correct via Reinforcement Learning — SCoRe (DeepMind) | RL-based intrinsic self-correction; the "how do agents get better at fixing themselves" angle SIO's improvement loop parallels. |
| [2303.11366](https://arxiv.org/abs/2303.11366) | Reflexion: Language Agents with Verbal Reinforcement Learning (Shinn et al.) | Verbal self-reflection on failures — directly the behavior SIO's `agent_admission` signal detects in the wild. |
| [2303.17651](https://arxiv.org/abs/2303.17651) | Self-Refine: Iterative Refinement with Self-Feedback (Madaan et al.) | The iterate-on-own-output loop; the positive counterpart to error mining. |

## Connection to the SIO codebase

The hand-tuned phrase lexicon that classifies these behaviors in real
transcripts lives at `src/sio/mining/error_extractor.py`
(`_ADMISSION_PATTERNS`, `_CORRECTION_PATTERNS`, `_UNDO_PATTERNS`). This folder is
the literature that lexicon can be grounded against / expanded from — notably
2302.13439 for the uncertainty-marker axis it doesn't yet cover.

## Adding more

```bash
arxiv-pull <id-or-url> [<id> ...] --out research/papers
```
Then add a row above.
