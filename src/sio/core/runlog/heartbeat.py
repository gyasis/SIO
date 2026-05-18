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
import logging
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .writer import RunLog, Stage

# Reflection-class model fingerprints (Pro/gpt-5 tier).
_REFLECTION_HINTS = ("gpt-5", "gemini-pro", "claude-opus", "claude-sonnet-4")
# Task-LM fingerprints (Flash/mini tier). If any of these show up, GEPA
# has reached the evaluation phase and is no longer stuck.
_TASK_HINTS = ("flash", "gpt-4o-mini", "ollama", "haiku")

# GEPA log line patterns we mine for live progress (2026-05-18 paired-debate).
# Empirical from a3dd014a stderr capture — these lines emit at iteration
# boundaries and within iterations as the optimizer reports best-found score.
_GEPA_ITER_RE = re.compile(
    r"Iteration (\d+): Selected program \d+ score: ([\d.]+)"
)
_GEPA_VALSET_RE = re.compile(
    r"Iteration (\d+): (?:Best score|Valset pareto front aggregate score|"
    r"Val aggregate for new program): ([\d.]+)"
)
_GEPA_ADAPTER_PARSE_ERR_RE = re.compile(r"AdapterParseError")
_GEPA_TRUNCATION_RE = re.compile(
    r"LM response was truncated due to exceeding max_tokens=\d+"
)
# MIPROv2 log line patterns (extension 2026-05-18). DSPy MIPROv2 emits in
# PERCENTAGE format (64.53) not 0-1 ratio — we convert when capturing.
# Empirical from c28c44fb stderr capture.
_MIPRO_TRIAL_RE = re.compile(r"== Trial (\d+) / (\d+)")
# "Default program score: 64.53" / "Best score so far: 64.53" / etc.
_MIPRO_SCORE_RE = re.compile(
    r"(?:Default program score|Best score so far|"
    r"Trial \d+ score|Score for trial \d+): ([\d.]+)"
)


