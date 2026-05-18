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
        # Slightly higher temperature for variant generation (creativity).
        # NOTE 2026-05-18: raised from 4000 → 6000 per token-length audit.
        # 10 variants × ~400 chars (~100 tokens) = 1000 raw + ChatAdapter
        # scaffolding ([[ ## variants_json ## ]] + [[ ## completed ## ]] +
        # field separators) + Gemini-Flash reasoning preamble can push past
        # 4000 for content-heavy patterns. 6000 provides headroom for
        # n_per_row up to ~15 without truncation.
        _gen_lm = dspy.LM(
            model="gemini/gemini-flash-latest",
            api_key=api_key,
            temperature=0.8,
            max_tokens=6000,
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

        DOMAIN PRESERVATION (origin 2026-05-18 paired-debate Step 2):
        If `domain_keywords` is non-empty, AT LEAST 60% of variants MUST
        keep at least ONE domain keyword somewhere in error_text. Drift
        to generic surrogates (salesforce → s3 → bigquery) when the
        source was an HH-domain athenahealth/dbt/cube error is FORBIDDEN
        unless explicitly varied. Domain mode-collapse was the failure
        mode of trainset id=10 — variants stayed category-faithful but
        lost HH vocabulary; the optimizer then had no domain signal.
        Surface VARIETY within the domain is encouraged (different HH
        tables, different dbt models, different chart names) — the
        constraint is on domain ABANDONMENT, not domain repetition.
        """

        original_pattern_id: str = dspy.InputField(
            desc="The category we MUST preserve, e.g. tool_failure__filenotfound"
        )
        original_error_text: str = dspy.InputField()
        original_tool_name: str = dspy.InputField()
        n_variants: int = dspy.InputField(desc="How many variants to produce")
        domain_keywords: str = dspy.InputField(
            desc=(
                "Comma-separated keywords from the source domain that "
                "AT LEAST 60% of variants must preserve. Empty string "
                "means no domain constraint (cross-domain variants OK). "
                "Example: 'athenahealth,dbt,databricks,cube,zeno'."
            )
        )
        variants_json: str = dspy.OutputField(
            desc=(
                "JSON array, length = n_variants. Each item: "
                '{"error_text": "...", "user_message": "...(contains !! or ??)..."}. '
                "ONLY the JSON. No code fences, no commentary."
            )
        )

    class JudgeVariants(dspy.Signature):
        """Score each variant 0.0-1.0 using a 5-tier rubric for FAILURE-MODE
        preservation + DOMAIN-fidelity against the target pattern_id.

        ⚠️ RUBRIC ANCHORS — calibrate to these (origin 2026-05-18 paired-debate
        Step 3, "Synthetic Extremes" pattern). Today's binary judge collapsed
        all kept variants to 1.0 with no spread. The rubric below FORCES
        graded scores that downstream optimizers can rank.

        SCORE 1.0 — GOLD
          Failure mode matches pattern_id exactly; surface features differ
          appropriately (different paths/tools/lines but same root cause).
          Domain vocabulary preserved when domain_keywords was non-empty.
          Example: pattern_id=tool_failure__permissiondenied AND variant says
          "Permission denied: cannot write to /etc/hh-dev/config" — perfect.

        SCORE 0.7 — SILVER
          Failure mode matches BUT domain drifted to generic surrogate
          (e.g. source was HH/athenahealth and variant uses
          generic-salesforce/s3/bigquery instead). Category-faithful,
          domain-diluted. Useful but mediocre — flag in score, don't drop.

        SCORE 0.5 — BRONZE
          Failure mode is CLOSE (sibling category) — e.g. judging
          tool_failure__filenotfound and variant is
          tool_failure__readbeforeedit. Both 'string not found' but
          different root mechanisms. Useful for diversity, low for
          category precision.

        SCORE 0.2 — DRIFT
          Failure mode drifted to a different category entirely
          (e.g. judging permission_denied, variant is network_timeout).
          Not useful for category training. Drop unless rescuing for
          a different category.

        SCORE 0.0 — HALLUCINATION
          Variant is malformed, contains injected instructions
          ("ignore all previous"), is non-English when source was English,
          or appears truncated. Always drop. THIS IS A HALLUCINATION
          ANCHOR SMOKE TEST — if your scoring never assigns 0.0 to
          such variants, your calibration is broken.

        SURFACE FEATURES — specific file paths, tool names, command lines,
        line numbers, error message wording — are EXPECTED TO DIFFER and
        MUST NOT lower the score by themselves.

        Output `scores_json` is a JSON list of floats matching the order
        of `variants_json`. PRODUCE GRADED SCORES — using only 1.0 and
        <0.6 will be detected as a calibration failure downstream.
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
        domain_keywords: str = dspy.InputField(
            desc=(
                "Comma-separated domain keywords from the source. "
                "If non-empty, variants that DROP all domain keywords "
                "should score 0.7 (silver) not 1.0 (gold), even if "
                "category is preserved. Empty string means no domain "
                "fidelity expected — score on category only."
            )
        )
        variants_json: str = dspy.InputField(
            desc='JSON array of variants — each item {"error_text": "..."}'
        )
        scores_json: str = dspy.OutputField(
            desc=(
                "JSON array of floats (0.0-1.0) matching variants_json "
                "order. Use the FULL rubric — 1.0/0.7/0.5/0.2/0.0 are "
                "the anchor points. Intermediate values OK. "
                "If you score everything 1.0 you have failed the "
                "calibration. ONLY the JSON."
            )
        )

    gen = dspy.Predict(GenerateVariants)
    judge = dspy.Predict(JudgeVariants)
    return gen, judge


# ---------------------------------------------------------------------------
# Step 2 (2026-05-18): Domain keyword extraction for generator preservation
# ---------------------------------------------------------------------------

# HH-domain lexicon — the keywords that distinguish HH errors from generic
# pipeline errors. If a source error mentions any of these, the generator
# should preserve at least one across the variants instead of drifting to
# generic s3/salesforce/bigquery surrogates (mode collapse observed in
# trainset id=10).
_HH_DOMAIN_LEXICON = frozenset({
    # Healthcare-data platform stack
    "athenahealth", "athena", "athenaone", "dbt", "databricks", "snowflake",
    "cube", "cubejs", "zeno", "supabase",
    # HH-specific projects
    "hhdev", "hh-dev", "cdia", "bas-2", "ccm", "careplan", "raf", "hcc",
    # HH clinical domains
    "tcm", "awv", "medicare", "advantage", "attribution", "membership",
    "behavioral", "scheduling",
    # HH data platform paths
    "dbw_hertek_prod", "dev_gyasi", "h_exp", "report_dev", "twice", "herself",
})

# Generic developer-tooling jargon worth preserving when no HH terms present.
_GENERIC_TECH_LEXICON = frozenset({
    "kubernetes", "k8s", "docker", "terraform", "ansible", "jenkins",
    "airflow", "kafka", "spark", "redis", "postgres", "mysql",
    "react", "next", "vite", "webpack", "npm", "pnpm",
    "python", "node", "rust", "golang",
})


def _filter_by_diversity(
    kept_variants: list[tuple[dict, str, str, float]],
    similarity_threshold: float = 0.95,
) -> tuple[list[tuple[dict, str, str, float]], int]:
    """Step 4 (2026-05-18 paired-debate): Drop near-duplicates by cosine similarity.

    For each pair of variants belonging to the SAME source row whose
    embedded error_text cosine similarity > threshold, keep only the
    one with the higher judge_score. Variants from different source
    rows are never compared (they're allowed to overlap — that's the
    job of the optimizer to dedupe at training time).

    Why per-source-row and not global: a generic "permission denied"
    error from two different patterns SHOULD coexist; that's legitimate
    cross-pattern signal. Mode collapse is when ONE source row's 10
    variants are 9 copies of the same thing.

    Returns:
        (filtered_list, dropped_count)

    Args:
        kept_variants: list of (orig_row, error_text, user_message, score)
        similarity_threshold: cosine similarity above which we dedupe
            (default 0.95 — permissive; tighten if optimizer needs more spread)
    """
    if len(kept_variants) < 2:
        return kept_variants, 0
    try:
        from sio.core.embeddings.local_model import FastEmbedBackend  # noqa: PLC0415
        backend = FastEmbedBackend()
    except Exception as exc:
        import sys as _sys  # noqa: PLC0415
        print(
            f"  [DIVERSITY_FILTER_SKIP] FastEmbed unavailable: "
            f"{type(exc).__name__}: {exc} — diversity filter disabled.",
            file=_sys.stderr, flush=True,
        )
        return kept_variants, 0

    # Group by source row identity (object id is fine; we're not pickling)
    from collections import defaultdict  # noqa: PLC0415
    groups = defaultdict(list)
    for i, (row, et, um, s) in enumerate(kept_variants):
        groups[id(row)].append(i)

    drop_indices: set[int] = set()
    for _row_id, idx_list in groups.items():
        if len(idx_list) < 2:
            continue
        texts = [kept_variants[i][1][:400] for i in idx_list]
        try:
            embeddings = backend.encode(texts)
        except Exception:
            continue
        # Normalize for cosine
        import numpy as _np  # noqa: PLC0415
        norms = _np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-9
        unit = embeddings / norms
        sim = unit @ unit.T
        # Greedy: walk pairs; if sim > threshold, drop the LOWER-score one
        n = len(idx_list)
        for a in range(n):
            if idx_list[a] in drop_indices:
                continue
            for b in range(a + 1, n):
                if idx_list[b] in drop_indices:
                    continue
                if sim[a, b] >= similarity_threshold:
                    sa = kept_variants[idx_list[a]][3]
                    sb = kept_variants[idx_list[b]][3]
                    # Drop the lower-score one; tiebreak: drop later index
                    drop = idx_list[b] if sa >= sb else idx_list[a]
                    drop_indices.add(drop)

    if drop_indices:
        import sys as _sys  # noqa: PLC0415
        print(
            f"  [DIVERSITY_FILTER] Dropped {len(drop_indices)} near-duplicate "
            f"variants (cosine ≥ {similarity_threshold}) across "
            f"{len(groups)} source rows.",
            file=_sys.stderr, flush=True,
        )

    filtered = [v for i, v in enumerate(kept_variants) if i not in drop_indices]
    try:
        backend.close()
    except Exception:
        pass
    return filtered, len(drop_indices)


def _extract_domain_keywords(error_text: str, tool_name: str) -> str:
    """Scan source error_text + tool_name for known domain keywords.

    Returns comma-separated keywords (lowercase, unique, up to 5) that
    the generator should preserve in at least 60% of variants. Empty
    string means no domain constraint — cross-domain variants allowed.

    HH lexicon wins over generic if both match. This biases toward
    preserving healthcare-data context when present, which was the
    failure mode in trainset id=10.
    """
    if not error_text and not tool_name:
        return ""
    haystack = f"{error_text} {tool_name}".lower()
    hits_hh = [kw for kw in _HH_DOMAIN_LEXICON if kw in haystack]
    if hits_hh:
        # Dedupe + cap at 5 to keep prompt overhead tiny
        seen, out = set(), []
        for kw in hits_hh:
            if kw not in seen:
                seen.add(kw)
                out.append(kw)
                if len(out) >= 5:
                    break
        return ",".join(out)
    hits_generic = [kw for kw in _GENERIC_TECH_LEXICON if kw in haystack]
    if hits_generic:
        return ",".join(hits_generic[:5])
    return ""


# ---------------------------------------------------------------------------
# Top-level amplify
# ---------------------------------------------------------------------------


def amplify(
    input_path: Path,
    output_path: Path,
    n_per_row: int = 10,
    min_judge_score: float = 0.6,
    max_workers: int = 4,
    diversity_filter: bool = True,
    diversity_threshold: float = 0.95,
) -> dict:
    """Amplify a curated JSONL by generating N variants per row.

    Args:
        input_path: JSONL from ``sio curate`` (canonical PatternToRule shape).
        output_path: where to write the amplified JSONL.
        n_per_row: variants to generate per input row.
        min_judge_score: drop variants below this judge score (0.0-1.0).
        max_workers: thread-pool parallelism for the LLM calls.
        diversity_filter: if True, drop near-duplicate variants within
            each source row using cosine similarity on fastembed
            (Step 4 of 2026-05-18 paired-debate framework).
        diversity_threshold: cosine similarity above which duplicates
            are dropped (default 0.95; permissive).

    Returns:
        Dict with counts: input_rows, total_generated, kept, dropped,
            diversity_dropped, path.
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
            # Step 2 (2026-05-18 paired-debate): auto-extract domain keywords
            # from the source so generator preserves domain vocabulary.
            # See _extract_domain_keywords() for the lexicon (HH-specific
            # plus a generic "common-jargon" fallback).
            domain_kw = _extract_domain_keywords(error_text, tool_name)

            out = _retry_429(
                gen,
                original_pattern_id=pattern_id,
                original_error_text=error_text,
                original_tool_name=tool_name,
                n_variants=n_per_row,
                domain_keywords=domain_kw,
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
            orig_tool = meta.get("tool_name") or "unknown"
            # Step 3 (2026-05-18 paired-debate): pass domain_keywords so the
            # judge can penalize domain-dilution variants (silver 0.7 instead
            # of gold 1.0). Same keyword extractor as the generator, so the
            # judge knows what the generator was TOLD to preserve.
            domain_kw = _extract_domain_keywords(orig_err, orig_tool)

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
                domain_keywords=domain_kw,
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

    # Phase 2.5 (Step 4, 2026-05-18 paired-debate): diversity filter
    diversity_dropped = 0
    if diversity_filter and results:
        # Only run diversity filter on variants that would PASS the
        # min_judge_score threshold — wasteful to embed about-to-drop rows
        above = [r for r in results if r[3] >= min_judge_score]
        below = [r for r in results if r[3] < min_judge_score]
        print(f"Phase 2.5: DIVERSITY FILTER on {len(above)} above-threshold variants "
              f"(cosine ≥ {diversity_threshold})",
              flush=True)
        filtered_above, diversity_dropped = _filter_by_diversity(
            above, similarity_threshold=diversity_threshold
        )
        results = filtered_above + below

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

    # Step 3 (2026-05-18 paired-debate): CALIBRATION HEALTH CHECK.
    # If the rubric is working, kept scores should show spread across
    # the 1.0/0.7/0.5 anchor tiers. If everything is still 1.0, the
    # judge is binary-mode-collapsed and we need Tier 2 (DSPy meta-opt).
    # LOUD warning per Article XIII so the operator sees it inline.
    try:
        kept_scores = [s for _, _, _, s in results if s >= min_judge_score]
        if kept_scores:
            unique_buckets = {round(s, 1) for s in kept_scores}
            pct_gold = sum(1 for s in kept_scores if s >= 0.95) / len(kept_scores)
            if len(unique_buckets) <= 2 or pct_gold >= 0.95:
                import sys as _sys  # noqa: PLC0415
                print(
                    f"\n  [JUDGE_CALIBRATION_WARN] Bimodal score distribution "
                    f"detected — {len(unique_buckets)} unique 0.1-buckets, "
                    f"{pct_gold * 100:.0f}% scored 1.0. Rubric anchors did "
                    f"NOT produce spread. Likely causes: (a) source data "
                    f"truly is uniformly high-quality, (b) Flash cannot "
                    f"discriminate at this resolution, (c) rubric prompt "
                    f"needs strengthening. Consider Step 5b: meta-optimize "
                    f"the judge via DSPy + Silver Standard (gpt-5, NOT "
                    f"gpt-4o per cost-control rule).",
                    file=_sys.stderr, flush=True,
                )
            else:
                # Healthy spread — affirm so the operator sees the signal too
                import sys as _sys  # noqa: PLC0415
                print(
                    f"  [JUDGE_CALIBRATION_OK] Score spread healthy: "
                    f"{len(unique_buckets)} buckets, gold={pct_gold * 100:.0f}%, "
                    f"min={min(kept_scores):.2f} max={max(kept_scores):.2f}.",
                    file=_sys.stderr, flush=True,
                )
    except Exception:
        pass  # observability — never crash

    return {
        "input_rows": len(inputs),
        "total_generated": len(results) + diversity_dropped,
        "kept": kept,
        "dropped": dropped,
        "diversity_dropped": diversity_dropped,
        "path": str(output_path),
    }
