# Module 4: The DSPy Brain — How a Pattern Becomes a Rule

### Teaching Arc
- **Metaphor:** A **vending machine with a worn-in panel**. Most vending machines just dispense whatever button you press. DSPy is a vending machine that learns: every time someone presses B4 and complains the chips were stale, the machine retrains itself so that next time you press B4 you get the *good* bag. The "machine" is the LLM. The "training" is DSPy optimization. The "good bag" is a rule that survives quality checks.
- **Opening hook:** The Miner found 23 instances of `sed -i` wipes. The Clusterer grouped them. Now someone has to actually write the sentence: "Never use `sed -i` — use the Edit tool instead." Who does that? And how does it not hallucinate a generic platitude like "always be careful with files"?
- **Key insight:** SIO uses **DSPy** — a Python framework where you describe *what* the LLM should do in a typed `Signature`, then `dspy.Module`s run it. Every LLM call goes through ONE function (`lm_factory.get_task_lm()`), and the prompt itself is **optimizable** — DSPy can run thousands of variations of the prompt against a labeled corpus and keep the version that scores highest.
- **"Why should I care?":** If a rule SIO writes looks generic or wrong, the fix is usually in the **signature** (the contract) or the **few-shot examples** in its docstring — not in the calling code. Knowing this is where to look saves hours.

### Code Snippets (pre-extracted)

**File: `src/sio/core/dspy/lm_factory.py` (lines 1-40)** — single source of LLM construction:
```python
"""LM backend factory — single-source dspy.LM construction (FR-041, SC-022).

All dspy.LM(...) construction happens here. No other file in src/sio/ may
construct dspy.LM directly; the grep test in test_lm_factory.py enforces this.

Environment overrides:
  SIO_TASK_LM       — model string for get_task_lm()  (default: openai/gpt-4o-mini)
  SIO_REFLECTION_LM — model string for get_reflection_lm() (default: openai/gpt-5)
"""
import os, dspy, litellm
litellm.drop_params = True

def get_task_lm() -> dspy.LM:
    """LM used for normal module forward passes. Cheap, fast, cached."""
    model = os.environ.get("SIO_TASK_LM", "openai/gpt-4o-mini")
    return dspy.LM(model, cache=True, temperature=0.0, max_tokens=4096)
```

**File: `src/sio/core/dspy/signatures.py` (PatternToRule signature)** — the contract:
```python
class PatternToRule(dspy.Signature):
    """Generate a concise CLAUDE.md rule that prevents the given error pattern.

    The rule must be actionable, file-path-safe, and <= 3 sentences.

    CRITICAL — required specificity (B5 grounding directives):
    - The rule TITLE must reference the SPECIFIC tokens from `pattern_description`
      (tool name, env var, path, command — whatever recurs in `Common phrases:`).
      Generic titles like "Always check inputs" or "Verify configuration" are
      WRONG — they lose the discriminating signal that justifies the rule.
    - The rule BODY must cite the concrete failure observed in `example_errors`.
    - If `example_errors` shows an env var, file path, or command literal,
      that literal MUST appear verbatim in the rule.
    """
    # input fields: pattern_description, example_errors, project_context
    # output fields: rule_title, rule_body, rule_rationale
```

**File: `src/sio/suggestions/dspy_generator.py` (lines 50-65)** — surface targets:
```python
_SURFACE_TARGET_MAP: dict[str, str] = {
    "claude_md_rule":    "CLAUDE.md",
    "skill_update":      ".claude/skills/",
    "hook_config":       ".claude/hooks/",
    "mcp_config":        ".claude.json",
    "settings_config":   ".claude/settings.json",
    "agent_profile":     ".claude/agents/",
    "project_config":    "CLAUDE.md",
}
```

**File: `src/sio/core/dspy/optimizer.py` (lines 1-15)** — three optimizers:
```python
"""DSPy optimizer wrapper — runs prompt optimization with quality gates.

Three optimizers available via `sio optimize --optimizer <name>`:
  GEPA (default)         — reflective, uses a separate reflection LM
  MIPROv2                — few-shot instruction optimization
  BootstrapFewShot       — fast bootstrapping with minimal labeled data
"""
```

