"""Typed dataclasses for the cohort tagging primitive.

Mirrors the ``experiments`` and ``experiment_runs`` SQLite tables in
``sio.core.db.schema`` (T001 / T002). Conversions to/from rows live in
``sio.core.cohort.store`` (T010).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

ExperimentStatus = Literal["open", "closed"]


@dataclass(frozen=True)
class Experiment:
    """A named cohort window with an optional config-hash snapshot.

    Attributes mirror the ``experiments`` table columns exactly.
    """

    name: str
    start_ts: str
    close_ts: Optional[str] = None
    note: Optional[str] = None
    config_hash: Optional[str] = None
    project: Optional[str] = None
    status: ExperimentStatus = "open"
    id: Optional[int] = None


@dataclass(frozen=True)
class ExperimentRun:
    """An event (row in ``source_table``) joined to an experiment by window.

    Populated by the resolver in ``sio.core.cohort.window`` (T016) at query
    time — there is no destructive in-place tagging of the source tables
    (Q3 decision, PRD §5).
    """

    event_id: int
    experiment_name: str
    source_table: str
    id: Optional[int] = None
