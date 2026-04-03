"""Autonomous optimisation loop (FR-042 to FR-045).

Runs mine -> cluster -> grade -> generate -> assert -> experiment -> validate
in a loop with configurable interval and safety limits.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path

from sio.core.arena.assertions import run_assertions
from sio.core.arena.txlog import TxLog
from sio.core.config import SIOConfig

logger = logging.getLogger(__name__)

_SENTINEL_PATH = os.path.expanduser("~/.sio/autoresearch.stop")


class AutoResearchLoop:
    """Autonomous research loop that mines errors and creates experiments.

    Safety limits (FR-043):
    - ``max_experiments``: Maximum concurrent experiments (default 3).
    - Maximum 1 new rule per cycle.
    - Budget enforcement on every application.
    - Human approval gate before any promotion.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        config: SIOConfig,
    ) -> None:
        self._db = db
        self._config = config
        self._txlog = TxLog(db)
        self._cycle = 0
        self._running = False

    @property
    def txlog(self) -> TxLog:
        """Expose the transaction log for external queries."""
        return self._txlog

    # ------------------------------------------------------------------
    # Single cycle
    # ------------------------------------------------------------------

    def run_cycle(self) -> dict:
        """Execute a single autoresearch cycle.

        Steps: mine -> cluster -> grade -> generate -> assert ->
               experiment_create -> validate.

        Returns:
            Dict summarising the cycle outcome.
        """
        self._cycle += 1
        cycle = self._cycle
        result: dict = {"cycle": cycle, "actions": []}

        # --- Check stop sentinel ---
        if os.path.exists(_SENTINEL_PATH):
            self._txlog.append(cycle, "stop", "success", "Stop sentinel found")
            result["stopped"] = True
            return result

        # --- Safety: max experiments ---
        active = self._txlog.active_experiment_count()
        if active >= self._config.max_experiments:
            self._txlog.append(
                cycle, "stop", "skipped",
                f"Max experiments reached ({active}/{self._config.max_experiments})",
            )
            result["skipped"] = True
            result["reason"] = "max_experiments"
            return result

        # --- Mine ---
        mine_result = self._step_mine(cycle)
        result["actions"].append(("mine", mine_result))
        if not mine_result.get("errors_found", 0):
            return result

        # --- Cluster ---
        cluster_result = self._step_cluster(cycle, mine_result)
        result["actions"].append(("cluster", cluster_result))
        if not cluster_result.get("patterns"):
            return result

        # --- Grade ---
        grade_result = self._step_grade(cycle, cluster_result)
        result["actions"].append(("grade", grade_result))
        if not grade_result.get("strong_patterns"):
            return result

        # --- Generate (max 1 rule per cycle) ---
        gen_result = self._step_generate(cycle, grade_result)
        result["actions"].append(("generate", gen_result))
        if not gen_result.get("suggestion_id"):
            return result

        # --- Assert ---
        assert_result = self._step_assert(cycle, gen_result)
        result["actions"].append(("assert", assert_result))
        if not assert_result.get("passed"):
            return result

        # --- Experiment create ---
        exp_result = self._step_experiment_create(cycle, gen_result)
        result["actions"].append(("experiment_create", exp_result))

        return result

    # ------------------------------------------------------------------
    # Pipeline steps (each logs to txlog)
    # ------------------------------------------------------------------

    def _step_mine(self, cycle: int) -> dict:
        """Mine recent sessions for errors."""
        try:
            from sio.mining.pipeline import run_mine

            source_dirs = []
            specstory_dir = Path(os.path.expanduser("~/.specstory/history"))
            jsonl_dir = Path(os.path.expanduser("~/.claude/projects"))
            if specstory_dir.exists():
                source_dirs.append(specstory_dir)
            if jsonl_dir.exists():
                source_dirs.append(jsonl_dir)

            if not source_dirs:
                self._txlog.append(
                    cycle, "mine", "skipped", "No source directories found",
                )
                return {"errors_found": 0}

            mine_out = run_mine(
                self._db, source_dirs, "7 days", "both", None,
            )
            errors_found = mine_out.get("errors_found", 0)
            self._txlog.append(
                cycle, "mine", "success",
                f"Mined {mine_out.get('total_files_scanned', 0)} sessions, "
                f"{errors_found} errors",
            )
            return {"errors_found": errors_found}
        except Exception as exc:
            self._txlog.append(
                cycle, "mine", "failure", f"Error: {exc}",
            )
            logger.exception("Mine step failed")
            return {"errors_found": 0}

    def _step_cluster(self, cycle: int, mine_result: dict) -> dict:
        """Cluster errors into patterns."""
        try:
            from sio.clustering.pattern_clusterer import cluster_errors
            from sio.core.db.queries import get_error_records

            errors = get_error_records(self._db)
            if not errors:
                self._txlog.append(
                    cycle, "cluster", "skipped", "No errors to cluster",
                )
                return {"patterns": []}

            patterns = cluster_errors(errors)
            self._txlog.append(
                cycle, "cluster", "success",
                f"Found {len(patterns)} patterns",
            )
            return {"patterns": patterns}
        except Exception as exc:
            self._txlog.append(
                cycle, "cluster", "failure", f"Error: {exc}",
            )
            logger.exception("Cluster step failed")
            return {"patterns": []}

    def _step_grade(self, cycle: int, cluster_result: dict) -> dict:
        """Grade patterns to find strong candidates."""
        try:
            from sio.clustering.grader import run_grading
            from sio.clustering.ranker import rank_patterns

            # Update lifecycle grades (emerging/strong/established/declining)
            run_grading(self._db, self._config)

            patterns = cluster_result.get("patterns", [])
            ranked = rank_patterns(patterns)
            # Filter to patterns graded as "strong" with sufficient occurrences
            strong = [
                p for p in ranked
                if p.get("grade") in ("strong", "established")
                and p.get("error_count", 0) >= self._config.min_pattern_occurrences
            ]
            self._txlog.append(
                cycle, "grade", "success",
                f"{len(strong)} strong patterns from {len(ranked)} total",
            )
            return {"strong_patterns": strong}
        except Exception as exc:
            self._txlog.append(
                cycle, "grade", "failure", f"Error: {exc}",
            )
            logger.exception("Grade step failed")
            return {"strong_patterns": []}

    def _step_generate(self, cycle: int, grade_result: dict) -> dict:
        """Generate a suggestion from the top strong pattern (max 1/cycle)."""
        try:
            from sio.suggestions.generator import generate_suggestions

            strong = grade_result.get("strong_patterns", [])
            if not strong:
                self._txlog.append(
                    cycle, "generate", "skipped", "No strong patterns",
                )
                return {}

            # Take only the top pattern (max 1 rule per cycle)
            top = strong[0]
            suggestions = generate_suggestions(self._db, [top])
            if not suggestions:
                self._txlog.append(
                    cycle, "generate", "skipped",
                    "Generator produced no suggestions",
                )
                return {}

            sug = suggestions[0]
            sug_id = sug.get("id") or sug.get("suggestion_id")
            self._txlog.append(
                cycle, "generate", "success",
                f"Generated suggestion #{sug_id}",
                suggestion_id=sug_id,
            )
            return {"suggestion_id": sug_id, "suggestion": sug}
        except Exception as exc:
            self._txlog.append(
                cycle, "generate", "failure", f"Error: {exc}",
            )
            logger.exception("Generate step failed")
            return {}

    def _step_assert(self, cycle: int, gen_result: dict) -> dict:
        """Run assertions on the generated suggestion."""
        try:
            sug = gen_result.get("suggestion", {})
            sug_id = gen_result.get("suggestion_id")

            # Build context for assertions
            context = {
                "suggestion": sug,
                "existing_suggestions": self._get_active_suggestions(),
                "pattern": sug,
                "config": self._config,
                "file_path": sug.get("target_file", ""),
            }

            assertion_names = ["no_collisions", "budget_within_limits"]
            if sug.get("confidence") or sug.get("rank_score"):
                assertion_names.append("confidence_above_threshold")

            results = run_assertions(assertion_names, context)
            all_passed = all(r.passed for r in results)

            assertion_dict = {r.name: r.passed for r in results}
            self._txlog.append(
                cycle, "assert",
                "success" if all_passed else "failure",
                f"Assertions: {assertion_dict}",
                suggestion_id=sug_id,
                assertion_results=assertion_dict,
            )
            return {"passed": all_passed, "results": results}
        except Exception as exc:
            self._txlog.append(
                cycle, "assert", "failure", f"Error: {exc}",
            )
            logger.exception("Assert step failed")
            return {"passed": False}

    def _step_experiment_create(self, cycle: int, gen_result: dict) -> dict:
        """Create an experiment branch for the suggestion."""
        try:
            from sio.core.arena.experiment import create_experiment

            sug_id = gen_result.get("suggestion_id")
            if sug_id is None:
                self._txlog.append(
                    cycle, "experiment_create", "skipped",
                    "No suggestion_id",
                )
                return {}

            branch = create_experiment(sug_id, self._db)
            self._txlog.append(
                cycle, "experiment_create", "success",
                f"Created experiment branch {branch}",
                suggestion_id=sug_id,
                experiment_branch=branch,
            )
            return {"branch": branch, "suggestion_id": sug_id}
        except Exception as exc:
            self._txlog.append(
                cycle, "experiment_create", "failure", f"Error: {exc}",
            )
            logger.exception("Experiment create failed")
            return {}

    # ------------------------------------------------------------------
    # Loop control
    # ------------------------------------------------------------------

    def start(
        self,
        interval_minutes: int = 30,
        max_cycles: int | None = None,
    ) -> None:
        """Run the autoresearch loop as a foreground process.

        Args:
            interval_minutes: Minutes to sleep between cycles.
            max_cycles: Maximum number of cycles (None = unlimited).
        """
        self._running = True

        # Remove stale stop sentinel
        if os.path.exists(_SENTINEL_PATH):
            os.remove(_SENTINEL_PATH)

        logger.info(
            "AutoResearch loop started (interval=%dm, max_cycles=%s)",
            interval_minutes,
            max_cycles or "unlimited",
        )

        cycles_run = 0
        while self._running:
            # Check stop sentinel at top of loop
            if os.path.exists(_SENTINEL_PATH):
                logger.info("Stop sentinel detected, exiting loop")
                self._txlog.append(
                    self._cycle + 1, "stop", "success",
                    "Stop sentinel found at loop start",
                )
                break

            if max_cycles is not None and cycles_run >= max_cycles:
                logger.info("Max cycles (%d) reached, exiting", max_cycles)
                break

            result = self.run_cycle()
            cycles_run += 1

            if result.get("stopped"):
                break

            # Sleep between cycles
            if self._running and (
                max_cycles is None or cycles_run < max_cycles
            ):
                logger.info(
                    "Cycle %d complete. Sleeping %d minutes...",
                    self._cycle, interval_minutes,
                )
                time.sleep(interval_minutes * 60)

    def stop(self) -> None:
        """Write the stop sentinel file to halt the loop."""
        self._running = False
        os.makedirs(os.path.dirname(_SENTINEL_PATH), exist_ok=True)
        Path(_SENTINEL_PATH).touch()
        logger.info("Stop sentinel written to %s", _SENTINEL_PATH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_active_suggestions(self) -> list[dict]:
        """Fetch suggestions that are currently active/applied."""
        rows = self._db.execute(
            "SELECT * FROM suggestions "
            "WHERE status IN ('approved', 'applied', 'experiment', 'pending_approval')",
        ).fetchall()
        return [dict(r) for r in rows]
