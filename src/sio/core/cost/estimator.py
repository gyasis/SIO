"""Pre-flight cost estimator (Principle XII clause 1).

Pricing table (USD per million tokens, as of 2026-05). Update when new
models drop or pricing shifts.
"""
from __future__ import annotations

from typing import NamedTuple

# Per-million-token pricing in USD
PRICING: dict[str, dict[str, float]] = {
    # Google Gemini
    "gemini/gemini-pro-latest":             {"in": 1.25, "out": 10.00},
    "gemini/gemini-flash-latest":           {"in": 0.075, "out": 0.30},
    "gemini/gemini-2.5-pro":                {"in": 1.25, "out": 10.00},
    "gemini/gemini-2.5-flash":              {"in": 0.075, "out": 0.30},
    # OpenAI
    "openai/gpt-5":                         {"in": 1.25, "out": 10.00},
    "openai/gpt-5-mini":                    {"in": 0.25, "out": 2.00},
    "openai/gpt-5-nano":                    {"in": 0.05, "out": 0.40},
    "openai/gpt-4o-mini":                   {"in": 0.15, "out": 0.60},
    "openai/gpt-4o":                        {"in": 2.50, "out": 10.00},  # banned, listed for cost ref
    # Anthropic (illustrative; SIO doesn't bill these today)
    "anthropic/claude-3-5-sonnet-20241022": {"in": 3.00, "out": 15.00},
    "anthropic/claude-sonnet-4-6":          {"in": 3.00, "out": 15.00},
    # Local / free
    "ollama/qwen3-coder:30b":               {"in": 0.0,  "out": 0.0},
    "ollama/deepseek-r1:32b":               {"in": 0.0,  "out": 0.0},
    "ollama_chat/qwen3-coder:30b":          {"in": 0.0,  "out": 0.0},
    "ollama_chat/deepseek-r1:32b":          {"in": 0.0,  "out": 0.0},
    "ollama_chat/gemma4:26b":               {"in": 0.0,  "out": 0.0},
    "ollama_chat/phi4:14b":                 {"in": 0.0,  "out": 0.0},
}

# Default fallback when model is unknown (mid-tier guess)
_FALLBACK = {"in": 1.0, "out": 5.0}


class Estimate(NamedTuple):
    model: str
    calls: int
    in_tokens: int
    out_tokens: int
    low: float       # 0.5× midpoint — conservative floor
    mid: float       # exact compute from pricing
    high: float      # 2× midpoint — conservative ceiling


def estimate_call(model: str, in_tokens: int, out_tokens: int) -> float:
    """Single call cost in USD (midpoint, exact at given token counts)."""
    p = PRICING.get(model, _FALLBACK)
    return (in_tokens / 1_000_000) * p["in"] + (out_tokens / 1_000_000) * p["out"]


def estimate_run(
    model: str,
    calls: int,
    avg_in_tokens: int,
    avg_out_tokens: int,
) -> Estimate:
    """Estimate total cost for N calls with average token volumes."""
    mid = calls * estimate_call(model, avg_in_tokens, avg_out_tokens)
    return Estimate(
        model=model, calls=calls,
        in_tokens=calls * avg_in_tokens,
        out_tokens=calls * avg_out_tokens,
        low=mid * 0.5, mid=mid, high=mid * 2.0,
    )


# Optimizer-specific calibrated estimates from real run data (2026-05-16)
_OPTIMIZER_PROFILES = {
    # (avg_calls, avg_in_tok, avg_out_tok) per role for ~370-row trainsets
    ("gepa", "light"): {
        "task":       {"calls": 60, "in_tok": 2400, "out_tok": 6300},
        "reflection": {"calls": 15, "in_tok": 2400, "out_tok": 3300},
    },
    ("gepa", "medium"): {
        "task":       {"calls": 150, "in_tok": 2400, "out_tok": 6300},
        "reflection": {"calls": 40, "in_tok": 2400, "out_tok": 3300},
    },
    ("gepa", "heavy"): {
        "task":       {"calls": 400, "in_tok": 2400, "out_tok": 6300},
        "reflection": {"calls": 100, "in_tok": 2400, "out_tok": 3300},
    },
    ("mipro", "light"): {
        "task":       {"calls": 30, "in_tok": 2600, "out_tok": 1100},
        "reflection": {"calls": 0,  "in_tok": 0,    "out_tok": 0},
    },
    ("mipro", "medium"): {
        "task":       {"calls": 100, "in_tok": 2600, "out_tok": 1100},
        "reflection": {"calls": 0,   "in_tok": 0,    "out_tok": 0},
    },
    ("bootstrap", "any"): {
        "task":       {"calls": 8, "in_tok": 2400, "out_tok": 2000},
        "reflection": {"calls": 0, "in_tok": 0,    "out_tok": 0},
    },
}


def estimate_optimize_run(
    optimizer: str,
    budget: str = "light",
    task_lm: str = "gemini/gemini-flash-latest",
    reflection_lm: str = "gemini/gemini-pro-latest",
) -> dict:
    """Return pre-flight cost band for an `sio optimize` run."""
    key = (optimizer, budget) if (optimizer, budget) in _OPTIMIZER_PROFILES else (optimizer, "any")
    if key not in _OPTIMIZER_PROFILES:
        return {"error": f"No profile for ({optimizer}, {budget})"}
    profile = _OPTIMIZER_PROFILES[key]

    out = {"optimizer": optimizer, "budget": budget, "by_role": {}}
    total_low = total_mid = total_high = 0.0
    for role, p in profile.items():
        lm = task_lm if role == "task" else reflection_lm
        est = estimate_run(lm, p["calls"], p["in_tok"], p["out_tok"])
        out["by_role"][role] = {
            "model": est.model, "calls": est.calls,
            "low": est.low, "mid": est.mid, "high": est.high,
        }
        total_low += est.low; total_mid += est.mid; total_high += est.high
    out["total"] = {"low": total_low, "mid": total_mid, "high": total_high}
    return out
