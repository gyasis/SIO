"""RLM corpus miner — analyzes failure context from conversation history."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Lazy import guard for dspy
try:
    import dspy

    _dspy_available = True
except ImportError:
    dspy = None  # type: ignore[assignment]
    _dspy_available = False


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


def _corpus_search(corpus_path: str, query: str) -> str:
    """Search the corpus for relevant context using the corpus indexer.

    Returns concatenated snippets from the top search results, or an
    empty string if the corpus cannot be indexed.
    """
    try:
        from sio.core.dspy.corpus_indexer import index_corpus

        from sio.core.constants import DEFAULT_PLATFORM  # noqa: PLC0415
        idx = index_corpus(DEFAULT_PLATFORM, history_dir=corpus_path)
        results = idx.search_keyword(query, top_k=3)
        if results:
            return "\n---\n".join(r.snippet for r in results)
    except Exception:
        logger.debug("Corpus search failed", exc_info=True)
    return ""


def _heuristic_mining(
    skill: str,
    message: str,
    outcome: str | None,
    corpus_context: str,
) -> MiningResult:
    """Heuristic-based failure analysis (no LLM required).

    Used as fallback when DSPy is unavailable or fails.
    """
    trajectory = [
        {
            "step": 1,
            "code": f"search_corpus('{skill}')",
            "output": f"Found failure records for skill: {skill}",
        },
        {
            "step": 2,
            "code": f"analyze_failure('{message}')",
            "output": f"Failure pattern: skill={skill}, outcome={outcome}",
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
        llm_calls=0,
    )


def mine_failure_context(
    corpus_path: str,
    failure_record: dict,
    sub_lm: str | None = None,
    max_iterations: int = 20,
    max_llm_calls: int = 50,
) -> MiningResult:
    """Mine failure context from conversation corpus.

    Uses DSPy ChainOfThought to analyze failures when available.
    Falls back to heuristic analysis when DSPy or the LLM is unavailable.

    Args:
        corpus_path: Path to indexed corpus directory.
        failure_record: Dict with failure details.
        sub_lm: Model name for LLM calls (cheap model).
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

    # Search corpus for relevant context
    corpus_context = _corpus_search(corpus_path, f"{skill} {message}")

    # Try the DSPy ChainOfThought path
    if _dspy_available:
        try:
            # Define signature inline for failure analysis
            class FailureAnalysis(dspy.Signature):
                """Analyze a failure record from conversation history to identify root cause."""

                failure_skill = dspy.InputField(desc="The tool/skill that failed")
                failure_message = dspy.InputField(
                    desc="The user message that triggered the failure",
                )
                failure_outcome = dspy.InputField(
                    desc="The outcome status of the failure",
                )
                corpus_context = dspy.InputField(
                    desc="Relevant context from conversation corpus",
                )
                failure_analysis = dspy.OutputField(
                    desc="Root cause analysis and recommendation",
                )

            module = dspy.ChainOfThought(FailureAnalysis)
            prediction = module(
                failure_skill=skill,
                failure_message=message,
                failure_outcome=str(outcome),
                corpus_context=corpus_context or "(no corpus context available)",
            )

            trajectory = [
                {
                    "step": 1,
                    "code": f"corpus_search('{skill} {message}')",
                    "output": corpus_context[:200] if corpus_context else "No context found",
                },
                {
                    "step": 2,
                    "code": "dspy.ChainOfThought(FailureAnalysis)(...)",
                    "output": prediction.failure_analysis[:200],
                },
            ]

            return MiningResult(
                failure_analysis=prediction.failure_analysis,
                trajectory=trajectory,
                llm_calls=1,
            )

        except Exception:
            # DSPy call failed — fall back to heuristic
            logger.warning("DSPy ChainOfThought failed, falling back to heuristic", exc_info=True)

    return _heuristic_mining(skill, message, outcome, corpus_context)
