"""Hop-2 refinement helper shared by ``sio suggest`` and ``sio search``.

Extracted from ``src/sio/cli/main.py:2207–2331`` so that the two-hop cascade
is reachable from both the ``suggest`` and ``search`` pipelines without
duplicating logic.  The ``suggest`` command continues to call this module;
``search`` gains the same capability by importing it directly.

Public API
----------
apply_hop2_filter(errors, refine_terms, strategy, recluster_threshold)
    Core Hop-2 logic.  Accepts a list of error record dicts (the Hop-1
    result) and returns a narrowed list, applying the requested strategy:

    * ``filter``    — pre-cluster narrowing: keep only errors whose text
                      contains at least one refine term (OR logic).  Fast,
                      no embeddings.  Does NOT call cluster_errors().
    * ``recluster`` — theme-aware decomposition: cluster the full Hop-1 set,
                      select patterns whose description/samples match the
                      refine terms, collect their underlying errors, then
                      re-cluster with a tighter threshold.  Calls
                      cluster_errors() twice.
    * ``hybrid``    — filter first, then recluster the survivors.  Calls
                      cluster_errors() once on the pre-filtered set.

load_errors_from_csv(csv_path)
    Read a Hop-1 preview CSV (written by ``sio suggest --preview``) back
    into a list of error record dicts.  Used by ``--within``/``--use-cache``
    to skip the DB round-trip on Hop-2 invocations.

build_noise_hint(hop1_count, noise_threshold, pattern)
    Return a concrete Hop-2 suggestion string when ``hop1_count`` exceeds
    ``noise_threshold``, or ``None`` when the result set is already focused.
    The hint is non-blocking — callers emit it to stderr and continue.

Design notes
------------
* ``cluster_errors()`` is imported from ``sio.clustering.pattern_clusterer``
  at call time (lazy import) so the module loads without the fastembed stack
  being present (test-friendly, faster import path for filter-only calls).
* Error record dicts use the same schema as ``get_error_records()`` rows:
  ``id``, ``error_type``, ``error_text``, ``tool_name``, ``session_id``,
  ``timestamp``, ``source_file``, ``user_message``, ``context_before``,
  ``context_after``.  The CSV loader populates all of these.
* The ``recluster`` and ``hybrid`` strategies return **error dicts**, not
  cluster pattern dicts, so callers always deal with a uniform list type.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Sequence

# cluster_errors is imported at module level so patch("sio.clustering.hop2.cluster_errors")
# works in tests.  The fastembed stack loads on first call, not on import of this module.
from sio.clustering.pattern_clusterer import cluster_errors

# Fields searched for term matches (identical to suggest cascade in main.py:2221-2231).
_SEARCHABLE_FIELDS = (
    "error_text",
    "user_message",
    "context_before",
    "context_after",
    "source_file",
)

# Default noise threshold: when a Hop-1 result set exceeds this count the
# caller may emit a Hop-2 suggestion.  Configurable via CLI --noise-threshold.
DEFAULT_NOISE_THRESHOLD: int = 20


# ---------------------------------------------------------------------------
# Core predicate — mirrors ``_hop2_matches`` in main.py:2218–2233
# ---------------------------------------------------------------------------


def _hop2_matches(error: dict, refine_terms: Sequence[str]) -> bool:
    """Return True if *error* contains at least one refine term (OR logic).

    Mirrors ``_hop2_matches`` in ``src/sio/cli/main.py:2218–2233`` exactly so
    both callers apply the same filter semantics.
    """
    if not refine_terms:
        return True
    for field in _SEARCHABLE_FIELDS:
        val = (error.get(field) or "").lower()
        for term in refine_terms:
            if term in val:
                return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_hop2_filter(
    errors: list[dict],
    refine_terms: Sequence[str],
    strategy: str = "filter",
    recluster_threshold: float = 0.85,
) -> list[dict]:
    """Narrow *errors* using the Hop-2 cascade strategy.

    Parameters
    ----------
    errors:
        Hop-1 result — a list of error record dicts.
    refine_terms:
        Second-hop AND-filter terms (OR within the list, AND with Hop-1).
        Pass ``[]`` to return all errors unchanged.
    strategy:
        One of ``"filter"``, ``"recluster"``, ``"hybrid"``.
    recluster_threshold:
        Cosine-similarity threshold for the tighter second clustering pass
        (``recluster`` / ``hybrid`` strategies only).  Mirrors the default in
        ``main.py:1943–1951``.

    Returns
    -------
    list[dict]
        Narrowed list of error record dicts (never pattern dicts).
    """
    if not refine_terms:
        return list(errors)

    strategy = strategy.lower()

    # ------------------------------------------------------------------
    # filter: pre-cluster narrowing — keep errors matching any refine term.
    # Fast, no embeddings.  Does NOT call cluster_errors().
    # (mirrors main.py:2236–2238)
    # ------------------------------------------------------------------
    if strategy == "filter":
        return [e for e in errors if _hop2_matches(e, refine_terms)]

    # ------------------------------------------------------------------
    # recluster: cluster the full set, find theme-matching patterns, collect
    # their error IDs, re-cluster with tighter threshold.
    # (mirrors main.py:2289–2331)
    # ------------------------------------------------------------------
    if strategy == "recluster":
        return _recluster(errors, refine_terms, recluster_threshold)

    # ------------------------------------------------------------------
    # hybrid: pre-filter, then recluster the survivors.
    # (mirrors main.py:2236 pre-cluster filter + 2289+ recluster)
    # ------------------------------------------------------------------
    if strategy == "hybrid":
        pre_filtered = [e for e in errors if _hop2_matches(e, refine_terms)]
        if len(pre_filtered) < 2:
            return pre_filtered
        return _recluster(pre_filtered, refine_terms, recluster_threshold)

    # Unknown strategy — fall back to filter.
    return [e for e in errors if _hop2_matches(e, refine_terms)]


def _recluster(
    errors: list[dict],
    refine_terms: Sequence[str],
    threshold: float,
) -> list[dict]:
    """Re-cluster *errors* with a tighter threshold and return error dicts.

    Implements the ``recluster`` branch from ``main.py:2289–2331``:
    1. Build an index of errors by ID.
    2. Cluster the full input set with the tighter *threshold*.
    3. Select patterns whose description or sample errors match the refine terms.
    4. Collect underlying error dicts for those patterns.
    5. If fewer than 2 errors match, fall back to plain pattern-filter behaviour
       (return the errors that do match, without a second clustering pass).
    """
    error_index = {e.get("id"): e for e in errors}

    def _pattern_matches(p: dict) -> bool:
        desc = (p.get("description") or "").lower()
        for term in refine_terms:
            if term in desc:
                return True
        for eid in p.get("error_ids", []):
            e = error_index.get(eid)
            if e and _hop2_matches(e, refine_terms):
                return True
        return False

    patterns = cluster_errors(errors, threshold=threshold)

    matching_patterns = [p for p in patterns if _pattern_matches(p)]

    matching_eids: set = set()
    for p in matching_patterns:
        for eid in p.get("error_ids", []):
            matching_eids.add(eid)

    matching_errors = [error_index[eid] for eid in matching_eids if eid in error_index]

    if len(matching_errors) < 2:
        # Fallback: plain-filter (mirrors main.py:2314–2320).
        return [e for e in errors if _hop2_matches(e, refine_terms)]

    # Re-cluster the theme-coherent subset (mirrors main.py:2322–2325).
    sub_patterns = cluster_errors(matching_errors, threshold=threshold)
    result_eids: set = set()
    for p in sub_patterns:
        for eid in p.get("error_ids", []):
            result_eids.add(eid)

    return [error_index[eid] for eid in result_eids if eid in error_index]


def load_errors_from_csv(csv_path: str | Path) -> list[dict]:
    """Load a Hop-1 preview CSV into a list of error record dicts.

    Mirrors the CSV-loading block in ``main.py:2082–2134`` so both ``suggest``
    and ``search`` can reuse the same cache-reading logic.

    The CSV is expected to have been written by ``sio suggest --preview``; the
    required columns are the same that ``get_error_records()`` returns.  Missing
    optional columns default to empty strings.
    """
    csv_abs = os.path.expanduser(str(csv_path))
    loaded: list[dict] = []
    with open(csv_abs, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_id = row.get("id", "")
            loaded.append(
                {
                    "id": int(raw_id) if (raw_id or "").isdigit() else raw_id,
                    "error_type": row.get("error_type") or "",
                    "error_text": row.get("error_text") or "",
                    "tool_name": row.get("tool_name") or "",
                    "session_id": row.get("session_id") or "",
                    "timestamp": row.get("timestamp") or "",
                    "source_file": row.get("source_file") or "",
                    "user_message": row.get("user_message") or "",
                    # context fields are truncated in the preview CSV; set to ""
                    # so Hop-2 filtering is field-consistent with the DB path.
                    "context_before": "",
                    "context_after": "",
                }
            )
    return loaded


def build_noise_hint(
    hop1_count: int,
    noise_threshold: int,
    pattern: str,
) -> str | None:
    """Return a concrete Hop-2 refine command when Hop-1 is noisy.

    Returns ``None`` when the result set is already focused (count ≤ threshold)
    so the caller can emit it only when actionable.  The hint is non-blocking —
    the caller prints it to stderr and continues (FR-006).

    Parameters
    ----------
    hop1_count:
        Number of matches returned by the first-hop search.
    noise_threshold:
        Minimum count (exclusive) above which the set is considered "noisy".
    pattern:
        The original search pattern (included in the hint for copy-paste ease).
    """
    if hop1_count <= noise_threshold:
        return None
    return (
        f"# Hop-2 suggestion: {hop1_count} matches — consider narrowing with:\n"
        f"#   sio search \"{pattern}\" --refine \"<narrowing-term>\" --strategy filter\n"
        f"# Or re-run with the error DB cascade:\n"
        f"#   sio suggest --grep \"{pattern}\" --refine \"<narrowing-term>\""
    )
