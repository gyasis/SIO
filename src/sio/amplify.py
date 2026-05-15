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
            max_tokens=600,
        )
        # Low temperature for judging (consistency)
        _judge_lm = dspy.LM(
            model="gemini/gemini-flash-latest",
            api_key=api_key,
            temperature=0.0,
            max_tokens=200,
        )
    return _gen_lm, _judge_lm


# ---------------------------------------------------------------------------
# DSPy signatures
# ---------------------------------------------------------------------------


def _make_modules():
    """Build the generator + judge DSPy modules. Called once per amplify run."""
    import dspy  # noqa: PLC0415

    class GenerateVariant(dspy.Signature):
        """Generate a SINGLE realistic variant of a developer-tool error.

        Preserve the underlying pattern_id category. Vary surface features:
        file path, tool name (where plausible), specific error wording, and
        the user_message phrasing. The variant must be a believable error
        of the same category — not a random different error.
        """

        original_pattern_id: str = dspy.InputField(
            desc="The category we MUST preserve, e.g. tool_failure__filenotfound"
        )
        original_error_text: str = dspy.InputField()
        original_user_message: str = dspy.InputField(
            desc="The frustration-marked user message preceding the error"
        )
        original_tool_name: str = dspy.InputField()
        variant_index: int = dspy.InputField(desc="Variant number (1, 2, ...) — for variety")
        variant_error_text: str = dspy.OutputField(
            desc=(
                "A different but category-equivalent error message. "
                "Preserve the failure mode; change paths, tool names, "
                "specific identifiers."
            )
        )
        variant_user_message: str = dspy.OutputField(
            desc=(
                "A different user_message preceding the error, with frustration "
                "tone preserved (!! or ?? somewhere). Different phrasing, same intent."
            )
        )

    class JudgeVariant(dspy.Signature):
        """Judge whether a variant preserves the original pattern category.

        Return a float 0.0-1.0. 1.0 = perfect category preservation.
        0.0 = variant is a completely different kind of error.
        0.5 = borderline (variant could fit either category).
        """

        original_pattern_id: str = dspy.InputField()
        original_error_text: str = dspy.InputField()
        variant_error_text: str = dspy.InputField()
        score: str = dspy.OutputField(
            desc="A single number from 0.0 to 1.0 (just the number, no explanation)"
        )

    gen = dspy.Predict(GenerateVariant)
    judge = dspy.Predict(JudgeVariant)
    return gen, judge


# ---------------------------------------------------------------------------
# Top-level amplify
# ---------------------------------------------------------------------------


def amplify(
    input_path: Path,
    output_path: Path,
    n_per_row: int = 10,
    min_judge_score: float = 0.6,
    max_workers: int = 8,
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

    gen_lm, judge_lm = _get_lms()
    dspy.configure(lm=gen_lm)
    gen, judge = _make_modules()

    # Collect work units: (input_row, variant_idx)
    work: list[tuple[dict, int]] = []
    for row in inputs:
        for i in range(1, n_per_row + 1):
            work.append((row, i))

    results: list[tuple[dict, str, str, float]] = []  # (row, var_err, var_umsg, score)
    lock = threading.Lock()
    done = [0]

    def _gen_one(row: dict, var_idx: int):
        try:
            data = row.get("data", {})
            meta = data.get("_meta", {})
            pattern_id = meta.get("pattern_id") or "tool_failure__unclassified"
            error_text = (data.get("example_errors", [""])[0] or "")[:400]
            user_message = ""  # not stored in canonical PatternToRule shape
            tool_name = meta.get("tool_name") or "unknown"

            # 1) Generate variant
            dspy.configure(lm=gen_lm)
            gen_out = gen(
                original_pattern_id=pattern_id,
                original_error_text=error_text,
                original_user_message=user_message,
                original_tool_name=tool_name,
                variant_index=var_idx,
            )
            var_err = (gen_out.variant_error_text or "").strip()
            var_umsg = (gen_out.variant_user_message or "").strip()
            if not var_err:
                return

            # 2) Judge
            dspy.configure(lm=judge_lm)
            j_out = judge(
                original_pattern_id=pattern_id,
                original_error_text=error_text,
                variant_error_text=var_err,
            )
            try:
                score = float((j_out.score or "0.0").strip().split()[0])
            except Exception:
                score = 0.0

            with lock:
                results.append((row, var_err, var_umsg, score))
        except Exception:
            return
        finally:
            with lock:
                done[0] += 1
                if done[0] % 50 == 0:
                    print(f"  [{done[0]}/{len(work)}] processed", flush=True)

    print(f"Amplifying {len(inputs)} rows × {n_per_row} = {len(work)} variants...",
          flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_gen_one, row, idx) for row, idx in work]
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
