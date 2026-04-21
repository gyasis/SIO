"""Skill candidate discovery — finds patterns worth promoting to skills.

Discovers potential skill candidates by cross-referencing error patterns
with positive flow events. Identifies three types of candidates:

- **tool-specific**: Patterns concentrated on a single tool (e.g. "Edit safety")
- **workflow-sequence**: Recurring multi-tool flows (e.g. "Read -> Edit -> Test")
- **repo-specific**: Patterns that appear only in a specific project path

Public API
----------
    discover_skill_candidates(db, repo_path=".") -> list[dict]
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter

logger = logging.getLogger(__name__)


def _extract_extensions_from_context(context: str | None) -> list[str]:
    """Extract file extensions mentioned in error context text."""
    if not context:
        return []
    matches = re.findall(r"\.\b(py|js|ts|tsx|sql|md|yaml|yml|json|toml|sh)\b", context)
    return [f".{m}" for m in matches]


def _classify_candidate(
    tool_counts: Counter,
    flow_hashes: list[str],
    repo_count: int,
    total_count: int,
) -> str:
    """Determine skill type from pattern and flow characteristics."""
    # If flows dominate, it is a workflow-sequence
    if flow_hashes:
        return "workflow-sequence"

    # If one tool accounts for 80%+ of errors, it is tool-specific
    if tool_counts:
        top_tool, top_count = tool_counts.most_common(1)[0]
        if total_count > 0 and (top_count / total_count) >= 0.8:
            return "tool-specific"

    # If most errors come from one project path, it is repo-specific
    if repo_count > 0 and total_count > 0 and (repo_count / total_count) >= 0.7:
        return "repo-specific"

    return "tool-specific"


def _compute_confidence(
    error_count: int,
    session_count: int,
    flow_success_rate: float | None,
) -> float:
    """Compute a confidence score for a skill candidate.

    Factors:
    - Higher error count = more evidence
    - More sessions = broader coverage
    - Higher flow success rate = proven workflow
    """
    # Base confidence from error frequency (caps at 0.4)
    freq_score = min(error_count / 20.0, 0.4)

    # Session breadth bonus (caps at 0.3)
    session_score = min(session_count / 10.0, 0.3)

    # Flow success bonus (caps at 0.3)
    flow_score = 0.0
    if flow_success_rate is not None:
        flow_score = min((flow_success_rate / 100.0) * 0.3, 0.3)

    return round(freq_score + session_score + flow_score, 3)


def discover_skill_candidates(
    db: sqlite3.Connection,
    repo_path: str = ".",
) -> list[dict]:
    """Discover patterns and flows that are good candidates for skill generation.

    Cross-references error patterns (grouped by tool_name and file extensions
    in context) with high-success flow events to identify candidates that
    have both a clear error signal AND a proven positive workflow.

    Parameters
    ----------
    db:
        An open sqlite3.Connection with the SIO schema.
    repo_path:
        Project path to check for repo-specific patterns (default ".").

    Returns
    -------
    list[dict]
        List of candidate dicts, each containing:
        ``description``, ``pattern_ids``, ``flow_hashes``,
        ``suggested_skill_type``, ``confidence``, ``error_count``,
        ``session_count``, ``tool_name``, ``extensions``.
    """
    candidates: list[dict] = []

    # ---------------------------------------------------------------
    # Step 1: Query patterns grouped by tool_name
    # ---------------------------------------------------------------
    tool_patterns = db.execute(
        """
        SELECT
            p.tool_name,
            GROUP_CONCAT(p.id) as pattern_ids,
            SUM(p.error_count) as total_errors,
            SUM(p.session_count) as total_sessions,
            COUNT(*) as pattern_count
        FROM patterns p
        WHERE p.tool_name IS NOT NULL AND p.tool_name != ''
        GROUP BY p.tool_name
        HAVING SUM(p.error_count) >= 2
        ORDER BY SUM(p.error_count) DESC
        """
    ).fetchall()

    # ---------------------------------------------------------------
    # Step 2: Query high-success flows
    # ---------------------------------------------------------------
    flows = db.execute(
        """
        SELECT
            fe.flow_hash,
            fe.sequence,
            COUNT(*) as count,
            SUM(fe.was_successful) as success_count,
            ROUND(
                CAST(SUM(fe.was_successful) AS REAL) / COUNT(*) * 100, 1
            ) as success_rate,
            COUNT(DISTINCT fe.session_id) as session_count
        FROM flow_events fe
        GROUP BY fe.flow_hash
        HAVING COUNT(*) >= 3
           AND (CAST(SUM(fe.was_successful) AS REAL) / COUNT(*)) > 0.5
        ORDER BY COUNT(*) DESC
        LIMIT 50
        """
    ).fetchall()

    flow_lookup: dict[str, dict] = {}
    for f in flows:
        fd = dict(f)
        flow_lookup[fd["flow_hash"]] = fd

    # ---------------------------------------------------------------
    # Step 3: For each tool group, check for matching flows and
    #         extract extension info from error context
    # ---------------------------------------------------------------
    for row in tool_patterns:
        rd = dict(row)
        tool_name = rd["tool_name"]
        pattern_id_strs = (rd["pattern_ids"] or "").split(",")
        pattern_ids = [int(pid) for pid in pattern_id_strs if pid.strip()]
        total_errors = rd["total_errors"]
        total_sessions = rd["total_sessions"]

        # Gather extensions from error records linked to these patterns
        ext_counter: Counter = Counter()
        if pattern_ids:
            placeholders = ", ".join(["?"] * len(pattern_ids))
            ctx_rows = db.execute(
                f"""
                SELECT er.context_before, er.context_after, er.source_file
                FROM error_records er
                JOIN pattern_errors pe ON pe.error_id = er.id
                WHERE pe.pattern_id IN ({placeholders})
                LIMIT 200
                """,
                pattern_ids,
            ).fetchall()

            for cr in ctx_rows:
                crd = dict(cr)
                for field in ("context_before", "context_after", "source_file"):
                    exts = _extract_extensions_from_context(crd.get(field))
                    ext_counter.update(exts)

        # Check for flows that mention this tool in their sequence
        matching_flows: list[str] = []
        best_flow_rate: float | None = None
        for fhash, fdata in flow_lookup.items():
            if tool_name.lower() in fdata["sequence"].lower():
                matching_flows.append(fhash)
                rate = fdata["success_rate"]
                if best_flow_rate is None or rate > best_flow_rate:
                    best_flow_rate = rate

        # Count repo-specific errors
        repo_count = 0
        if repo_path != "." and pattern_ids:
            placeholders = ", ".join(["?"] * len(pattern_ids))
            repo_row = db.execute(
                f"""
                SELECT COUNT(*) FROM error_records er
                JOIN pattern_errors pe ON pe.error_id = er.id
                WHERE pe.pattern_id IN ({placeholders})
                  AND er.source_file LIKE ?
                """,
                [*pattern_ids, f"%{repo_path}%"],
            ).fetchone()
            repo_count = repo_row[0] if repo_row else 0

        # Classify and score
        tool_counter: Counter = Counter({tool_name: total_errors})
        skill_type = _classify_candidate(
            tool_counter,
            matching_flows,
            repo_count,
            total_errors,
        )
        confidence = _compute_confidence(
            total_errors,
            total_sessions,
            best_flow_rate,
        )

        top_exts = [ext for ext, _ in ext_counter.most_common(3)]

        # Build description
        ext_note = f" (common files: {', '.join(top_exts)})" if top_exts else ""
        flow_note = (
            f" with proven workflow ({best_flow_rate:.0f}% success)"
            if best_flow_rate is not None
            else ""
        )
        description = (
            f"{tool_name}: {total_errors} errors across "
            f"{total_sessions} sessions{ext_note}{flow_note}"
        )

        candidates.append(
            {
                "description": description,
                "pattern_ids": pattern_ids,
                "flow_hashes": matching_flows,
                "suggested_skill_type": skill_type,
                "confidence": confidence,
                "error_count": total_errors,
                "session_count": total_sessions,
                "tool_name": tool_name,
                "extensions": top_exts,
            }
        )

    # ---------------------------------------------------------------
    # Step 4: Add flow-only candidates (workflow sequences not yet
    #         represented by a pattern group above)
    # ---------------------------------------------------------------
    covered_flows: set[str] = set()
    for c in candidates:
        covered_flows.update(c["flow_hashes"])

    for fhash, fdata in flow_lookup.items():
        if fhash in covered_flows:
            continue
        if fdata["session_count"] < 3:
            continue

        confidence = _compute_confidence(
            fdata["count"],
            fdata["session_count"],
            fdata["success_rate"],
        )

        candidates.append(
            {
                "description": (
                    f"Workflow: {fdata['sequence']} "
                    f"({fdata['count']} times, {fdata['success_rate']:.0f}% success)"
                ),
                "pattern_ids": [],
                "flow_hashes": [fhash],
                "suggested_skill_type": "workflow-sequence",
                "confidence": confidence,
                "error_count": 0,
                "session_count": fdata["session_count"],
                "tool_name": None,
                "extensions": [],
            }
        )

    # Sort by confidence descending
    candidates.sort(key=lambda c: c["confidence"], reverse=True)

    return candidates
