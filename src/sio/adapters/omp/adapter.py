"""omp (oh-my-pi) session adapter — reads ~/.omp/agent/sessions/**/*.jsonl.

omp is a fork of pi and writes pi's session format v3, so every entry is
parsed by :mod:`sio.adapters.pi.adapter` (``events_from_entry`` is re-exported
for the search parser). What differs, all handled by that parser already:

* the FIRST line is a padded ``{type:"title", ...}`` record omp rewrites in
  place; the ``{type:"session", version:"3", cwd, ...}`` header is line 2
  (``version`` is the string ``"3"``, not pi's int);
* extra bookkeeping entries (``title``, ``title_change``, ``model_usage``,
  ``custom``) carry no transcript content and fall through the pi parser's
  unrecognised-type branch, so they yield no events;
* files live at ``~/.omp/agent/sessions/<cwd-relative-to-HOME>/<iso-ts>_<uuid>.jsonl``
  (the dir name is not authoritative — the real cwd is in the header).
"""

from __future__ import annotations

from sio.adapters.pi.adapter import PiAdapter, events_from_entry, events_from_lines

__all__ = ["OmpAdapter", "events_from_entry", "events_from_lines"]


class OmpAdapter(PiAdapter):
    """EXTRACT events from an omp session transcript (pi format v3)."""

    agent = "omp"
