"""Differential flow analysis — pair (failed, successful) flows of the same shape.

T1.V (PRD sio_backend_dead_loop_2026-05-15). The flow_events table holds
~125K rows where the same tool-call SEQUENCE sometimes succeeds and
sometimes fails. The differential signal — what's different about the
session context when the same sequence succeeds vs fails — is one of
the richest training signals SIO can extract WITHOUT a paid LLM step.

This module is the cheap data-prep layer. It:

1. Finds "twin" flow_hashes — sequences with ≥N successful AND ≥N failed
   occurrences (both cohorts exist in the data).
2. Pairs samples from each cohort into a JSONL with paired metadata
   (success row + failure row + the shared sequence).
3. Optionally produces a "positive examples" extract for
   ``src/sio/export/dataset_builder.py`` (T1.V.3) so DSPy datasets that
   currently say "0 positive / N negative" can finally get balanced.

The LLM-DELTA step (T1.V.2 — feed the pair to Gemini, ask "what's
different that makes success vs failure") is intentionally deferred —
it's the same shape as the distilabel work in PRD
synthetic_amplification_distilabel_2026-05-15.md.

USAGE
-----
    sio differential-flows --min-each 3 --output ~/.sio/differential.jsonl
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def find_twin_hashes(
    conn: sqlite3.Connection,
    min_success: int = 3,
    min_failure: int = 3,
) -> list[dict]:
    """Return flow_hashes that have BOTH success and failure events.

    Each result dict:
        {flow_hash, sequence, success_count, failure_count,
         success_rate, total}
    Sorted by total descending — most-frequent twin first.
    """
    rows = conn.execute(
        """
        SELECT flow_hash,
               MAX(sequence) AS sequence,
               SUM(was_successful)  AS s_count,
               SUM(1 - was_successful) AS f_count
        FROM flow_events
        GROUP BY flow_hash
        HAVING s_count >= ? AND f_count >= ?
        ORDER BY (s_count + f_count) DESC
        """,
        (min_success, min_failure),
    ).fetchall()

    out: list[dict] = []
    for fh, seq, sc, fc in rows:
        total = sc + fc
        out.append({
            "flow_hash": fh,
            "sequence": seq,
            "success_count": sc,
            "failure_count": fc,
            "total": total,
            "success_rate": sc / total if total else 0.0,
        })
    return out


def sample_pairs(
    conn: sqlite3.Connection,
    flow_hash: str,
    per_cohort: int = 3,
) -> list[dict]:
    """Sample up to N successful + N failed rows for a given flow_hash.

    Returns a flat list of row dicts annotated with ``outcome`` ('success'
    or 'failure') for downstream pair-building.
    """
    conn.row_factory = sqlite3.Row
    rows: list[dict] = []
    for outcome, predicate in (("success", 1), ("failure", 0)):
        cur = conn.execute(
            """
            SELECT session_id, sequence, ngram_size, was_successful,
                   duration_seconds, source_file, timestamp, file_path,
                   parent_session_id, is_subagent
            FROM flow_events
            WHERE flow_hash = ? AND was_successful = ?
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (flow_hash, predicate, per_cohort),
        )
        for r in cur.fetchall():
            d = dict(r)
            d["outcome"] = outcome
            rows.append(d)
    return rows


def export_pairs(
    db_path: str,
    output_path: Path,
    min_success: int = 3,
    min_failure: int = 3,
    per_cohort: int = 3,
    max_hashes: int | None = None,
) -> dict:
    """Run the twin finder + pair export. Returns summary dict.

    Output JSONL has one line per (flow_hash, paired_rows) group:

        {
          "flow_hash": "...",
          "sequence": "Bash+ -> Read.md+",
          "success_count": 46,
          "failure_count": 345,
          "samples": [
            {"outcome": "success", "session_id": "...", ...},
            {"outcome": "failure", "session_id": "...", ...},
            ...
          ]
        }
    """
    conn = sqlite3.connect(db_path)
    try:
        twins = find_twin_hashes(conn, min_success, min_failure)
        if max_hashes:
            twins = twins[:max_hashes]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with output_path.open("w") as f:
            for t in twins:
                samples = sample_pairs(conn, t["flow_hash"], per_cohort)
                row = {**t, "samples": samples}
                f.write(json.dumps(row) + "\n")
                written += 1
    finally:
        conn.close()

    return {
        "twin_hashes": len(twins),
        "rows_written": written,
        "path": str(output_path),
    }


def export_positives_for_dataset_builder(
    db_path: str,
    output_path: Path,
    min_success: int = 3,
    min_failure: int = 3,
    per_cohort: int = 5,
) -> dict:
    """T1.V.3 — emit ONLY the successful samples in a shape the existing
    ``src/sio/export/dataset_builder.py`` can append as positive examples.

    The output is a flat JSONL — one line per successful flow_event,
    annotated with the failure-side context as metadata so DSPy can use
    it as a contrastive signal during training.
    """
    conn = sqlite3.connect(db_path)
    try:
        twins = find_twin_hashes(conn, min_success, min_failure)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with output_path.open("w") as f:
            for t in twins:
                samples = sample_pairs(conn, t["flow_hash"], per_cohort)
                # Pull failure context once per twin (shared metadata)
                failures = [s for s in samples if s["outcome"] == "failure"]
                for s in samples:
                    if s["outcome"] != "success":
                        continue
                    record = {
                        "inputs": ["sequence", "flow_hash"],
                        "data": {
                            "sequence": t["sequence"],
                            "flow_hash": t["flow_hash"],
                            "outcome": "success",
                            "_meta": {
                                "twin_success_count": t["success_count"],
                                "twin_failure_count": t["failure_count"],
                                "session_id": s["session_id"],
                                "timestamp": s["timestamp"],
                                "contrastive_failure_session_ids": [
                                    fl["session_id"] for fl in failures[:3]
                                ],
                            },
                        },
                    }
                    f.write(json.dumps(record) + "\n")
                    written += 1
    finally:
        conn.close()
    return {
        "twins": len(twins),
        "positive_rows_written": written,
        "path": str(output_path),
    }
