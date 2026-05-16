"""Per-invocation JSON run-log writer (Principle XIII).

Every `sio` CLI invocation gets a RunLog object held on a contextvar.
The decorator (decorator.py) starts/stops it. Stages, warnings, errors,
and DSPy interactions append to it. On exit, it flushes one JSON file to
``~/.sio/runs/<UTC-ISO>_<cmd>_<short-id>.json``.

The watch-movement-under-glass principle: every gear visible. Stages
emit rows_in/rows_out so the next debugger (human or agent) can see
exactly which one stopped.
"""
from __future__ import annotations

import contextvars
import json
import os
import secrets
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_RUNS_DIR = Path.home() / ".sio" / "runs"
_RUNS_DIR.mkdir(parents=True, exist_ok=True)

# Coverage threshold below which a COVERAGE_DROP warning fires
_COVERAGE_THRESHOLD = 0.8

_current: contextvars.ContextVar[Optional["RunLog"]] = contextvars.ContextVar(
    "sio_runlog_current", default=None
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ts_compact() -> str:
    # 2026-05-16T07-41-43Z  (filename-safe)
    return _utc_iso().replace(":", "-")


class Stage:
    """One pipeline stage inside a RunLog."""

    def __init__(self, parent: "RunLog", name: str):
        self.parent = parent
        self.name = name
        self.start_ts = time.time()
        self.end_ts: Optional[float] = None
        self.rows_in: Optional[int] = None
        self.rows_out: Optional[int] = None
        self.llm_calls: int = 0
        self.llm_cost_usd: float = 0.0
        self.heartbeats: int = 0
        self.notes: list[str] = []

    def set_rows(self, rows_in: int, rows_out: int) -> None:
        self.rows_in = rows_in
        self.rows_out = rows_out
        if rows_in and rows_out / rows_in < _COVERAGE_THRESHOLD:
            self.parent.warn(
                "COVERAGE_DROP",
                f"{self.name}: rows_out/rows_in = {rows_out}/{rows_in} "
                f"= {rows_out/rows_in:.2%} < {_COVERAGE_THRESHOLD:.0%}",
                stage=self.name,
            )

    def add_llm(self, calls: int = 1, cost_usd: float = 0.0) -> None:
        self.llm_calls += calls
        self.llm_cost_usd += cost_usd

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ms": int(((self.end_ts or time.time()) - self.start_ts) * 1000),
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "llm_calls": self.llm_calls,
            "llm_cost_usd": round(self.llm_cost_usd, 6),
            "heartbeats": self.heartbeats,
            "notes": self.notes,
        }


