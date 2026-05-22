"""LLM-based error classifier — the Router model from 2026-05-15 paired debate.

Replaces (or complements) the embedding-based pattern clusterer for the
``tool_failure`` slice of error_records.  See PRD
``sio_backend_dead_loop_2026-05-15.md`` and recipe ``R-AUDIT-DEBATE`` for
the full design rationale.

ROUTER MODEL (by error_type)
----------------------------
* ``repeated_attempt`` (75% of corpus, templated)
    Signature = ``tool_name`` directly. No LLM call. Pattern slug pattern:
    ``repeated_attempt__<tool>``.

* ``tool_failure`` (22% of corpus, real variance)
    Hash the normalised error_text, then LLM-classify the hash into one
    of the 13 fixed categories below. Pattern slug:
    ``tool_failure__<category>__<cycle>``.

* ``agent_admission`` / ``undo`` / ``user_correction`` (3% combined,
   60-73% singleton rate)
    Fall back to hash + LLM-classify until embeddings are wired.

The 13 categories are intentionally CLOSED — the LLM must choose one or
``Other``.  New categories require a code change here AND a re-run of the
backfill script.

PUBLIC API
----------
* ``classify_error(tool_name, error_text)`` — returns category string
* ``classify_batch(records, max_workers=8)`` — parallel batch classify
* ``signature_hash(text)`` — normalised hash for dedup
* ``CATEGORIES`` — the closed enum
* ``slug_for(error_type, tool_name, category)`` — pattern slug builder

USAGE
-----
``sio mine`` calls ``classify_error`` after extracting each
tool_failure record so new rows arrive with ``pattern_id`` already set.
``sio suggest`` / ``sio errors`` call ``classify_if_missing`` lazily to
catch any rows that slipped through (e.g. mined under an older version
of the pipeline).
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

# Public closed enum — keep in sync with the seeded patterns table.
CATEGORIES = (
    "PermissionDenied",
    "CascadeFailure",
    "ReadBeforeEdit",
    "ContentTooLarge",
    "FileNotFound",
    "NetworkTimeout",
    "AuthError",
    "RateLimited",
    "WrongArgument",
    "ToolApiError",
    "GenericExit",
    "StartupNoise",
    "Other",
)

CYCLE_ID = "manual_backfill_2026-05-15"

# Hash-normalisation rules (from the 2026-05-15 adversarial audit).
_NORM_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    ), "U"),
    (re.compile(r"0x[0-9a-fA-F]+"), "H"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[\.\d:Z+-]*"), "TS"),
    (re.compile(r"/[^\s\)\]\"',]+"), "P"),
    (re.compile(r":\d{2,5}\b"), "PT"),
    (re.compile(r"line \d+"), "line N"),
    (re.compile(r"\d{5,}"), "N5"),
    (re.compile(r"\d+"), "n"),
    (re.compile(r"\s+"), " "),
)


def _normalize(text: str) -> str:
    if not text:
        return ""
    out = text[:500]
    for pat, repl in _NORM_RULES:
        out = pat.sub(repl, out)
    return out.strip().lower()


def signature_hash(text: str) -> str:
    """Stable 16-char hash of the normalised error_text."""
    return hashlib.sha256(_normalize(text).encode()).hexdigest()[:16]


def slug_for(error_type: str, tool_name: str | None, category: str) -> str:
    """Build the pattern_id slug for an (error_type, tool, category) tuple."""
    et = (error_type or "unknown").lower()
    cat = (category or "Other").lower()
    if et == "repeated_attempt":
        # Tool name IS the signature for this slice.
        tool = (tool_name or "unknown").lower()
        return f"repeated_attempt__{tool}"
    return f"{et}__{cat}__{CYCLE_ID.split('_')[-1]}"


# --------------------------------------------------------------------------
# DSPy classifier (Flash) — lazy-initialised so importing this module is cheap.
# --------------------------------------------------------------------------

_classify_lock = threading.Lock()
_classify_callable = None


def _get_classifier():
    global _classify_callable
    if _classify_callable is not None:
        return _classify_callable
    with _classify_lock:
        if _classify_callable is not None:
            return _classify_callable

        import dspy  # noqa: PLC0415

        class _ClassifyError(dspy.Signature):
            """Classify a developer-tool error message into one fixed category.
            Return ONLY the category name from the allowed list."""

            tool_name: str = dspy.InputField(desc="Name of the tool that errored")
            error_text: str = dspy.InputField(desc="Error message (truncated to 200 chars)")
            category: str = dspy.OutputField(
                desc=(
                    f"MUST be one of: {', '.join(CATEGORIES)}. "
                    f"Choose the BEST fit. Default to 'Other' if no category fits."
                )
            )

        from sio.core.dspy.lm_factory import make_lm

        api_key = os.environ.get("SIO_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "classifier requires SIO_GEMINI_API_KEY (or GEMINI_API_KEY) in env. "
                "Source ~/.sio/secrets.env first."
            )
        # NOTE 2026-05-18: raised from 500 → 1000 per token-length audit.
        # Output is just one category string (~10 chars), but ChatAdapter
        # scaffolding ([[ ## category ## ]] sentinel) + Gemini-Flash
        # reasoning preamble can consume the 500-token budget BEFORE the
        # answer fires — same bug class as amplify's judge (commit 7f31ce3).
        # The classifier currently returns "Other" on failure (line 177)
        # which is silently lossy — every "Other" might be a real category
        # that got truncated. 1000 tokens of headroom kills that risk.
        lm = make_lm(
            "gemini/gemini-flash-latest",
            temperature=0.0,
            max_tokens=1000,
            api_key=api_key,
        )
        dspy.configure(lm=lm)
        _classify_callable = dspy.Predict(_ClassifyError)
    return _classify_callable


def classify_error(tool_name: str | None, error_text: str | None) -> str:
    """Classify ONE error into one of CATEGORIES. Returns 'Other' on failure."""
    if not error_text:
        return "Other"
    allowed = set(CATEGORIES)
    try:
        pred = _get_classifier()(
            tool_name=tool_name or "",
            error_text=(error_text or "")[:200],
        )
        cat = (pred.category or "").strip()
        if cat in allowed:
            return cat
        # Try to extract a known category from a wordy response.
        return next((c for c in CATEGORIES if c.lower() in cat.lower()), "Other")
    except Exception:
        return "Other"


def classify_batch(
    records: Iterable[dict],
    max_workers: int = 8,
) -> dict[int, str]:
    """Classify a batch of records in parallel.

    Each record must have keys: ``id``, ``tool_name``, ``error_text``.
    Returns ``{record_id: category_string}``.
    """
    out: dict[int, str] = {}
    lock = threading.Lock()

    def _one(rec):
        cat = classify_error(rec.get("tool_name"), rec.get("error_text"))
        with lock:
            out[rec["id"]] = cat

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_one, r) for r in records]
        for _ in as_completed(futures):
            pass
    return out


# ---------------------------------------------------------------------------
# Mining-pipeline integration
# ---------------------------------------------------------------------------


def tag_records(records: list[dict], max_workers: int = 8) -> None:
    """Mutate records in place, adding ``pattern_id`` to each dict.

    Called by the mining pipeline AFTER error extraction and BEFORE INSERT
    so each new row arrives with a pattern_id already set. Routes by
    ``error_type``:

    * ``repeated_attempt`` → deterministic slug from ``tool_name`` (no LLM)
    * ``tool_failure`` → batch LLM classify into the 13 closed categories
    * others (``agent_admission``, ``undo``, ``user_correction``) →
      deterministic ``<error_type>__unclassified`` slug; the lazy
      classifier in :func:`ensure_pattern_id` may upgrade them later.

    On classifier failure (auth issue, network), records without a slug
    get ``<error_type>__unclassified`` so the pipeline still completes;
    a later ``sio mine`` run will retry.
    """
    if not records:
        return

    deterministic_types = {"repeated_attempt", "agent_admission", "undo", "user_correction"}
    tool_failures: list[dict] = []
    for rec in records:
        et = (rec.get("error_type") or "").lower()
        if et == "repeated_attempt":
            rec["pattern_id"] = slug_for(et, rec.get("tool_name"), "Other")
        elif et in deterministic_types:
            rec["pattern_id"] = f"{et}__unclassified"
        elif et == "tool_failure":
            tool_failures.append(rec)
        else:
            rec["pattern_id"] = f"{et or 'unknown'}__unclassified"

    if tool_failures:
        try:
            results: dict[int, str] = {}
            lock = threading.Lock()

            def _one(rec):
                cat = classify_error(rec.get("tool_name"), rec.get("error_text"))
                with lock:
                    # Use idx (object id is unique within this list call) — records have no `id` yet
                    results[id(rec)] = cat

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(_one, r) for r in tool_failures]
                for _ in as_completed(futures):
                    pass
            for rec in tool_failures:
                cat = results.get(id(rec), "Other")
                rec["pattern_id"] = slug_for("tool_failure", rec.get("tool_name"), cat)
        except Exception:
            # Classifier unavailable (no API key, network) — leave as
            # unclassified so the pipeline completes; lazy mode will retry.
            for rec in tool_failures:
                rec.setdefault("pattern_id", "tool_failure__unclassified")


def ensure_pattern_id(conn, record_id: int) -> str | None:
    """Lazy classifier — call from query paths to backfill any NULL pattern_id.

    Reads the record, classifies if needed, UPDATEs the row, and returns
    the new pattern_id. Returns existing value if already set; returns
    None if record not found.
    """
    row = conn.execute(
        "SELECT id, tool_name, error_text, error_type, pattern_id "
        "FROM error_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        return None
    # sqlite3.Row supports key access
    existing = row["pattern_id"] if hasattr(row, "keys") else row[4]
    if existing:
        return existing
    et = (row["error_type"] or "").lower()
    tool = row["tool_name"]
    text = row["error_text"]
    if et == "repeated_attempt":
        slug = slug_for(et, tool, "Other")
    elif et == "tool_failure":
        cat = classify_error(tool, text)
        slug = slug_for(et, tool, cat)
    else:
        slug = f"{et or 'unknown'}__unclassified"
    conn.execute(
        "UPDATE error_records SET pattern_id = ? WHERE id = ?",
        (slug, record_id),
    )
    conn.commit()
    return slug
