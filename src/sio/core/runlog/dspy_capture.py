"""DSPy interaction capture (Principle XIII clause 7).

Monkey-patches ``dspy.LM.__call__`` (and its sibling generation path) to
log every LM invocation:
  - The prompt sent to the LM
  - The raw completion
  - Per-call latency
  - Token counts (when present in response metadata)

Writes one JSON line per call to ``~/.sio/runs/<UTC>_<cmd>_<id>_dspy.jsonl``
— a sibling file to the main run log. Also captures GEPA reflective
prompts because GEPA's ``reflection_lm`` is just another dspy.LM under
the hood and routes through ``__call__``.

Activation is implicit when @runlogged starts a stage; install() is
idempotent.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from .writer import current

_INSTALLED = False
_ORIG_LM_CALL = None
_CAPTURE_FILE: Optional[Path] = None


def _ensure_capture_file() -> Optional[Path]:
    """Resolve the capture file path from the active RunLog."""
    global _CAPTURE_FILE
    rl = current()
    if rl is None:
        return None
    if _CAPTURE_FILE is not None:
        return _CAPTURE_FILE
    # Sibling file: <runlog>_dspy.jsonl
    base = rl._path  # type: ignore[attr-defined]
    _CAPTURE_FILE = base.with_name(base.stem + "_dspy.jsonl")
    return _CAPTURE_FILE


def _append_record(rec: dict) -> None:
    path = _ensure_capture_file()
    if path is None:
        return
    try:
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # don't break the pipeline over a log


def _truncate(s: Any, n: int = 8000) -> str:
    """Cap big prompts/completions so the file stays readable."""
    if s is None:
        return ""
    s = str(s)
    if len(s) <= n:
        return s
    return s[:n] + f"\n…[truncated {len(s) - n} chars]"


def install() -> None:
    """Monkey-patch dspy.LM.__call__ for one-shot capture. Idempotent.

    P1 fix 2026-05-16: capture _ORIG_LM_CALL FRESH every install (not cached
    from first-ever invocation). Prevents leaking intermediate patches across
    process-shared runs (e.g. pytest running two @runlogged tests).
    """
    global _INSTALLED, _ORIG_LM_CALL
    if _INSTALLED:
        return
    try:
        import dspy  # noqa: PLC0415
    except ImportError:
        return  # DSPy not present in this env; nothing to capture

    # Capture FRESH each install — don't reuse stale cached original
    _ORIG_LM_CALL = dspy.LM.__call__

    def _wrapped(self, *args, **kwargs):  # noqa: ANN001, ANN202
        rl = current()
        if rl is None:
            # Not in a runlogged context — just call through
            return _ORIG_LM_CALL(self, *args, **kwargs)
        start = time.time()
        prompt = None
        # DSPy 2.x signature: __call__(self, prompt=None, messages=None, ...)
        if args:
            prompt = args[0]
        else:
            prompt = kwargs.get("prompt") or kwargs.get("messages")
        try:
            result = _ORIG_LM_CALL(self, *args, **kwargs)
            latency_ms = int((time.time() - start) * 1000)
            # Try to extract tokens + raw text from response metadata
            usage = None
            text_out: Any = result
            try:
                # P1 fix: snapshot history via list() to avoid
                # IndexError on concurrent append from multi-threaded
                # GEPA workers; use `is None` not `not` to avoid
                # overwriting empty-list/dict completions (which are
                # falsy but valid).
                if hasattr(self, "history"):
                    hist_snapshot = list(self.history)
                    if hist_snapshot:
                        last = hist_snapshot[-1]
                        usage = last.get("usage") if isinstance(last, dict) else None
                        if text_out is None and isinstance(last, dict):
                            text_out = last.get("response") or last.get("output")
            except Exception:
                pass
            _append_record({
                "ts": _iso(),
                "run_id": rl.run_id,
                "stage": rl.stages[-1].name if rl.stages else None,
                "model": getattr(self, "model", "?"),
                "latency_ms": latency_ms,
                "usage": usage,
                "prompt": _truncate(prompt, 12000),
                "completion": _truncate(text_out, 12000),
                "ok": True,
            })
            # XII clause 2: append-only ~/.sio/usage.log with computed cost
            try:
                from sio.core.cost import estimate_call, record_call  # noqa: PLC0415
                in_tok = int((usage or {}).get("prompt_tokens", 0) or 0)
                out_tok = int((usage or {}).get("completion_tokens", 0) or 0)
                model_name = getattr(self, "model", "?")
                cost = estimate_call(model_name, in_tok, out_tok)
                record_call(
                    model=model_name, role="task_or_reflection",
                    in_tokens=in_tok, out_tokens=out_tok, cost_usd=cost,
                    run_id=rl.run_id, cmd=rl.cmd, latency_ms=latency_ms,
                )
                # Charge the active stage with an LM call AND actual cost
                if rl.stages:
                    rl.stages[-1].add_llm(calls=1, cost_usd=cost)
            except Exception:
                # Cost capture is observability; never crash the pipeline
                if rl.stages:
                    rl.stages[-1].add_llm(calls=1, cost_usd=0.0)
            return result
        except Exception as exc:
            latency_ms = int((time.time() - start) * 1000)
            _append_record({
                "ts": _iso(),
                "run_id": rl.run_id,
                "stage": rl.stages[-1].name if rl.stages else None,
                "model": getattr(self, "model", "?"),
                "latency_ms": latency_ms,
                "prompt": _truncate(prompt, 12000),
                "ok": False,
                "error": _truncate(exc, 2000),
            })
            raise

    dspy.LM.__call__ = _wrapped  # type: ignore[assignment]
    _INSTALLED = True


def uninstall() -> None:
    """Restore the original dspy.LM.__call__."""
    global _INSTALLED, _ORIG_LM_CALL, _CAPTURE_FILE
    if not _INSTALLED or _ORIG_LM_CALL is None:
        return
    try:
        import dspy  # noqa: PLC0415
        dspy.LM.__call__ = _ORIG_LM_CALL
    except ImportError:
        pass
    _INSTALLED = False
    _CAPTURE_FILE = None


def _iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
