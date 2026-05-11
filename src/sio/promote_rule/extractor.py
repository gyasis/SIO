"""DSPy detection-pattern extractor for ``sio promote-rule``.

Reads a CLAUDE.md rule that's being violated + a sample of the actual
violating tool calls and produces a structured detection pattern that
Phase 4 can render as an executable PreToolUse hook script.

The hot path goes through ``sio.core.dspy.lm_factory`` per repo
convention (Constitution V — DSPy-first; bare ``dspy.LM(...)`` calls
are forbidden).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import dspy

from sio.core.config import load_config
from sio.core.dspy.lm_factory import create_lm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DSPy Signature
# ---------------------------------------------------------------------------


class ExtractDetectionPattern(dspy.Signature):
    """Extract a structured PreToolUse-hook detection pattern from a violated rule.

    Read the rule text and the representative violating tool calls. Decide:
      - which tool name(s) the hook should match on (the harness "matcher")
      - a single Python expression that returns True iff the rule is being
        violated, using only these locals:
            tool_name           — str, the name of the tool about to run
            tool_input          — dict, the tool's argument payload
            recent_tool_names   — list[str], the previous 5 tool names this turn
            recent_tool_inputs  — list[dict], the matching previous payloads
      - a short rationale (one sentence) explaining how the expression
        captures the rule's intent.

    Be conservative: prefer false-negatives over false-positives. The
    generated hook will start in 'warn' mode so the user can audit
    before promoting to 'block'. If the rule is not structurally
    enforceable as a PreToolUse pattern (e.g. "always be careful"),
    return matcher_tools='' and detection_expr='False' — Phase 5 will
    surface that as "rule not promotable" and skip writing the hook.
    """

    rule_text: str = dspy.InputField(desc="The exact CLAUDE.md rule text being violated.")
    violation_examples_json: str = dspy.InputField(
        desc=(
            "JSON list of {tool_name, tool_input, error_text} dicts — "
            "samples of how the rule was violated in past sessions."
        )
    )

    matcher_tools: str = dspy.OutputField(
        desc=(
            "Comma-separated harness 'matcher' tool names (e.g. 'Bash', "
            "'Edit,Write'). Use '*' for any tool. Empty string means the "
            "rule is not promotable as a PreToolUse hook."
        )
    )
    detection_expr: str = dspy.OutputField(
        desc=(
            "Single Python expression that returns True when the rule "
            "is being violated. Available locals: tool_name (str), "
            "tool_input (dict), recent_tool_names (list[str]), "
            "recent_tool_inputs (list[dict]). Examples: "
            "\"'Bash' in recent_tool_names and tool_name in ('Edit', 'Write')\", "
            "\"tool_input.get('command', '').startswith('rm -rf /')\". "
            "Return 'False' if the rule is not promotable."
        )
    )
    rationale: str = dspy.OutputField(
        desc=(
            "One sentence explaining how the detection expression "
            "captures the rule's intent."
        )
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class DetectionPattern:
    """The structured output of extract_detection().

    Phase 4 (hook generator) consumes this directly. ``promotable``
    surfaces whether the LLM thinks the rule is structurally
    enforceable as a PreToolUse hook.
    """

    matcher_tools: list[str]
    detection_expr: str
    rationale: str
    promotable: bool

    def to_json_str(self) -> str:
        """Serialised form for the ``promoted_hooks.detection_pattern`` column."""
        return json.dumps(
            {
                "matcher_tools": self.matcher_tools,
                "detection_expr": self.detection_expr,
                "rationale": self.rationale,
                "promotable": self.promotable,
            },
            indent=2,
        )


def extract_detection(
    rule_text: str,
    samples: list[dict[str, Any]],
    *,
    max_samples: int = 10,
) -> DetectionPattern:
    """Run the DSPy extractor against a rule + its violating samples.

    Args:
        rule_text: The exact CLAUDE.md rule being violated.
        samples: List of violation dicts as produced by
            ``get_violation_report["violations"]``. Each must carry
            ``tool_name``, ``tool_input``, and ``error_text``.
        max_samples: Cap on samples sent to the LM (prompt size).

    Returns:
        A :class:`DetectionPattern` with the extracted matcher,
        detection expression, rationale, and a ``promotable`` flag
        derived from whether the LM produced a non-empty matcher and
        a non-trivial expression.

    Raises:
        RuntimeError: If no LLM is configured (``~/.sio/config.toml``
            ``[llm]`` block uncommented). Caller should surface this
            as a clean CLI error rather than a stack trace.
    """
    cfg = load_config()
    lm = create_lm(cfg)
    if lm is None:
        raise RuntimeError(
            "no LM configured — uncomment one [llm] provider block in "
            "~/.sio/config.toml and set the matching API key in env, "
            "then re-run `sio promote-rule`."
        )

    # Trim samples to fit the prompt budget. Keep tool_name +
    # truncated tool_input + truncated error_text.
    trimmed = []
    for s in samples[:max_samples]:
        trimmed.append(
            {
                "tool_name": s.get("tool_name") or "",
                "tool_input": (s.get("tool_input") or "")[:600],
                "error_text": (s.get("error_text") or "")[:300],
            }
        )
    examples_json = json.dumps(trimmed, ensure_ascii=False)

    with dspy.context(lm=lm):
        program = dspy.ChainOfThought(ExtractDetectionPattern)
        prediction = program(
            rule_text=rule_text,
            violation_examples_json=examples_json,
        )

    matcher_raw = (prediction.matcher_tools or "").strip()
    matcher_tools = (
        [t.strip() for t in matcher_raw.split(",") if t.strip()]
        if matcher_raw
        else []
    )
    detection_expr = (prediction.detection_expr or "False").strip()
    # The LM sometimes wraps its output in surrounding quotes (treats the
    # expression as a JSON string literal). Strip them so eval() sees the
    # actual expression — without this, eval returns the string itself
    # which is always truthy and the hook fires on every call.
    if len(detection_expr) >= 2 and (
        (detection_expr.startswith('"') and detection_expr.endswith('"'))
        or (detection_expr.startswith("'") and detection_expr.endswith("'"))
    ):
        detection_expr = detection_expr[1:-1]
    rationale = (prediction.rationale or "").strip()

    promotable = bool(matcher_tools) and detection_expr.lower() != "false"

    return DetectionPattern(
        matcher_tools=matcher_tools,
        detection_expr=detection_expr,
        rationale=rationale,
        promotable=promotable,
    )