### Interactive Elements

- [x] **Code↔English translation** — `PatternToRule` signature docstring. Right column: "A `Signature` is a typed contract for an LLM. Inputs go in, outputs come out, the docstring tells the LLM *how*. The B5 grounding directives are explicit anti-platitude rules: 'don't say "always check inputs" — name the actual command.' The docstring itself is part of the prompt; this is why it matters that it's so detailed."
- [x] **Code↔English translation** — `get_task_lm()`. Right column: "Every LLM call in SIO routes through this function. Why? So you can swap models with one env var (`SIO_TASK_LM=anthropic/claude-3-5-sonnet`), and so a single test can grep the codebase and assert no one bypassed it. **Single point of LLM construction = single point of safety.**"
- [x] **Group chat animation** — Required mandatory element. Actors: **Pattern**, **Suggester (DSPy Module)**, **lm_factory.get_task_lm()**, **OpenAI / Anthropic API**, **Quality Gates**. Sequence:
  1. Pattern → Suggester: "23 errors, all about `sed -i` wipes on .env"
  2. Suggester → lm_factory: "I need a task LM"
  3. lm_factory → Suggester: "Here: gpt-4o-mini, temp=0, cached"
  4. Suggester → API: prompt built from PatternToRule signature
  5. API → Suggester: "rule_title: 'Never use sed -i' / rule_body: ...'"
  6. Suggester → Quality Gates: "Validate format, check for PHI, check specificity"
  7. Quality Gates → Suggester: "OK ✓ — score 0.87"
  8. Suggester → DB: "Saved suggestion #142 with confidence 0.87"
- [x] **Quiz — multiple-choice** — "Why does SIO require all `dspy.LM(...)` calls to go through `lm_factory.py`?" Options: (A) Performance (B) So model + caching + temperature defaults are centralized; one grep test enforces it ✅ (C) Required by DSPy library (D) For licensing.
- [x] **Quiz — scenario** — "A SIO-generated rule says 'Always check your inputs before running commands.' What went wrong?" Correct: The B5 grounding directive failed — the LLM produced a generic platitude instead of citing the actual command (e.g., `sed -i`). Fix: improve the few-shot examples in `PatternToRule.__doc__` or run `sio optimize --optimizer gepa` against a larger gold-standard corpus.
- [x] **Pattern cards** — three cards side-by-side, one per optimizer:
  - **GEPA** (default) — "Reflective. Uses a second LM to critique and refine. Slow but best quality."
  - **MIPROv2** — "Bayesian-search over instruction variants. Good when you have 50+ examples."
  - **BootstrapFewShot** — "Generates synthetic few-shot examples from your trainset. Fast, minimum data needed."
- [x] **Glossary tooltips** — "DSPy", "signature", "module (DSPy)", "few-shot", "optimizer (DSPy)", "litellm", "GEPA", "MIPROv2", "BootstrapFewShot", "PHI", "quality gate".

### Aha Callouts
1. **"The docstring IS the prompt."** When you change a `Signature`'s docstring, you've changed how the LLM behaves. There's no separate "prompt template" file — that's the point of DSPy.
2. **"Optimizers don't fine-tune the model — they fine-tune the prompt."** No weights are updated. DSPy runs your module against labeled examples, mutates the prompt, keeps the version that scores best. The resulting "optimized module" is a JSON file at `~/.sio/optimized/`.

### Reference Files to Read
- `references/interactive-elements.md` → Group Chat Animation, Multiple-Choice Quizzes, Scenario Quizzes, Pattern Cards, Code↔English Translation, Callout Boxes
- `references/design-system.md` → pattern card tokens, code styling
- `references/content-philosophy.md` → all of it
- `references/gotchas.md` → all of it

### Connections
- **Previous module:** "From Sessions to Patterns" — the Clusterer produced a Pattern; this module turns it into a rule.
- **Next module:** "Closing the Loop" — the rule still has to be approved and atomically written to disk without trashing anything.
- **Tone/style notes:** Accent = teal. DSPy is dense — emphasize the **two big shifts**: (1) prompts are typed (signatures), (2) prompts are optimizable (the docstring is real source code, not throwaway text).
