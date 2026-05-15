"""sio.analyze — read-only diagnostics over the mined corpus.

Implements ``sio analyze same-error`` (PRD T1.5.6): answer
"which exact error signatures appeared ≥N times across sessions" along
with surrounding context. The unit of analysis is the
``signature_hash`` (normalised error_text) — the same hash the curate
filter uses, except this command focuses on REPETITION across sessions
rather than amplification of a single insight.

Sample query:

    sio analyze same-error --min-count 3 --since "30 days"

Output is a table:

    | hash | count | sessions | tools | first_seen | last_seen | sample |

Plus optional ``--with-context`` to print the agent's ``context_before``
right before each occurrence — the cognitive-failure pattern is rarely
about the error itself; it's about what the agent was trying to do.

This is a read-only command. No writes, no LLM calls.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Same normalisation rules as sio.clustering.classifier — shared so the
# signature hash here matches what the curate filter would produce.
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


def _sig(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()[:16]


def _parse_since(since: str) -> str:
    s = since.strip().lower()
    if " " in s:
        n_str, unit = s.split(maxsplit=1)
        try:
            n = int(n_str)
        except ValueError:
            return since
        if unit.startswith("day"):
            return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()
        if unit.startswith("hour"):
            return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()
        if unit.startswith("week"):
            return (datetime.now(timezone.utc) - timedelta(weeks=n)).isoformat()
    return since


def same_error_analysis(
    db_path: str,
    min_count: int = 3,
    since: str | None = None,
    limit: int = 50,
    with_context: bool = False,
) -> list[dict]:
    """Run the same-error analysis. Returns ordered list of finding dicts.

    Each finding dict:
        signature_hash, count, sessions (set), tools (Counter),
        first_seen, last_seen, sample_error, contexts (list[str]) when
        with_context is True.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    where = ["1=1"]
    params: list = []
    if since:
        where.append("timestamp >= ?")
        params.append(_parse_since(since))
    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"SELECT id, session_id, timestamp, tool_name, error_text, "
        f"context_before, error_type "
        f"FROM error_records WHERE {where_sql}",
        params,
    ).fetchall()
    conn.close()

    # Group by signature
    sig_count: Counter = Counter()
    sig_sessions: dict[str, set[str]] = defaultdict(set)
    sig_tools: dict[str, Counter] = defaultdict(Counter)
    sig_first: dict[str, str] = {}
    sig_last: dict[str, str] = {}
    sig_sample: dict[str, str] = {}
    sig_contexts: dict[str, list[str]] = defaultdict(list)
    sig_types: dict[str, Counter] = defaultdict(Counter)

    for r in rows:
        h = _sig(r["error_text"] or "")
        sig_count[h] += 1
        sig_sessions[h].add(r["session_id"] or "")
        sig_tools[h][r["tool_name"] or ""] += 1
        sig_types[h][r["error_type"] or ""] += 1
        ts = r["timestamp"] or ""
        if h not in sig_first or ts < sig_first[h]:
            sig_first[h] = ts
        if h not in sig_last or ts > sig_last[h]:
            sig_last[h] = ts
        if h not in sig_sample:
            sig_sample[h] = (r["error_text"] or "")[:200]
        if with_context and r["context_before"]:
            if len(sig_contexts[h]) < 3:
                sig_contexts[h].append((r["context_before"] or "")[:200])

    # Order by count desc, filter by min_count, cap at limit
    findings = []
    for h, c in sig_count.most_common():
        if c < min_count:
            continue
        findings.append({
            "signature_hash": h,
            "count": c,
            "session_count": len(sig_sessions[h]),
            "tools": dict(sig_tools[h].most_common(3)),
            "first_seen": sig_first[h],
            "last_seen": sig_last[h],
            "sample_error": sig_sample[h],
            "error_types": dict(sig_types[h].most_common(3)),
            "contexts": sig_contexts[h] if with_context else [],
        })
        if len(findings) >= limit:
            break
    return findings
