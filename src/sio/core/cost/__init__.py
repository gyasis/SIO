"""Cost transparency (Principle XII).

Two pieces:
  estimator  — pre-flight: given (model, expected_calls, expected_tokens), returns USD band
  usage_writer — post-flight: append one JSON line per LM call to ~/.sio/usage.log
"""
from .budget import (
    BudgetExceeded,
    check_budget,
    rolling_24h_spend,
)
from .estimator import (
    PRICING,
    estimate_call,
    estimate_optimize_run,
    estimate_run,
)
from .usage_writer import (
    USAGE_LOG,
    record_call,
    summarize,
)

__all__ = [
    "BudgetExceeded",
    "check_budget",
    "rolling_24h_spend",
    "PRICING",
    "estimate_call",
    "estimate_run",
    "estimate_optimize_run",
    "USAGE_LOG",
    "record_call",
    "summarize",
]
