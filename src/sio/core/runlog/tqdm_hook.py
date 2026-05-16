"""tqdm progress hook (Principle XIII clause 6 — auto-ETA).

Monkey-patches `tqdm.tqdm.update()` so every tqdm-emitted progress tick from
DSPy GEPA / MIPROv2 / BootstrapFewShot automatically writes
`stage.progress_current` / `stage.progress_total` on the active RunLog's
latest stage. The heartbeat then surfaces ETA + percentage on stderr +
`sio runs <id>`.

Activation is implicit on @runlogged via dspy_capture.install() flow (added
to decorator.py).
"""
from __future__ import annotations

from typing import Optional

from .writer import current

_INSTALLED = False
_ORIG_UPDATE = None
_ORIG_INIT = None


def install() -> None:
    """Patch tqdm.tqdm to forward progress to the active RunLog stage."""
    global _INSTALLED, _ORIG_UPDATE, _ORIG_INIT
    if _INSTALLED:
        return
    try:
        import tqdm as _tqdm  # noqa: PLC0415
        from tqdm import tqdm as _tqdm_cls  # noqa: PLC0415
    except ImportError:
        return

    _ORIG_UPDATE = _tqdm_cls.update
    _ORIG_INIT = _tqdm_cls.__init__

    def _patched_init(self, *args, **kwargs):
        _ORIG_INIT(self, *args, **kwargs)
        _maybe_record(self)

    def _patched_update(self, n=1):
        result = _ORIG_UPDATE(self, n)
        _maybe_record(self)
        return result

    _tqdm_cls.__init__ = _patched_init
    _tqdm_cls.update = _patched_update
    _INSTALLED = True


def _maybe_record(t) -> None:
    """If a RunLog stage is active, write its progress fields.

    2026-05-16 fix: prefer the LARGEST tqdm bar (outer optimization budget,
    e.g. 250 rollouts in GEPA) over inner per-eval bars (e.g. 50 valset items
    that reset to 0 each minibatch). Updates only if the new bar's total is
    >= the existing one, so inner-loop bars don't clobber outer progress.
    """
    rl = current()
    if rl is None or not rl.stages:
        return
    try:
        stage = rl.stages[-1]
        total = getattr(t, "total", None)
        n = getattr(t, "n", None)
        if total is None or n is None or total <= 0:
            return
        # Only overwrite if new total is at least as big as current — keeps
        # outer-loop bar (e.g. "250 rollouts") visible while inner-loop bars
        # (e.g. "50 valset items") flicker beneath it.
        existing_total = stage.progress_total
        if existing_total is None or int(total) >= int(existing_total):
            stage.set_progress(int(n), int(total))
    except Exception:
        pass  # observability must NEVER kill the user's pipeline


def uninstall() -> None:
    """Restore original tqdm methods."""
    global _INSTALLED, _ORIG_UPDATE, _ORIG_INIT
    if not _INSTALLED:
        return
    try:
        from tqdm import tqdm as _tqdm_cls  # noqa: PLC0415
        if _ORIG_UPDATE:
            _tqdm_cls.update = _ORIG_UPDATE
        if _ORIG_INIT:
            _tqdm_cls.__init__ = _ORIG_INIT
    except ImportError:
        pass
    _INSTALLED = False
