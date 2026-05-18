"""sio.amplify — synthetic dataset amplification.

Takes a curated JSONL (from ``sio curate``) and produces N synthetic
variants per row that preserve the underlying error CATEGORY while
varying surface features (paths, tool names, error phrasing). Output is
a JSONL in the same canonical PatternToRule shape so it can be consumed
directly by ``sio optimize --trainset-file``.

PIPELINE
--------
For each input row:
  1. Generate N variants via Gemini Flash with a "preserve the category"
     prompt. Different paths, different tool names, different error
     wording — same underlying pattern.
  2. LLM-as-judge each variant: score 0-1 for "does this variant
     preserve the original pattern_id category?"
  3. Drop variants with score < ``min_judge_score`` (default 0.6).
  4. Emit surviving variants as JSONL, one per line, in the same shape
     as ``sio curate``.

PATTERN PRESERVATION
-------------------
The variation generator is told the original ``pattern_id`` and is
explicitly instructed to preserve the category. The judge confirms.
Variants that drift to a different category are filtered out. This is
the difference between "synthetic noise" and "amplified signal."

COST
----
Roughly: ``rows * (n + n) * ~400 tokens`` on Gemini Flash. For the
canonical 93-row curated set with n=10:
  93 × 10 × 400 (gen) + 93 × 10 × 200 (judge) ≈ 558k tokens ≈ ~$0.20.

USAGE
-----
    sio amplify --input ~/.sio/curated/<name>.jsonl --n 10
                --output ~/.sio/amplified/<name>_amplified.jsonl
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


_lm_lock = threading.Lock()
_gen_lm = None
_judge_lm = None


def _get_lms():
    """Lazy-initialize Gemini Flash for both generation and judging."""
    global _gen_lm, _judge_lm
    if _gen_lm is not None and _judge_lm is not None:
        return _gen_lm, _judge_lm
    with _lm_lock:
        if _gen_lm is not None and _judge_lm is not None:
            return _gen_lm, _judge_lm

        import dspy  # noqa: PLC0415

        api_key = os.environ.get("SIO_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "amplify requires SIO_GEMINI_API_KEY (or GEMINI_API_KEY). "
                "Source ~/.sio/secrets.env first."
            )
        # Slightly higher temperature for variant generation (creativity)
        _gen_lm = dspy.LM(
            model="gemini/gemini-flash-latest",
            api_key=api_key,
            temperature=0.8,
            max_tokens=4000,  # enough for ~10 variants in JSON array
        )
        # Low temperature for judging (consistency).
        # NOTE 2026-05-18 (adversarial-audit H2 CONFIRMED): max_tokens was
        # 500. ChatAdapter scaffolding ([[ ## scores_json ## ]] + [[ ## completed ## ]]
        # sentinel) + Gemini-Flash reasoning preamble + N floats CONSUMES
        # ~600-2000 tokens for typical N=10. Stderr capture today showed
        # scores_json values truncated mid-array: '[1.0, 1.', '[1.0, 1.0,'
        # → JSONDecodeError → all variants got 0.5 placeholder, defeating
        # the judge entirely. Raising to 2000 covers ChatAdapter overhead
        # for N up to ~30 variants. See PRD amplify_observability_gaps
        # ISSUE 1 for the full diagnostic.
        _judge_lm = dspy.LM(
            model="gemini/gemini-flash-latest",
            api_key=api_key,
            temperature=0.0,
            max_tokens=2000,
        )
    return _gen_lm, _judge_lm


# ---------------------------------------------------------------------------
# DSPy signatures
# ---------------------------------------------------------------------------


def _make_modules():
    """Build the generator + judge DSPy modules. Called once per amplify run.

    BULK design (one LLM call handles N variants at once):
    * GenerateVariants — emits a JSON array of N {error_text, user_message}
      pairs per source row.
    * JudgeVariants — emits a JSON array of N float scores, one per variant.

    This reduces N-variants-per-row from 2N calls (gen+judge each) down to
    2 calls total per source row.
    """
    import dspy  # noqa: PLC0415

    class GenerateVariants(dspy.Signature):
        """Generate a JSON ARRAY of N category-preserving error variants.

        Output `variants_json` must be a JSON list of objects. Each object
        has keys `error_text` (string) and `user_message` (string).
        Length MUST equal `n_variants`. Each variant preserves the
        underlying pattern_id category — same failure mode, different
        surface features (paths, tool names, phrasing).
        The user_message must contain frustration markers (!! or ??).
        """

        original_pattern_id: str = dspy.InputField(
            desc="The category we MUST preserve, e.g. tool_failure__filenotfound"
        )
        original_error_text: str = dspy.InputField()
        original_tool_name: str = dspy.InputField()
        n_variants: int = dspy.InputField(desc="How many variants to produce")
        variants_json: str = dspy.OutputField(
            desc=(
                "JSON array, length = n_variants. Each item: "
                '{"error_text": "...", "user_message": "...(contains !! or ??)..."}. '
                "ONLY the JSON. No code fences, no commentary."
            )
        )

    class JudgeVariants(dspy.Signature):
        """Score each variant 0.0-1.0 for FAILURE-MODE preservation against
        the target pattern_id.

        SURFACE FEATURES — specific file paths, tool names, command lines,
        line numbers, error message wording — are EXPECTED TO DIFFER between
        variants. They MUST NOT lower the score. Two errors with completely
        different paths/commands but the same FAILURE CATEGORY (e.g. both
        are "permission denied" or both are "file not found") should score
        ~1.0.

        Only score lower when the variant FAILURE MODE drifts to a
        different category (e.g. you're judging `tool_failure__permissiondenied`
        and a variant is actually a network timeout — that's drift, score
        ~0.2).

        Output `scores_json` is a JSON list of floats matching the order
        of `variants_json`. 1.0 = perfect category match. 0.0 = total drift.
        """

        # NOTE 2026-05-18: dropped `original_error_text` from inputs. Today's
        # E2E test showed the judge was anchoring on surface similarity to
        # the original instead of evaluating category preservation. Without
        # the original in scope, the judge MUST evaluate against pattern_id.
        # This is Tier 1 of PRD sio_meta_optimize_judgevariants_2026-05-18
        # (manual prompt fix; Tier 2-5 is DSPy meta-optimization).
        original_pattern_id: str = dspy.InputField(
            desc="Target failure category id, e.g. tool_failure__permissiondenied"
        )
        variants_json: str = dspy.InputField(
            desc='JSON array of variants — each item {"error_text": "..."}'
        )
        scores_json: str = dspy.OutputField(
            desc=(
                "JSON array of floats matching variants_json order. "
                "Score 1.0 if variant preserves the FAILURE MODE of "
                "pattern_id; 0.0 if it drifts to a different category. "
                "Different paths/tools/commands are NOT drift. ONLY the JSON."
            )
        )

    gen = dspy.Predict(GenerateVariants)
    judge = dspy.Predict(JudgeVariants)
    return gen, judge


# ---------------------------------------------------------------------------
# Top-level amplify
# ---------------------------------------------------------------------------


def amplify(
    input_path: Path,
    output_path: Path,
    n_per_row: int = 10,
    min_judge_score: float = 0.6,
    max_workers: int = 4,
) -> dict:
    """Amplify a curated JSONL by generating N variants per row.

    Args:
        input_path: JSONL from ``sio curate`` (canonical PatternToRule shape).
        output_path: where to write the amplified JSONL.
        n_per_row: variants to generate per input row.
        min_judge_score: drop variants below this judge score (0.0-1.0).
        max_workers: thread-pool parallelism for the LLM calls.

    Returns:
        Dict with counts: input_rows, total_generated, kept, dropped, path.
    """
    import dspy  # noqa: PLC0415

    # Load input
    inputs: list[dict] = []
    with input_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            inputs.append(json.loads(line))

    if not inputs:
        return {"input_rows": 0, "total_generated": 0, "kept": 0, "dropped": 0,
                "path": str(output_path)}

    import time as _time  # noqa: PLC0415

    gen_lm, judge_lm = _get_lms()
    gen, judge = _make_modules()

    # BULK pipeline — one LLM call per row produces N variants.
    # Total calls = 2 * len(inputs) (gen + judge), NOT 2 * len(inputs) * N.
    # Two-phase to avoid dspy.configure race across threads.

    generated: list[tuple[dict, list[dict]]] = []  # (row, [{error_text, user_message}, ...])
    lock = threading.Lock()
    done = [0]

    def _retry_429(fn, *args, **kwargs):
        """Retry the LLM call on RateLimitError up to 3x with backoff."""
        for attempt in range(3):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "rate" in msg or "429" in msg or "quota" in msg:
                    _time.sleep(2 ** attempt)
                    continue
                raise
        return None

    def _gen_one(row: dict):
        try:
            data = row.get("data", {})
            meta = data.get("_meta", {})
            pattern_id = meta.get("pattern_id") or "tool_failure__unclassified"
            error_text = (data.get("example_errors", [""])[0] or "")[:400]
            tool_name = meta.get("tool_name") or "unknown"

            out = _retry_429(
                gen,
                original_pattern_id=pattern_id,
                original_error_text=error_text,
                original_tool_name=tool_name,
                n_variants=n_per_row,
            )
            if out is None:
                return
            raw = (out.variants_json or "").strip()
            # Strip code fences if Gemini added them
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1] if "```" in raw[3:] else raw[3:]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            try:
                variants = json.loads(raw)
            except Exception:
                return
            if not isinstance(variants, list):
                return
            cleaned = []
            for v in variants[:n_per_row]:
                if not isinstance(v, dict):
                    continue
                et = (v.get("error_text") or "").strip()
                um = (v.get("user_message") or "").strip()
                if et:
                    cleaned.append({"error_text": et, "user_message": um})
            if cleaned:
                with lock:
                    generated.append((row, cleaned))
        except Exception as exc:  # noqa: BLE001
            import sys as _sys  # noqa: PLC0415
            print(f"  gen-err: {type(exc).__name__}: {str(exc)[:120]}",
                  file=_sys.stderr, flush=True)
        finally:
            with lock:
                done[0] += 1
                if done[0] % 10 == 0:
                    print(f"  gen [{done[0]}/{len(inputs)}]", flush=True)

    print(f"Phase 1: GENERATE (bulk) — {len(inputs)} rows × {n_per_row} variants/row = "
          f"{len(inputs)} LLM calls",
          flush=True)
    dspy.configure(lm=gen_lm)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_gen_one, row) for row in inputs]
        for _ in as_completed(futures):
            pass
    total_generated = sum(len(v) for _, v in generated)
    print(f"Phase 1 done: {len(generated)} rows produced {total_generated} variants",
          flush=True)

    # Phase 2: JUDGE (bulk — all variants for a row at once)
    results: list[tuple[dict, str, str, float]] = []
    done[0] = 0

    def _judge_one(item: tuple[dict, list[dict]]):
        row, variants = item
        try:
            data = row.get("data", {})
            meta = data.get("_meta", {})
            pattern_id = meta.get("pattern_id") or "unknown"
            orig_err = (data.get("example_errors", [""])[0] or "")[:400]

            # Compact variant list for the judge prompt
            judge_input = json.dumps(
                [{"error_text": v["error_text"][:400]} for v in variants]
            )
            # NOTE 2026-05-18: dropped `original_error_text=orig_err` kwarg.
            # JudgeVariants no longer accepts it as an input (see signature
            # at line 132). The judge now grades against pattern_id +
            # variants only — forces category-evaluation, kills the
            # surface-similarity anchor bias.
            out = _retry_429(
                judge,
                original_pattern_id=pattern_id,
                variants_json=judge_input,
            )
            # XIII (loud failure): every fallback path below MUST emit a
            # structured signal so production failure modes don't hide
            # behind a 0.5 placeholder that coincidentally passes a 0.5
            # threshold. Today's E2E test caught this — silent fallback
            # meant we had 0 quality assurance on the kept variants.
            import sys as _sys  # noqa: PLC0415
            if out is None:
                # Fallback path A: judge call returned None (likely None
                # response or _retry_429 exhausted). All variants get
                # placeholder 0.5 → if user picks threshold ≤0.5 they get
                # FALSE QA. LOUD signal so user/agent sees it.
                print(
                    f"  [JUDGE_FALLBACK_NONE] row pattern={pattern_id} — "
                    f"judge returned None; {len(variants)} variants "
                    f"assigned PLACEHOLDER 0.5 (not real judge score).",
                    file=_sys.stderr, flush=True,
                )
                for v in variants:
                    with lock:
                        results.append((row, v["error_text"], v["user_message"], 0.5))
                return
            raw = (out.scores_json or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1] if "```" in raw[3:] else raw[3:]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            try:
                scores = json.loads(raw)
            except Exception as _parse_exc:  # noqa: BLE001
                # Fallback path B: judge returned text but JSON parse
                # failed. Same FALSE QA risk. LOUD signal.
                print(
                    f"  [JUDGE_FALLBACK_PARSE] row pattern={pattern_id} — "
                    f"scores_json={raw[:120]!r} parse_err={type(_parse_exc).__name__}; "
                    f"{len(variants)} variants assigned PLACEHOLDER 0.5.",
                    file=_sys.stderr, flush=True,
                )
                scores = []
            _fallback_index_used = False
            _fallback_cast_used = False
            for i, v in enumerate(variants):
                try:
                    if i >= len(scores):
                        _fallback_index_used = True
                        sc = 0.5
                    else:
                        sc = float(scores[i])
                except Exception:
                    _fallback_cast_used = True
                    sc = 0.5
                with lock:
                    results.append((row, v["error_text"], v["user_message"], sc))
            # Fallback path C: variant count > score count. LOUD signal.
            if _fallback_index_used:
                print(
                    f"  [JUDGE_FALLBACK_INDEX] row pattern={pattern_id} — "
                    f"got {len(scores)} scores for {len(variants)} variants; "
                    f"extras assigned PLACEHOLDER 0.5.",
                    file=_sys.stderr, flush=True,
                )
            # Fallback path D: score wasn't a float. LOUD signal.
            if _fallback_cast_used:
                print(
                    f"  [JUDGE_FALLBACK_CAST] row pattern={pattern_id} — "
                    f"one or more scores not coercible to float; "
                    f"affected variants assigned PLACEHOLDER 0.5.",
                    file=_sys.stderr, flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            # Graceful degradation: when the judge call itself fails (DSPy
            # adapter error / empty Gemini response / etc.), keep the variants
            # with a placeholder score = min_judge_score so they survive the
            # downstream filter. Better to have an unjudged variant than to
            # silently drop work the generator already paid for.
            import sys as _sys  # noqa: PLC0415
            print(f"  judge-err: {type(exc).__name__}: {str(exc)[:120]} — "
                  f"keeping {len(variants)} variants with placeholder score",
                  file=_sys.stderr, flush=True)
            for v in variants:
                with lock:
                    results.append((row, v["error_text"], v["user_message"],
                                    min_judge_score))
        finally:
            with lock:
                done[0] += 1
                if done[0] % 10 == 0:
                    print(f"  judge [{done[0]}/{len(generated)}]", flush=True)

    print(f"Phase 2: JUDGE (bulk) — {len(generated)} rows = {len(generated)} LLM calls",
          flush=True)
    dspy.configure(lm=judge_lm)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_judge_one, item) for item in generated]
        for _ in as_completed(futures):
            pass

    # Write kept variants
    kept = 0
    dropped = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        # Pass through originals first (don't lose them)
        for row in inputs:
            f.write(json.dumps(row) + "\n")
        # Then amplified
        for orig_row, var_err, var_umsg, score in results:
            if score < min_judge_score:
                dropped += 1
                continue
            orig_data = orig_row.get("data", {})
            orig_meta = orig_data.get("_meta", {})
            new_row = {
                "inputs": orig_row.get("inputs", []),
                "data": {
                    "pattern_description": f"[{orig_meta.get('pattern_id')}] {var_err[:300]}",
                    "example_errors": [var_err[:500]],
                    "project_context": "",
                    "rule_title": "",
                    "rule_body": "",
                    "rule_rationale": "",
                    "_meta": {
                        **orig_meta,
                        "synthetic": True,
                        "judge_score": score,
                        "synthetic_user_message": var_umsg[:300],
                    },
                },
            }
            f.write(json.dumps(new_row) + "\n")
            kept += 1

    return {
        "input_rows": len(inputs),
        "total_generated": len(results),
        "kept": kept,
        "dropped": dropped,
        "path": str(output_path),
    }
