"""Threaded heartbeat emitter (Principle XIII clause 6).

Long-running stages emit `[HB run=... stage=... elapsed=Ns ...]` to stderr
every N seconds so the user/agent knows the process is alive vs hung. No
artificial SIGTERM ceilings — heartbeats let the operator decide.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Optional

from .writer import RunLog, Stage


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
            if since_progress > self.hung_after:
                self.run.warn(
                    "HUNG_STAGE",
                    f"no progress for {since_progress}s",
                    stage=self.stage.name,
                )
                self._last_progress = time.time()  # reset to avoid spam

    def __enter__(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