class _GepaProgressWatcher(logging.Handler):
    """Mines DSPy GEPA log lines for live iteration + best-valset score.

    Hooks into the `dspy.teleprompt.gepa.gepa` logger and the root logger
    (where truncation warnings emit). Maintains:
      - current_iter: latest iteration index seen
      - last_iter_advance_at: monotonic-clock time of last NEW iteration
      - best_valset_score: best valset score reported so far
      - parse_errors_5min: deque of (timestamp,) for AdapterParseError
      - truncations_5min: deque of (timestamp,) for max_tokens warnings

    Threadsafe. Designed so heartbeat thread can read state cheaply.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._lock = threading.Lock()
        # === GEPA state ===
        self.current_iter: int = 0
        self.last_iter_advance_at: float = time.time()
        self.last_iter_score: Optional[float] = None
        self.best_valset_score: Optional[float] = None
        self.score_history: deque = deque(maxlen=10)
        # === MIPRO state (added 2026-05-18 — same observability for MIPRO) ===
        self.current_trial: int = 0
        self.trial_total: int = 0
        self.last_trial_advance_at: float = time.time()
        self.last_trial_score: Optional[float] = None  # 0.0-1.0 (converted from %)
        self.best_trial_score: Optional[float] = None
        self.trial_history: deque = deque(maxlen=10)
        # Rolling 5-min windows; oldest entries evicted in property accessors
        self.parse_errors: deque = deque()
        self.truncations: deque = deque()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            msg = record.getMessage()
        except Exception:
            return
        now = time.time()
        with self._lock:
            m = _GEPA_ITER_RE.search(msg)
            if m:
                idx = int(m.group(1))
                try:
                    iter_score = float(m.group(2))
                except Exception:
                    iter_score = None
                # ALWAYS capture iter_score even if iter index hasn't advanced —
                # GEPA can emit multiple "Selected program" lines per iter as it
                # explores alternatives. The latest score is what's current.
                if iter_score is not None:
                    self.last_iter_score = iter_score
                if idx >= self.current_iter:
                    if idx > self.current_iter:
                        self.current_iter = idx
                        self.last_iter_advance_at = now
                    if iter_score is not None:
                        # Append to history; deque(maxlen=10) drops oldest
                        self.score_history.append((idx, iter_score))
            m2 = _GEPA_VALSET_RE.search(msg)
            if m2:
                try:
                    score = float(m2.group(2))
                    if self.best_valset_score is None or score > self.best_valset_score:
                        self.best_valset_score = score
                except Exception:
                    pass
            # MIPRO trial start
            m3 = _MIPRO_TRIAL_RE.search(msg)
            if m3:
                try:
                    new_trial = int(m3.group(1))
                    total = int(m3.group(2))
                    if new_trial >= self.current_trial:
                        self.current_trial = new_trial
                        self.trial_total = total
                        self.last_trial_advance_at = now
                except Exception:
                    pass
            # MIPRO score (Default program score / Best score so far / etc.)
            # MIPRO emits percentage (e.g. 64.53). Convert to 0-1 ratio.
            m4 = _MIPRO_SCORE_RE.search(msg)
            if m4:
                try:
                    raw = float(m4.group(1))
                    score = raw / 100.0 if raw > 1.5 else raw
                    self.last_trial_score = score
                    if self.best_trial_score is None or score > self.best_trial_score:
                        self.best_trial_score = score
                    if self.current_trial > 0:
                        self.trial_history.append((self.current_trial, score))
                except Exception:
                    pass
            if _GEPA_ADAPTER_PARSE_ERR_RE.search(msg):
                self.parse_errors.append(now)
            if _GEPA_TRUNCATION_RE.search(msg):
                self.truncations.append(now)

    def _evict_old(self, q: deque, window_sec: int = 300) -> None:
        cutoff = time.time() - window_sec
        while q and q[0] < cutoff:
            q.popleft()

    def snapshot(self) -> dict:
        """Returns current state for heartbeat rendering + abort checks."""
        with self._lock:
            self._evict_old(self.parse_errors)
            self._evict_old(self.truncations)
            # Trend: compare latest score to score 3 iters back
            trend = None
            if len(self.score_history) >= 3:
                recent = self.score_history[-1][1]
                older = self.score_history[-3][1]
                diff = recent - older
                if diff > 0.005:
                    trend = "up"
                elif diff < -0.005:
                    trend = "down"
                else:
                    trend = "flat"
            # MIPRO trend (same heuristic: last vs 3-back)
            mipro_trend = None
            if len(self.trial_history) >= 3:
                recent = self.trial_history[-1][1]
                older = self.trial_history[-3][1]
                diff = recent - older
                if diff > 0.005:
                    mipro_trend = "up"
                elif diff < -0.005:
                    mipro_trend = "down"
                else:
                    mipro_trend = "flat"
            # Detect active optimizer
            active = None
            if self.current_iter > 0:
                active = "gepa"
            elif self.current_trial > 0:
                active = "mipro"
            return {
                "active": active,
                # GEPA fields
                "iter": self.current_iter,
                "iter_score": self.last_iter_score,
                "best": self.best_valset_score,
                "trend": trend,
                "history": list(self.score_history),
                "iter_idle_sec": int(time.time() - self.last_iter_advance_at),
                # MIPRO fields (added 2026-05-18)
                "trial": self.current_trial,
                "trial_total": self.trial_total,
                "trial_score": self.last_trial_score,
                "best_trial": self.best_trial_score,
                "mipro_trend": mipro_trend,
                "trial_history": list(self.trial_history),
                "trial_idle_sec": int(time.time() - self.last_trial_advance_at),
                # Shared error counters
                "parse_errors_5min": len(self.parse_errors),
                "truncations_5min": len(self.truncations),
            }


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
        # GEPA live-progress watcher (2026-05-18 paired-debate). Hooks
        # into Python logging so we don't need to scrape files. Killed
        # in __exit__.
        self._gepa = _GepaProgressWatcher()
        self._gepa_attached = False
        # T1-T4 abort tier state (one-shot warnings)
        self._iter_idle_warned_at_8m = False
        self._iter_idle_abort_at_15m = False
        self._parse_err_abort_emitted = False
        self._truncation_abort_emitted = False

    def progress(self) -> None:
        """Call from the stage when work advances. Resets the hung-stage timer."""
        self._last_progress = time.time()

    def _attach_gepa_watcher(self) -> None:
        """Attach _GepaProgressWatcher to relevant loggers. Idempotent."""
        if self._gepa_attached:
            return
        try:
            logging.getLogger("dspy.teleprompt.gepa.gepa").addHandler(self._gepa)
            logging.getLogger("dspy.clients.lm").addHandler(self._gepa)
            logging.getLogger("dspy.adapters.json_adapter").addHandler(self._gepa)
            logging.getLogger().addHandler(self._gepa)  # root catches the rest
            self._gepa_attached = True
        except Exception:
            pass

    def _detach_gepa_watcher(self) -> None:
        if not self._gepa_attached:
            return
        for name in (
            "dspy.teleprompt.gepa.gepa",
            "dspy.clients.lm",
            "dspy.adapters.json_adapter",
            "",  # root
        ):
            try:
                logging.getLogger(name).removeHandler(self._gepa)
            except Exception:
                pass
        self._gepa_attached = False

    def _loop(self) -> None:
        start = time.time()
        last_llm = 0
        # Only attach GEPA watcher when relevant (optimize stage names).
        # Other stages (amplify, curate) don't emit GEPA log lines.
        if "optimize" in self.stage.name.lower():
            self._attach_gepa_watcher()
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
            # GEPA live-progress suffix (origin 2026-05-18 paired-debate).
            # Surfaces iteration index + best valset score + abort signals
            # so the operator can see GEPA's "score-so-far" instead of
            # waiting for a black-box end-of-run number.
            opt_extra = ""
            if self._gepa_attached:
                snap = self._gepa.snapshot()
                # Stash on stage so external readers (sio gepa-status, agent
                # in conversation) see the same data as stderr. Updated each
                # heartbeat tick (~30s) — fresh enough for human eyeballing.
                self.stage.gepa_snapshot = snap
                # GEPA rendering — only when GEPA is active
                if snap.get("active") == "gepa":
                    opt_extra = f" gepa_iter={snap['iter']}"
                    if snap["iter_score"] is not None:
                        opt_extra += f" iter_score={snap['iter_score']:.4f}"
                    if snap["best"] is not None:
                        opt_extra += f" best_valset={snap['best']:.4f}"
                    if snap["trend"]:
                        arrow = {"up": "↑", "down": "↓", "flat": "→"}[snap["trend"]]
                        opt_extra += f" trend={arrow}"
                    if snap["iter_idle_sec"] > 60:
                        opt_extra += f" iter_idle={snap['iter_idle_sec']}s"
                # MIPRO rendering — only when MIPRO is active (no GEPA iters seen)
                elif snap.get("active") == "mipro":
                    opt_extra = f" mipro_trial={snap['trial']}/{snap['trial_total']}"
                    if snap["trial_score"] is not None:
                        opt_extra += f" trial_score={snap['trial_score']:.4f}"
                    if snap["best_trial"] is not None:
                        opt_extra += f" best_trial={snap['best_trial']:.4f}"
                    if snap.get("mipro_trend"):
                        arrow = {"up": "↑", "down": "↓", "flat": "→"}[snap["mipro_trend"]]
                        opt_extra += f" trend={arrow}"
                    if snap["trial_idle_sec"] > 120:
                        opt_extra += f" trial_idle={snap['trial_idle_sec']}s"
                # Always show error counters when present, regardless of optimizer
                if snap["parse_errors_5min"]:
                    opt_extra += f" parse_err_5m={snap['parse_errors_5min']}"
                if snap["truncations_5min"]:
                    opt_extra += f" trunc_5m={snap['truncations_5min']}"
            gepa_extra = opt_extra  # keep var name compatibility below
            print(
                f"[HB run={self.run.run_id} stage={self.stage.name} "
                f"elapsed={elapsed}s since_progress={since_progress}s "
                f"llm_calls={self.stage.llm_calls}{extra}{gepa_extra}]",
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

            # T1-T4 abort tiers (origin 2026-05-18 paired-debate). Tighter
            # heuristics calibrated from today's stuck GEPA: at iter 17 the
            # task-LM started emitting truncated/malformed outputs and never
            # advanced. Catching this within 30s-15min saves ~$0.20-0.50 vs
            # the 40-min reflection-stuck backstop.
            try:
                self._check_abort_tiers()
            except Exception:
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

    def _check_abort_tiers(self) -> None:
        """T1-T4 abort tiers (2026-05-18 paired-debate).

        T1: iter-idle 8min  → WARN (one-shot)
        T2: iter-idle 15min → CRITICAL ABORT signal (one-shot)
        T3: >=3 AdapterParseError in 5min → CRITICAL ABORT signal (one-shot)
        T4: >=3 max_tokens truncations in 5min → CRITICAL ABORT signal (one-shot)

        We do NOT auto-SIGTERM (Python signals across threads are fragile).
        Operator decides — same philosophy as the T5 reflection-stuck
        backstop. The warnings appear in run.warns AND on stderr.
        """
        if not self._gepa_attached:
            return
        snap = self._gepa.snapshot()
        # T1 / T2: iteration idle (only after at least one iteration started)
        if snap["iter"] > 0:
            idle = snap["iter_idle_sec"]
            if idle >= 15 * 60 and not self._iter_idle_abort_at_15m:
                self._iter_idle_abort_at_15m = True
                self.run.warn(
                    "GEPA_ITER_STALLED_CRITICAL",
                    f"GEPA iteration {snap['iter']} has not advanced in "
                    f"{idle//60}m. Best-valset-so-far={snap['best']}. "
                    f"Healthy iterations cycle every 1-3 min. Consider "
                    f"Ctrl+C — likely cause: task-LM emitting "
                    f"malformed/truncated outputs (check parse_err_5m + "
                    f"trunc_5m in HB line).",
                    stage=self.stage.name,
                )
                print(
                    f"[CRITICAL run={self.run.run_id}] T2 GEPA_ITER_STALLED — "
                    f"iter={snap['iter']} idle={idle//60}m. Consider Ctrl+C.",
                    file=sys.stderr, flush=True,
                )
            elif idle >= 8 * 60 and not self._iter_idle_warned_at_8m:
                self._iter_idle_warned_at_8m = True
                self.run.warn(
                    "GEPA_ITER_STALL_WARN",
                    f"GEPA iteration {snap['iter']} idle for {idle//60}m. "
                    f"Healthy cadence is 1-3min/iter. Will re-flag at 15m.",
                    stage=self.stage.name,
                )
        # T3: parse-error streak
        if snap["parse_errors_5min"] >= 3 and not self._parse_err_abort_emitted:
            self._parse_err_abort_emitted = True
            self.run.warn(
                "GEPA_ADAPTER_PARSE_STREAK",
                f"{snap['parse_errors_5min']} AdapterParseError in last 5min. "
                f"Task-LM is emitting outputs missing required fields. "
                f"GEPA cannot reflect on garbage; the loop is effectively "
                f"dead. Likely fix: raise task-LM max_tokens or simplify "
                f"the signature output schema.",
                stage=self.stage.name,
            )
            print(
                f"[CRITICAL run={self.run.run_id}] T3 PARSE_ERR_STREAK — "
                f"{snap['parse_errors_5min']} AdapterParseError / 5min. "
                f"Consider Ctrl+C.",
                file=sys.stderr, flush=True,
            )
        # T4: truncation streak
        if snap["truncations_5min"] >= 3 and not self._truncation_abort_emitted:
            self._truncation_abort_emitted = True
            self.run.warn(
                "GEPA_TRUNCATION_STREAK",
                f"{snap['truncations_5min']} max_tokens truncation warnings "
                f"in last 5min. Task-LM cannot complete responses. Likely "
                f"fix: raise the LM's max_tokens (default 4096 too small "
                f"for verbose rule outputs).",
                stage=self.stage.name,
            )
            print(
                f"[CRITICAL run={self.run.run_id}] T4 TRUNCATION_STREAK — "
                f"{snap['truncations_5min']} truncation warns / 5min. "
                f"Consider Ctrl+C.",
                file=sys.stderr, flush=True,
            )

    def __enter__(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._detach_gepa_watcher()
