"""Cohort tagging primitive — `sio experiment` backend.

PRD: ``~/dev/prd/scratch/sio_autotag_experiments_2026-05-23.md``

Bookmarks a named time window (start_ts → close_ts), snapshots the active
config (CLAUDE.md + skills + rules + settings.json hooks) at start time,
and joins behavior_invocations / error_records / flow_events to the window
at query time. The ``cohort`` name is intentional — the existing
``sio.core.arena.experiment`` module owns the unrelated git-worktree
"experiment" concept.
"""

from sio.core.cohort.models import Experiment, ExperimentRun

__all__ = ["Experiment", "ExperimentRun"]