class RunLog:
    """Per-invocation log; one file at ~/.sio/runs/."""

    def __init__(self, cmd: str, argv: list[str]):
        self.run_id = secrets.token_hex(4)
        self.cmd = cmd
        self.argv = argv
        self.start_ts = _utc_iso()
        self.start_mono = time.time()
        self.end_ts: Optional[str] = None
        self.exit_code: Optional[int] = None
        self.exit_class: Optional[str] = None  # "ok" | "partial" | "error"
        self.stages: list[Stage] = []
        self.warnings: list[dict] = []
        self.errors: list[dict] = []
        self.outputs: dict[str, Any] = {}
        self.pid = os.getpid()
        self._path = _RUNS_DIR / f"{_ts_compact()}_{cmd}_{self.run_id}.json"
        # P1 fix 2026-05-16: protects warnings/stages/errors lists against
        # concurrent mutation from heartbeat daemon thread vs main-thread flush.
        # Audit-confirmed bug: to_dict() iterates self.stages/warnings via list
        # comprehension while HB thread .appends, causing RuntimeError:
        # "list changed size during iteration".
        self._lock = threading.RLock()
        # P1 fix: idempotent finalize.
        self._finalized = False

    # ---- stage API ---------------------------------------------------
    @contextmanager
    def stage(self, name: str):
        s = Stage(self, name)
        self.stages.append(s)
        try:
            yield s
        finally:
            s.end_ts = time.time()
            # Incremental flush after each stage so a SIGKILL still
            # leaves the last completed stage visible.
            self._flush_partial()

    # ---- event API (thread-safe per P1 fix) --------------------------
    def warn(self, code: str, msg: str, stage: Optional[str] = None) -> None:
        entry = {
            "code": code,
            "msg": str(msg)[:500],
            "stage": stage,
            "ts": _utc_iso(),
        }
        with self._lock:
            self.warnings.append(entry)
        print(
            f"[WARN run={self.run_id}"
            f"{f' stage={stage}' if stage else ''}] {code}: {msg}",
            file=sys.stderr,
        )

    def error(self, code: str, exc: BaseException, stage: Optional[str] = None) -> None:
        entry = {
            "code": code,
            "msg": str(exc)[:500],
            "type": type(exc).__name__,
            "stage": stage,
            "trace": traceback.format_exc()[:4000],
            "ts": _utc_iso(),
        }
        with self._lock:
            self.errors.append(entry)
        print(
            f"[ERROR run={self.run_id}"
            f"{f' stage={stage}' if stage else ''}] {code}: {exc}",
            file=sys.stderr,
        )

    def output(self, key: str, value: Any) -> None:
        with self._lock:
            self.outputs[key] = value

    # ---- exit class --------------------------------------------------
    def compute_exit_class(self, raised: bool) -> str:
        if raised or self.errors:
            return "error"
        # partial: any COVERAGE_DROP / HUNG_STAGE / GEN_FAILURE / amp
        # judge filter dropped > 0 rows
        partial_codes = {"COVERAGE_DROP", "HUNG_STAGE", "GEN_FAILURE",
                         "DSPY_PARSE_FAILED", "JUDGE_DROPPED"}
        if any(w["code"] in partial_codes for w in self.warnings):
            return "partial"
        return "ok"

    # ---- flush -------------------------------------------------------
    def _flush_partial(self) -> None:
        """Write current state — called after every stage. THREAD-SAFE.

        P1 fix 2026-05-16: catches ALL exceptions (was only OSError, missing
        the RuntimeError from concurrent list mutation by HB thread).
        Snapshots state under lock to avoid mid-iteration size change.
        """
        try:
            with self._lock:
                snapshot = self.to_dict()
            self._path.write_text(json.dumps(snapshot, indent=2))
        except Exception:
            # Bare Exception is intentional: a flush error MUST NEVER take
            # down the user's pipeline. Logging is a side-effect.
            pass

    def finalize(self, exit_code: int, raised: bool = False) -> int:
        # P1 fix: idempotent. Double-finalize was producing two [RUN] lines
        # on the SystemExit path.
        if self._finalized:
            return self.exit_code if self.exit_code is not None else exit_code
        self._finalized = True
        self.end_ts = _utc_iso()
        self.exit_code = exit_code
        # Override exit code for partial-success
        if not raised and self.errors:
            self.exit_class = "error"
            final_code = 1 if not exit_code else exit_code
        elif raised:
            self.exit_class = "error"
            final_code = exit_code if exit_code else 1
        else:
            self.exit_class = self.compute_exit_class(raised)
            if self.exit_class == "partial" and exit_code == 0:
                final_code = 3
            else:
                final_code = exit_code
        self.exit_code = final_code
        self._flush_partial()
        # one-line summary to stderr
        print(
            f"[RUN run={self.run_id} cmd={self.cmd} "
            f"class={self.exit_class} exit={final_code} "
            f"warns={len(self.warnings)} errs={len(self.errors)} "
            f"file={self._path}]",
            file=sys.stderr,
        )
        return final_code

    def to_dict(self) -> dict:
        # P1 fix: snapshot mutable lists via list() copy to prevent
        # iteration-during-mutation errors from heartbeat thread.
        return {
            "run_id": self.run_id,
            "cmd": self.cmd,
            "argv": list(self.argv),
            "pid": self.pid,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "exit_code": self.exit_code,
            "exit_class": self.exit_class,
            "elapsed_sec": round(time.time() - self.start_mono, 3),
            "stages": [s.to_dict() for s in list(self.stages)],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "outputs": dict(self.outputs),
        }


def current() -> Optional[RunLog]:
    """Return the active RunLog if a @runlogged context is running."""
    return _current.get()


def set_current(rl: Optional[RunLog]) -> contextvars.Token:
    return _current.set(rl)


def reset_current(tok: contextvars.Token) -> None:
    _current.reset(tok)
