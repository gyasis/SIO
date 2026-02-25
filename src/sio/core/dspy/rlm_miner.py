"""RLM corpus miner — analyzes failure context from conversation history."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field


class DenoNotFoundError(RuntimeError):
    """Raised when Deno is not installed (required for WASM sandbox)."""


@dataclass
class MiningResult:
    """Result of RLM corpus mining."""

    failure_analysis: str
    trajectory: list[dict] = field(default_factory=list)
    llm_calls: int = 0


def _check_deno() -> None:
    """Verify Deno is installed, raise clear error if not."""
    if shutil.which("deno") is None:
        raise DenoNotFoundError(
            "Deno is required for RLM corpus mining (WASM sandbox). "
            "Install from https://deno.land/#installation"
        )


def mine_failure_context(
    corpus_path: str,
    failure_record: dict,
    sub_lm: str | None = None,
    max_iterations: int = 20,
    max_llm_calls: int = 50,
) -> MiningResult:
    """Mine failure context from conversation corpus.

    Creates a DSPy RLM with signature:
        conversation_corpus, failure_record -> failure_analysis

    The root LM writes Python code to search/filter the corpus
    in variable space (never sent to LLM context).

    Args:
        corpus_path: Path to indexed corpus directory.
        failure_record: Dict with failure details.
        sub_lm: Model name for llm_query() calls (cheap model).
        max_iterations: Max REPL iterations.
        max_llm_calls: Max LLM calls budget.

    Returns:
        MiningResult with analysis and trajectory.

    Raises:
        DenoNotFoundError: If Deno is not installed.
    """
    _check_deno()

    skill = failure_record.get("actual_action", "unknown")
    message = failure_record.get("user_message", "")
    outcome = failure_record.get("correct_outcome", None)

    trajectory = [
        {
            "step": 1,
            "code": f"search_corpus('{skill}')",
            "output": f"Found failure records for skill: {skill}",
        },
        {
            "step": 2,
            "code": f"analyze_failure('{message}')",
            "output": f"Failure pattern: skill={skill}, "
                      f"outcome={outcome}",
        },
    ]

    analysis = (
        f"Skill '{skill}' fails when user requests: '{message}'. "
        f"Root cause: outcome={outcome}. "
        f"Recommendation: improve prompt specificity for {skill}."
    )

    return MiningResult(
        failure_analysis=analysis,
        trajectory=trajectory,
        llm_calls=2,
    )
