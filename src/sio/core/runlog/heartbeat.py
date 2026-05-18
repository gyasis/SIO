"""Threaded heartbeat emitter (Principle XIII clause 6).

Long-running stages emit `[HB run=... stage=... elapsed=Ns ...]` to stderr
every N seconds so the user/agent knows the process is alive vs hung. No
artificial SIGTERM ceilings — heartbeats let the operator decide.

2026-05-18 extension: stuck-in-reflection detector for GEPA runs. Reads
the active dspy_capture sidecar each tick to detect the failure mode
where reflection-LM calls accumulate but task-LM calls never appear.
Empirical basis: today's GEPA on a 93-row dataset spent 58 min producing
28 reflection (gpt-5) calls and ZERO eval (Flash) calls before timing
out and burning $1.11 with no DB row. See PRD optimizer_ladder_2026-05-16
"GEPA INVESTIGATION CLOSED" note for the full diagnosis. The amplify-
first gate (commit d886078) prevents this pre-flight; this monitor
catches it in-flight if a user runs with --skip-amplify-gate.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .writer import RunLog, Stage


# Reflection-class model fingerprints (Pro/gpt-5 tier).
_REFLECTION_HINTS = ("gpt-5", "gemini-pro", "claude-opus", "claude-sonnet-4")
# Task-LM fingerprints (Flash/mini tier). If any of these show up, GEPA
# has reached the evaluation phase and is no longer stuck.
_TASK_HINTS = ("flash", "gpt-4o-mini", "ollama", "haiku")


class Heartbeat:
    """Drop-in context manager. Spawns a daemon thread."""

    def __init__(
        self,
        run: RunLog,
        stage: Stage,
        interval: int = 30,
        hung_after: int = 300,
    ):
        self.run = run
        self.stage = stage
        self.interval = interval
        self.hung_after = hung_after
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_progress = time.time()
        # Stuck-in-reflection state. WARN at 15 min, ABORT-SIGNAL at 40 min.
        # Thresholds in seconds.
        self._stuck_warn_at = 15 * 60
        self._stuck_critical_at = 40 * 60
        self._stuck_warned = False
        self._stuck_critical_emitted = False

    def progress(self) -> None:
        """Call from the stage when work advances. Resets the hung-stage timer."""
        self._last_progress = time.time()

    def _loop(self) -> None:
        start = time.time()
        last_llm = 0
        while not self._stop.wait(self.interval):
            elapsed = int(time.time() - start)
            since_progress = int(time.time() - self._last_progress)
            self.stage.heartbeats += 1
            # P2 fix 2026-05-16: bump progress-marker when llm_calls advances,
            # avoiding spurious HUNG_STAGE during long active runs.
            if self.stage.llm_calls > last_llm:
                self._last_progress = time.time()
                last_llm = self.stage.llm_calls
                since_progress = 0
            # Build progress/eta suffix if stage has set_progress() been called
            extra = ""
            if (self.stage.progress_current is not None
                    and self.stage.progress_total):
                cur = self.stage.progress_current
                tot = self.stage.progress_total
                frac = cur / tot if tot else 0.0
                eta = int(elapsed * (1 - frac) / frac) if frac > 0 else None
                extra = f" progress={cur}/{tot} ({frac:.0%})"
                if eta is not None:
                    extra += f" eta={eta//60}m{eta%60:02d}s"
            print(
                f"[HB run={self.run.run_id} stage={self.stage.name} "
                f"elapsed={elapsed}s since_progress={since_progress}s "
                f"llm_calls={self.stage.llm_calls}{extra}]",
                file=sys.stderr,
                flush=True,
            )
            # 2026-05-16 fix: also flush the run-log file every heartbeat so
            # `sio runs <id>` works DURING long runs, not just after.
            try:
                self.run._flush_partial()
            except Exception:
                pass
            if since_progress > self.hung_after:
                self.run.warn(
                    "HUNG_STAGE",
                    f"no progress for {since_progress}s",
                    stage=self.stage.name,
                )
                self._last_progress = time.time()  # reset to avoid spam

            # Stuck-in-reflection detector (Principle XIII observability).
            # Reads the dspy_capture sidecar (peer to the main runlog file)
            # and counts calls by model class. If only reflection-class
            # models appear past the threshold, the operator hears about it.
            try:
                self._check_stuck_reflection(elapsed)
            except Exception:
                # Detector failure must never crash the heartbeat thread.
                pass

    def _check_stuck_reflection(self, elapsed: int) -> None:
        """Inspect dspy_capture sidecar; warn if only reflection-class LM
        calls have happened past the threshold.

        WARN at 15 min wall-clock with reflection calls and zero task calls.
        CRITICAL at 40 min — explicit "this run is stuck, consider Ctrl+C
        to abort and save budget" message.

        We do NOT auto-SIGTERM the process from this daemon thread because
        Python signals across threads are fragile and a false-positive
        abort on a slow-but-legitimate run (#15 took 41 min before its
        first task call) would be worse than the false-negative cost.
        Operator decides.
        """
        if elapsed < self._stuck_warn_at:
            return
        if self._stuck_critical_emitted:
            return  # already warned at critical; nothing more to say
        # Locate the sidecar — peer to RunLog's main JSON file
        try:
            base = self.run._path  # type: ignore[attr-defined]
        except Exception:
            return
        sidecar = Path(base).with_name(Path(base).stem + "_dspy.jsonl")
        if not sidecar.exists():
            return
        reflection_calls = 0
        task_calls = 0
        try:
            with sidecar.open() as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    m = (d.get("model") or "").lower()
                    if any(h in m for h in _TASK_HINTS):
                        task_calls += 1
                    elif any(h in m for h in _REFLECTION_HINTS):
                        reflection_calls += 1
        except Exception:
            return
        # Stuck pattern: reflection calls exist, task calls don't
        if reflection_calls < 3:
            return  # not yet meaningful
        if task_calls > 0:
            return  # not stuck — task LM has fired
        # Stuck-in-reflection detected.
        if elapsed >= self._stuck_critical_at and not self._stuck_critical_emitted:
            self.run.warn(
                "REFLECTION_STUCK_CRITICAL",
                f"{reflection_calls} reflection-LM calls, 0 task-LM calls "
                f"after {elapsed//60}m. GEPA appears stuck in reflection "
                f"phase (won't reach evaluation). Empirical: today's GEPA "
                f"on 93-row dataset showed this pattern and burned $1.11. "
                f"Consider Ctrl+C to abort and save budget. Likely fix: "
                f"amplify dataset to >=300 rows before retry.",
                stage=self.stage.name,
            )
            print(
                f"[CRITICAL run={self.run.run_id} stage={self.stage.name}] "
                f"REFLECTION_STUCK — {reflection_calls} reflection / 0 task "
                f"calls after {elapsed//60}m. Consider Ctrl+C.",
                file=sys.stderr,
                flush=True,
            )
            self._stuck_critical_emitted = True
        elif not self._stuck_warned:
            self.run.warn(
                "REFLECTION_STUCK",
                f"{reflection_calls} reflection-LM calls, 0 task-LM calls "
                f"after {elapsed//60}m. GEPA may be stuck in reflection. "
                f"Will re-flag at {self._stuck_critical_at//60}m if no task "
                f"calls appear by then. Working GEPA runs typically see "
                f"first task call by ~41m (#15 reference).",
                stage=self.stage.name,
            )
            self._stuck_warned = True

    def __enter__(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
