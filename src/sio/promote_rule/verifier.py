"""Historical-violation verifier for promoted detection patterns.

Phase 5 of ``prds/prd-violated-rule-to-pretooluse-hook.md``. Replays
the extracted detection expression against the actual past violations
of a rule and reports coverage — the safety gate that runs before
``sio promote-rule --write`` actually installs the hook.

Coverage is the answer to: "if I install this hook today, how often
would it have caught the rule being violated in the past?" High
coverage means the detection captures the rule's intent. Low coverage
means the LM's expression doesn't match the real-world violations and
the user should review before promoting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sio.promote_rule.extractor import DetectionPattern


@dataclass
class VerificationResult:
    """Output of :func:`verify_against_history`."""

    total: int                                  # total historical violations checked
    fires: int                                  # how many the detection caught
    misses: int                                 # how many it missed
    coverage_rate: float                        # fires / total (0.0 if total==0)
    by_session: dict[str, dict[str, int]]       # per-session fires/misses/total
    examples_fired: list[dict[str, Any]] = field(default_factory=list)   # up to 5
    examples_missed: list[dict[str, Any]] = field(default_factory=list)  # up to 5


def _parse_tool_input(raw: Any) -> dict[str, Any]:
    """tool_input is stored as JSON-encoded text in error_records — parse safely."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def verify_against_history(
    pattern: DetectionPattern,
    matching: list[dict[str, Any]],
    *,
    window_size: int = 5,
    example_cap: int = 5,
) -> VerificationResult:
    """Replay ``pattern.detection_expr`` against the rule's historical violations.

    Groups violations by ``session_id`` and replays each session in
    timestamp order, reconstructing the same rolling
    ``recent_tool_names`` / ``recent_tool_inputs`` window the live
    hook will see at runtime. For each call evaluates the detection
    expression and counts fires vs misses.

    Args:
        pattern: The detection pattern from Phase 3.
        matching: Full list of violations for this rule (NOT the
            10-sample subset used for extraction — using the full
            list gives a real coverage signal since the LM never
            saw most of these).
        window_size: Rolling window size — must match the value in
            the generated hook script (currently 5).
        example_cap: Cap on the per-bucket examples kept (kept low
            so the panel renders in one screen).

    Returns:
        :class:`VerificationResult` with totals, per-session stats,
        and a few representative fired/missed examples.
    """
    # Group + sort by session, then by timestamp
    by_session: dict[str, list[dict[str, Any]]] = {}
    for v in sorted(matching, key=lambda v: v.get("timestamp", "")):
        sid = v.get("session_id", "") or "unknown"
        by_session.setdefault(sid, []).append(v)

    fires = 0
    misses = 0
    examples_fired: list[dict[str, Any]] = []
    examples_missed: list[dict[str, Any]] = []
    by_session_stats: dict[str, dict[str, int]] = {}

    for sid, session_calls in by_session.items():
        recent_tool_names: list[str] = []
        recent_tool_inputs: list[dict] = []
        s_fires, s_misses = 0, 0

        for call in session_calls:
            tool_name = call.get("tool_name", "") or ""
            tool_input = _parse_tool_input(call.get("tool_input"))

            fires_now = False
            try:
                fires_now = bool(
                    eval(
                        pattern.detection_expr,
                        {"__builtins__": {}},
                        {
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "recent_tool_names": list(recent_tool_names),
                            "recent_tool_inputs": list(recent_tool_inputs),
                        },
                    )
                )
            except Exception:  # noqa: BLE001
                # Eval failure = no fire (matches the live hook's fail-open
                # behaviour). Counted as a miss.
                fires_now = False

            entry = {
                "tool_name": tool_name,
                "tool_input_excerpt": str(tool_input)[:80],
                "recent_tool_names": list(recent_tool_names),
                "session_id": sid[:12],
            }
            if fires_now:
                fires += 1
                s_fires += 1
                if len(examples_fired) < example_cap:
                    examples_fired.append(entry)
            else:
                misses += 1
                s_misses += 1
                if len(examples_missed) < example_cap:
                    examples_missed.append(entry)

            # Update rolling window for the next call in this session
            recent_tool_names.append(tool_name)
            recent_tool_inputs.append(tool_input)
            recent_tool_names = recent_tool_names[-window_size:]
            recent_tool_inputs = recent_tool_inputs[-window_size:]

        by_session_stats[sid] = {
            "fires": s_fires,
            "misses": s_misses,
            "total": len(session_calls),
        }

    total = fires + misses
    return VerificationResult(
        total=total,
        fires=fires,
        misses=misses,
        coverage_rate=(fires / total) if total else 0.0,
        by_session=by_session_stats,
        examples_fired=examples_fired,
        examples_missed=examples_missed,
    )
