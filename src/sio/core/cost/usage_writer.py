"""Post-flight usage.log writer (Principle XII clause 2).

Appends one JSON line per billable LM call to ~/.sio/usage.log so the user
can answer "what did SIO spend this week?" from one file.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

USAGE_LOG = Path.home() / ".sio" / "usage.log"
_LOCK = threading.Lock()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def record_call(
    model: str,
    role: str,
    in_tokens: int,
    out_tokens: int,
    cost_usd: float,
    run_id: str | None = None,
    cmd: str | None = None,
    latency_ms: int | None = None,
) -> None:
    """Append one JSON line. Thread-safe."""
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _iso(),
        "model": model,
        "role": role,
        "in_tokens": in_tokens,
        "out_tokens": out_tokens,
        "cost_usd": round(cost_usd, 6),
        "run_id": run_id,
        "cmd": cmd,
        "latency_ms": latency_ms,
    }
    with _LOCK:
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")


def summarize(since_days: int = 7) -> dict:
    """Read usage.log, return totals by model + day."""
    if not USAGE_LOG.exists():
        return {"total_cost": 0.0, "total_calls": 0, "by_model": {}, "by_day": {}}
    cutoff_iso = None
    if since_days is not None:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=since_days)
        cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")

    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    total_cost = 0.0; total_calls = 0

    with open(USAGE_LOG) as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if cutoff_iso and e.get("ts", "") < cutoff_iso:
                continue
            m = e["model"]; day = e["ts"][:10]
            for d, k in ((by_model, m), (by_day, day)):
                slot = d.setdefault(k, {"calls": 0, "in_tok": 0, "out_tok": 0, "cost": 0.0})
                slot["calls"] += 1
                slot["in_tok"] += e.get("in_tokens", 0)
                slot["out_tok"] += e.get("out_tokens", 0)
                slot["cost"] += e.get("cost_usd", 0.0)
            total_cost += e.get("cost_usd", 0.0)
            total_calls += 1

    return {
        "total_cost": round(total_cost, 4),
        "total_calls": total_calls,
        "by_model": by_model,
        "by_day": by_day,
    }
