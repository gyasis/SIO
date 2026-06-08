"""recall_polish — real LM-based runbook polisher for `sio recall --polish`.

US5 / T051 implementation. Routes through lm_factory (FR-041) — the single
authorised dspy.LM construction hub. Default model: ``ollama/qwen3-coder:30b``
(free, local, coding-oriented). Override via ``--polish-model`` CLI flag or
``SIO_POLISH_LM`` env var.

Cost gate
---------
* ``ollama/*`` models → FREE, no gate.
* All other providers → PAID; print model + estimated cost range + require
  either an interactive ``y`` confirmation OR the ``--confirm-cost`` flag before
  calling the LM.  NEVER defaults to or allows ``gpt-4o`` (cost-control rule).

Loud failure
------------
If ``make_lm`` raises (banned/unconfigured) OR the LM call raises
(network/auth), we raise ``PolishError`` with a clear message. The caller
(``main.py`` recall command) converts this to a non-zero exit.  The old
prompt-string stub is GONE.
"""

from __future__ import annotations

import os
from typing import Any

# lm_factory is the mandated LM-construction hub (FR-041, CLAUDE.md).
# Imported at module level so tests can monkeypatch sio.recall_polish.make_lm.
# is_free_model is the canonical free/paid policy helper — single source of truth.
from sio.core.dspy.lm_factory import (
    is_free_model,  # noqa: F401 (re-exported)
    make_lm,  # noqa: F401 (re-exported for patching)
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_POLISH_MODEL = "ollama/qwen3-coder:30b"
_POLISH_MODEL_ENV = "SIO_POLISH_LM"

# Cost estimate for paid provider calls (~$0.001–0.02 per call, depends on tokens).
_PAID_COST_ESTIMATE = "$0.001–0.05"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PolishError(RuntimeError):
    """Raised when the polish LM call fails or is aborted by the user."""


# ---------------------------------------------------------------------------
# Core polish call
# ---------------------------------------------------------------------------


def polish_runbook(
    raw_runbook: str,
    query: str,
    *,
    model: str | None = None,
    confirm_cost: bool = False,
    interactive: bool = True,
) -> str:
    """Polish ``raw_runbook`` into a clean runbook using the configured LM.

    Args:
        raw_runbook: The unpolished recall output (markdown).
        query: The original user query (used in the prompt).
        model: Model string override. Falls back to ``SIO_POLISH_LM`` env var,
            then to ``DEFAULT_POLISH_MODEL``.
        confirm_cost: If True, skip interactive cost gate (equivalent to ``--confirm-cost``).
        interactive: If False, treat as non-interactive (CI / piped output) — paid
            model without ``confirm_cost=True`` will raise instead of prompting.

    Returns:
        Polished runbook string from the LM.

    Raises:
        PolishError: if the LM is unreachable, call fails, or cost gate is not passed.
    """

    resolved_model = (
        model
        or os.environ.get(_POLISH_MODEL_ENV)
        or DEFAULT_POLISH_MODEL
    )

    # Cost gate for paid providers
    if not is_free_model(resolved_model):
        cost_msg = (
            f"[recall --polish] Model: {resolved_model}  "
            f"Estimated cost: {_PAID_COST_ESTIMATE} per call."
        )
        print(cost_msg)

        if not confirm_cost:
            if not interactive:
                raise PolishError(
                    f"Paid model '{resolved_model}' requires explicit cost confirmation. "
                    "Pass --confirm-cost to proceed without interactive prompt."
                )
            # Interactive prompt
            try:
                answer = input("Proceed with paid model? [y/N] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer not in ("y", "yes"):
                raise PolishError(
                    f"Polish aborted — cost not confirmed for model '{resolved_model}'."
                )

    # Build LM via lm_factory (the mandated construction hub).
    # Uses the module-level ``make_lm`` import so tests can monkeypatch
    # ``sio.recall_polish.make_lm`` without touching lm_factory itself.
    #
    # If make_lm raises ValueError for a banned or forbidden model (gpt-4o
    # family check or config exact-match ban), we re-raise it as PolishError
    # so the CLI surface gets a clean message instead of a raw traceback.
    try:
        from sio import recall_polish as _self  # noqa: PLC0415

        lm = _self.make_lm(
            resolved_model,
            temperature=0.3,
            max_tokens=4096,
            cache=False,
            role="task",
        )
    except PolishError:
        raise
    except ValueError as exc:
        # Banned/forbidden model from lm_factory._check_banned — surface cleanly.
        msg = str(exc)
        if "forbidden" in msg.lower() or "banned" in msg.lower():
            raise PolishError(
                f"Model '{resolved_model}' is forbidden: {exc}. "
                "See ~/.claude/rules/domains/cost-control.md."
            ) from exc
        raise PolishError(
            f"Failed to initialise LM '{resolved_model}': {exc}. "
            "Check your model configuration or try a different model via SIO_POLISH_LM."
        ) from exc
    except Exception as exc:
        raise PolishError(
            f"Failed to initialise LM '{resolved_model}': {exc}. "
            "Check your model configuration or try a different model via SIO_POLISH_LM."
        ) from exc

    # Build the polish prompt
    prompt = _build_polish_prompt(raw_runbook, query)

    # Call the LM
    try:
        response = lm(messages=[{"role": "user", "content": prompt}])
    except Exception as exc:
        raise PolishError(
            f"LM call to '{resolved_model}' failed: {exc}. "
            "Is Ollama running? Is the API key set? Check connectivity."
        ) from exc

    # Extract text from dspy LM response (list of strings or list of dicts)
    return _extract_text(response, resolved_model)


def _build_polish_prompt(raw_runbook: str, query: str) -> str:
    """Build the LM polish prompt from the raw recall output."""
    return (
        f"You are a technical writer. Polish the following raw runbook into a clean, "
        f"concise step-by-step guide for the task: '{query}'.\n\n"
        "Rules:\n"
        "1. Keep only the steps that are necessary for the task.\n"
        "2. Format each step as: '<N>. **<action>**' with a brief bash command block if "
        "relevant.\n"
        "3. Highlight problem→fix pairs clearly.\n"
        "4. Do not add steps that were not in the original runbook.\n"
        "5. Output only the polished runbook (markdown), no preamble.\n\n"
        f"RAW RUNBOOK:\n{raw_runbook}"
    )


def _extract_text(response: Any, model: str) -> str:
    """Extract plain text from a dspy LM response.

    dspy LM returns a list of completion strings or a list of message objects.
    """
    if isinstance(response, list) and response:
        first = response[0]
        if isinstance(first, str):
            return first
        if hasattr(first, "text"):
            return first.text
        if isinstance(first, dict):
            return first.get("content", str(first))
    if isinstance(response, str):
        return response
    raise PolishError(
        f"Unexpected LM response format from '{model}': {type(response)!r}. "
        "Cannot extract polished runbook text."
    )
