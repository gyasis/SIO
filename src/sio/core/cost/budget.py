"""24h rolling spend cap (Principle XII clause 6).

Reads [budget] block from ~/.sio/config.toml. Computes rolling 24h spend
from ~/.sio/usage.log. Refuses to launch new paid LM call when cap is
exceeded. --budget-override <USD> escapes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .usage_writer import USAGE_LOG

_CONFIG = Path.home() / ".sio" / "config.toml"
_DEFAULT_24H_CAP_USD = 5.0


def _read_cap() -> float:
    """Read [budget].rolling_24h_usd from config.toml. Defaults to $5."""
    if not _CONFIG.exists():
        return _DEFAULT_24H_CAP_USD
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        return _DEFAULT_24H_CAP_USD
    try:
        with open(_CONFIG, "rb") as f:
            cfg = tomllib.load(f)
        return float(cfg.get("budget", {}).get("rolling_24h_usd", _DEFAULT_24H_CAP_USD))
    except Exception:
        return _DEFAULT_24H_CAP_USD


def rolling_24h_spend() -> float:
    """Sum cost_usd of every usage.log entry within the last 24 hours."""
    if not USAGE_LOG.exists():
        return 0.0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24))
    cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    total = 0.0
    try:
        with open(USAGE_LOG) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("ts", "") < cutoff_iso:
                    continue
                total += float(e.get("cost_usd", 0.0) or 0.0)
    except OSError:
        return 0.0
    return total


class BudgetExceeded(Exception):
    pass


def check_budget(override_usd: float | None = None) -> dict:
    """Return state. Raises BudgetExceeded if cap breached and no override.

    Override is set by the env var SIO_BUDGET_OVERRIDE (set from --budget-override
    by the optimize / amplify CLI flags).
    """
    cap = _read_cap()
    if override_usd is None:
        try:
            override_usd = float(os.environ.get("SIO_BUDGET_OVERRIDE", "0") or "0")
        except ValueError:
            override_usd = 0.0
    effective_cap = max(cap, override_usd or 0.0)
    spend = rolling_24h_spend()
    state = {
        "cap_usd": cap,
        "override_usd": override_usd,
        "effective_cap_usd": effective_cap,
        "spend_24h_usd": round(spend, 4),
        "remaining_usd": round(effective_cap - spend, 4),
        "breached": spend > effective_cap,
    }
    if state["breached"]:
        raise BudgetExceeded(
            f"24h spend {spend:.4f} > cap {effective_cap:.4f}. "
            f"Set [budget].rolling_24h_usd higher in ~/.sio/config.toml, "
            f"or pass --budget-override <USD> to escape this run."
        )
    return state
