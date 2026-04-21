"""DSPy Module wrapper for skill generation — Predict reasoning over
SkillGeneratorSignature.

dspy is imported lazily so the rest of SIO can load without it installed.
"""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

try:
    import dspy as _dspy

    _Module = _dspy.Module
except ImportError:  # pragma: no cover
    _dspy = None  # type: ignore[assignment]
    _Module = object  # type: ignore[assignment,misc]


def _template_skill(
    pattern: str,
    errors: str,
    positives: str,
    flow: str,
) -> str:
    """Generate a template-based skill when no LLM is available.

    Returns a markdown skill document built from the inputs without
    calling any language model.
    """
    lines = [
        f"# Skill: {pattern}",
        "",
        "## Trigger Conditions",
        f"When the error pattern '{pattern}' is detected.",
        "",
        "## Steps",
    ]
    for i, step in enumerate(flow.split(","), 1):
        lines.append(f"{i}. Run `{step.strip()}`")
    lines.append("")
    lines.append("## Guardrails")
    lines.append("- NEVER skip the established tool sequence")
    lines.append("- ALWAYS verify each step before proceeding")
    return "\n".join(lines)


class SkillGeneratorModule(_Module):
    """Generates structured Claude Code skill files using Predict reasoning."""

    def __init__(self) -> None:
        if _dspy is None:
            raise ImportError("dspy is required for SkillGeneratorModule — pip install dspy")
        super().__init__()
        from sio.core.dspy.signatures import SkillGeneratorSignature

        self.generate = _dspy.Predict(SkillGeneratorSignature)

    def forward(
        self,
        pattern_description: str,
        error_examples: str,
        positive_examples: str = "[]",
        flow_sequence: str = "",
    ):
        """Run the DSPy prediction and return the result."""
        return self.generate(
            pattern_description=pattern_description,
            error_examples=error_examples,
            positive_examples=positive_examples,
            flow_sequence=flow_sequence,
        )

    def generate_skill(
        self,
        pattern: str,
        errors: list | str,
        positives: list | str | None = None,
        flow: str = "",
    ) -> str:
        """Convenience method: run prediction and assemble into skill markdown.

        Args:
            pattern: Description of the recurring error pattern.
            errors: Error examples (list or JSON string).
            positives: Positive signal examples (list or JSON string).
            flow: Comma-separated tool sequence that succeeds.

        Returns:
            Markdown-formatted skill document.
        """
        if isinstance(errors, list):
            errors = json.dumps(errors)
        if positives is None:
            positives = "[]"
        if isinstance(positives, list):
            positives = json.dumps(positives)

        result = self.forward(
            pattern_description=pattern,
            error_examples=errors,
            positive_examples=positives,
            flow_sequence=flow,
        )

        # Assemble markdown from structured output fields
        lines = [
            f"# Skill: {pattern}",
            "",
            "## Trigger Conditions",
            result.trigger_conditions,
            "",
            "## Steps",
            result.ordered_steps,
            "",
            "## Guardrails",
            result.guardrails,
        ]
        return "\n".join(lines)


def _load_optimized_or_default(
    conn: sqlite3.Connection | None = None,
) -> SkillGeneratorModule:
    """Load the active optimized SkillGeneratorModule, or return a fresh one.

    If an optimized module exists in the module_store, load its state.
    Otherwise, return a default (unoptimized) instance.

    Args:
        conn: Optional SQLite connection for looking up optimized modules.

    Returns:
        A SkillGeneratorModule instance (optimized if available).
    """
    if conn is not None:
        try:
            from sio.core.dspy.module_store import (
                get_active_module,
                load_module,
            )

            active = get_active_module(conn, "skill_generator")
            if active and active.get("file_path"):
                logger.info(
                    "Loading optimized skill_generator module: %s",
                    active["file_path"],
                )
                return load_module(SkillGeneratorModule, active["file_path"])
        except Exception:
            logger.debug(
                "Failed to load optimized module, using default",
                exc_info=True,
            )

    return SkillGeneratorModule()


def generate_skill_safe(
    pattern: str,
    errors: list | str,
    positives: list | str | None = None,
    flow: str = "",
    conn: sqlite3.Connection | None = None,
) -> str:
    """Generate a skill with LLM if available, otherwise use template fallback.

    This is the primary entry point for skill generation. It attempts to use
    the DSPy module with an LLM backend. If no LLM is configured or dspy
    is not installed, it falls back to template-based generation.

    Args:
        pattern: Description of the recurring error pattern.
        errors: Error examples (list or JSON string).
        positives: Positive signal examples (list or JSON string).
        flow: Comma-separated tool sequence that succeeds.
        conn: Optional SQLite connection for loading optimized modules.

    Returns:
        Markdown-formatted skill document.
    """
    if isinstance(errors, list):
        errors_str = json.dumps(errors)
    else:
        errors_str = errors
    if positives is None:
        positives_str = "[]"
    elif isinstance(positives, list):
        positives_str = json.dumps(positives)
    else:
        positives_str = positives

    # Try LLM-backed generation
    try:
        module = _load_optimized_or_default(conn)
        return module.generate_skill(
            pattern=pattern,
            errors=errors_str,
            positives=positives_str,
            flow=flow,
        )
    except (ImportError, Exception) as exc:
        logger.info(
            "LLM-backed skill generation unavailable (%s), falling back to template",
            exc,
        )
        return _template_skill(
            pattern=pattern,
            errors=errors_str,
            positives=positives_str,
            flow=flow,
        )
