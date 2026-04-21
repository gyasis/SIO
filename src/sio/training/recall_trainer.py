"""DSPy training pipeline for SIO recall.

The pipeline:
1. Load exported datasets (routing, recovery, flow) + recall_examples
2. Define DSPy signatures for each task
3. Train using BootstrapFewShot (<50 examples) or GEPA (50+)
4. Save optimized module to disk + register in optimized_modules table
5. `sio recall` loads the trained module for inference

Public API
----------
    load_training_data(db_conn, dataset_dir) -> dict[str, list]
    train_recall_module(examples, optimizer="bootstrap", model="gpt-4o-mini") -> module
    save_trained_module(module, db_conn, metrics) -> str
    infer_recall(query, session_steps, module) -> str
    RecallEvaluator: dspy.Module subclass using RuleRecallScore signature
    evaluate_recall(gold, candidate) -> dspy.Prediction (back-compat wrapper)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import dspy

from sio.core.dspy.signatures import RuleRecallScore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# T067: RecallEvaluator — idiomatic dspy.Module for rule recall scoring
# ---------------------------------------------------------------------------


class RecallEvaluator(dspy.Module):
    """Evaluate how well a candidate rule captures the preventive intent of a gold rule.

    Uses the RuleRecallScore signature with ChainOfThought reasoning.

    Class attributes:
        DEFAULT_METRIC: The METRIC_REGISTRY key used by optimizer module-selection.
    """

    DEFAULT_METRIC = "embedding_similarity"

    def __init__(self) -> None:
        super().__init__()
        self.evaluate = dspy.ChainOfThought(RuleRecallScore)

    def forward(self, gold_rule: str, candidate_rule: str) -> dspy.Prediction:
        """Score how well candidate_rule captures the intent of gold_rule.

        Args:
            gold_rule: The reference (gold standard) rule text.
            candidate_rule: The candidate rule to evaluate.

        Returns:
            dspy.Prediction with:
                score (float): Recall score in [0, 1].
                reasoning (str): Brief justification for the score.
        """
        result = self.evaluate(gold_rule=gold_rule, candidate_rule=candidate_rule)
        # Clamp score to [0, 1] defensively
        try:
            clamped = max(0.0, min(1.0, float(result.score)))
            return dspy.Prediction(
                score=clamped,
                reasoning=result.reasoning,
            )
        except (TypeError, ValueError):
            return result


def evaluate_recall(gold: str, candidate: str) -> dspy.Prediction:
    """Back-compat wrapper: instantiate RecallEvaluator and call forward().

    Args:
        gold: Gold-standard rule text.
        candidate: Candidate rule text to evaluate.

    Returns:
        dspy.Prediction with score (float) and reasoning (str).
    """
    evaluator = RecallEvaluator()
    return evaluator.forward(gold_rule=gold, candidate_rule=candidate)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def load_training_data(
    db_conn: sqlite3.Connection,
    dataset_dir: str | Path | None = None,
) -> dict[str, list]:
    """Load training data from exported JSONL files and recall_examples table.

    Returns dict with keys: routing, recovery, flow, recall
    """
    if dataset_dir is None:
        dataset_dir = Path(os.path.expanduser("~/.sio/datasets"))
    else:
        dataset_dir = Path(dataset_dir)

    data = {"routing": [], "recovery": [], "flow": [], "recall": []}

    # Load exported JSONL files
    for task in ("routing", "recovery", "flow"):
        # Find most recent file for this task
        files = sorted(dataset_dir.glob(f"{task}_*.jsonl"), reverse=True)
        if files:
            with open(files[0]) as f:
                for line in f:
                    try:
                        data[task].append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    # Load recall examples from DB (polished + labeled positive)
    rows = db_conn.execute(
        """SELECT query, raw_steps, polished_runbook
           FROM recall_examples
           WHERE label = 'positive' AND polished_runbook IS NOT NULL"""
    ).fetchall()

    for row in rows:
        data["recall"].append(
            {
                "inputs": {"query": row["query"], "raw_steps": row["raw_steps"][:2000]},
                "outputs": {"runbook": row["polished_runbook"]},
            }
        )

    logger.info(
        "Loaded training data: routing=%d, recovery=%d, flow=%d, recall=%d",
        len(data["routing"]),
        len(data["recovery"]),
        len(data["flow"]),
        len(data["recall"]),
    )
    return data


# ---------------------------------------------------------------------------
# DSPy Signatures & Modules
# ---------------------------------------------------------------------------


def _get_dspy():
    """Lazy import DSPy. Returns the module or None if not installed."""
    try:
        import dspy

        return dspy
    except ImportError:
        logger.warning("DSPy not installed. Run: pip install dspy-ai")
        return None


def build_signatures():
    """Build DSPy signature classes for each task.

    Returns dict of signature classes, or None if DSPy unavailable.
    """
    dspy = _get_dspy()
    if not dspy:
        return None

    class RecallRouter(dspy.Signature):
        """Given a user query about a past workflow, predict which tools were used."""

        user_query = dspy.InputField(desc="User question about a past workflow")
        context = dspy.InputField(desc="Available session metadata")
        tools_used = dspy.OutputField(desc="Comma-separated list of tools that were likely used")
        session_type = dspy.OutputField(desc="Type: setup, debug, deploy, investigate, refactor")

    class RecallDistiller(dspy.Signature):
        """Given raw session steps and a query, produce a clean runbook."""

        query = dspy.InputField(desc="What the user wants to recall")
        raw_steps = dspy.InputField(desc="Raw distilled steps from the session")
        runbook = dspy.OutputField(desc="Clean 10-15 step markdown runbook")

    class ErrorRecovery(dspy.Signature):
        """Given an error, predict the fix."""

        error_message = dspy.InputField(desc="The error message or traceback")
        failed_tool = dspy.InputField(desc="Tool that failed")
        tool_input = dspy.InputField(desc="The input that caused the failure")
        user_context = dspy.InputField(desc="What the user was trying to do")
        recovery_tool = dspy.OutputField(desc="Tool to use for the fix")
        recovery_input = dspy.OutputField(desc="Specific input/command to fix the error")

    class FlowPredictor(dspy.Signature):
        """Given current tool sequence, predict the next tool."""

        current_tools = dspy.InputField(desc="Tools used so far in sequence")
        current_tool_count = dspy.InputField(desc="Number of tools in current sequence")
        next_tool = dspy.OutputField(desc="Most likely next tool to use")
        confidence = dspy.OutputField(desc="Confidence score 0-100")

    return {
        "router": RecallRouter,
        "distiller": RecallDistiller,
        "recovery": ErrorRecovery,
        "flow": FlowPredictor,
    }


def _examples_from_data(data: list[dict], task: str):
    """Convert exported JSONL records to DSPy Example objects."""
    dspy = _get_dspy()
    if not dspy:
        return []

    examples = []
    for record in data:
        inputs = record.get("inputs", {})
        outputs = record.get("outputs", {})
        try:
            ex = dspy.Example(**inputs, **outputs).with_inputs(*inputs.keys())
            examples.append(ex)
        except Exception:
            continue

    return examples


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_recall_module(
    data: dict[str, list],
    task: str = "distiller",
    optimizer: str = "bootstrap",
    model: str | None = None,
    max_examples: int = 200,
) -> dict:
    """Train a DSPy module on the specified task's data.

    Args:
        data: Output of load_training_data()
        task: Which module to train (router, distiller, recovery, flow)
        optimizer: "bootstrap" (BootstrapFewShot) or "gepa" (GEPA)
        model: LLM model to use (default: from env DSPY_MODEL or gpt-4o-mini)
        max_examples: Cap training examples

    Returns:
        {
            "module": trained DSPy module (or None),
            "metrics": {"before": float, "after": float, "examples": int},
            "output_path": str (path to saved module),
            "error": str | None,
        }
    """
    dspy = _get_dspy()
    if not dspy:
        return {
            "module": None,
            "metrics": {},
            "output_path": "",
            "error": "DSPy not installed. Run: pip install dspy-ai",
        }

    # Configure LLM via the factory (SC-022: factory is the single construction point)
    try:
        from sio.core.dspy.lm_factory import get_task_lm  # noqa: PLC0415

        # If a specific model override was requested, honour it via env var
        # rather than constructing dspy.LM directly.
        if model is not None:
            import os as _os  # noqa: PLC0415

            _os.environ.setdefault("SIO_TASK_LM", model)
        lm = get_task_lm()
        dspy.configure(lm=lm)
    except Exception as e:
        return {
            "module": None,
            "metrics": {},
            "output_path": "",
            "error": f"Failed to configure DSPy LM: {e}",
        }

    # Build signatures
    sigs = build_signatures()
    if not sigs or task not in sigs:
        return {
            "module": None,
            "metrics": {},
            "output_path": "",
            "error": f"Unknown task: {task}. Available: {list(sigs.keys()) if sigs else 'none'}",
        }

    # Get training data for this task
    task_data_map = {
        "router": "routing",
        "distiller": "recall",
        "recovery": "recovery",
        "flow": "flow",
    }
    dataset_key = task_data_map.get(task, task)
    raw_examples = data.get(dataset_key, [])

    if not raw_examples:
        return {
            "module": None,
            "metrics": {},
            "output_path": "",
            "error": f"No training data for '{task}'. Run 'sio export-dataset' first.",
        }

    # Convert to DSPy examples
    examples = _examples_from_data(raw_examples[:max_examples], task)
    if len(examples) < 3:
        return {
            "module": None,
            "metrics": {},
            "output_path": "",
            "error": f"Need at least 3 examples, got {len(examples)}.",
        }

    # Split train/val
    split = max(1, int(len(examples) * 0.8))
    trainset = examples[:split]
    valset = examples[split:] if split < len(examples) else examples[-3:]

    # Create predictor
    predictor = dspy.Predict(sigs[task])

    # Define metric
    def recall_metric(example, prediction, trace=None):
        """Simple metric: does the prediction have non-empty outputs?"""
        outputs = sigs[task].output_fields
        score = 0
        for field_name in outputs:
            val = getattr(prediction, field_name, "")
            if val and str(val).strip():
                score += 1
        return score / max(len(outputs), 1)

    # Select optimizer
    logger.info("Training %s with %s on %d examples...", task, optimizer, len(trainset))

    try:
        if optimizer == "gepa" and len(trainset) >= 20:
            # GEPA for larger datasets
            try:
                tp = dspy.GEPA(
                    metric=recall_metric,
                    max_bootstrapped_demos=4,
                    max_labeled_demos=8,
                )
                optimized = tp.compile(predictor, trainset=trainset)
            except (AttributeError, TypeError):
                # GEPA might not be available in all DSPy versions
                logger.warning("GEPA not available, falling back to BootstrapFewShot")
                tp = dspy.BootstrapFewShot(
                    metric=recall_metric,
                    max_bootstrapped_demos=4,
                    max_labeled_demos=8,
                )
                optimized = tp.compile(predictor, trainset=trainset)
        else:
            # BootstrapFewShot for smaller datasets
            tp = dspy.BootstrapFewShot(
                metric=recall_metric,
                max_bootstrapped_demos=min(4, len(trainset)),
                max_labeled_demos=min(8, len(trainset)),
            )
            optimized = tp.compile(predictor, trainset=trainset)

        # Evaluate before/after
        before_score = sum(recall_metric(ex, predictor(**ex.inputs())) for ex in valset) / len(
            valset
        )
        after_score = sum(recall_metric(ex, optimized(**ex.inputs())) for ex in valset) / len(
            valset
        )

    except Exception as e:
        return {
            "module": None,
            "metrics": {},
            "output_path": "",
            "error": f"Training failed: {e}",
        }

    # Save module
    output_dir = Path(os.path.expanduser("~/.sio/models"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / f"{task}_optimized.json")

    try:
        optimized.save(output_path)
    except Exception as e:
        logger.warning("Failed to save module: %s", e)
        output_path = ""

    return {
        "module": optimized,
        "metrics": {
            "before": round(before_score, 3),
            "after": round(after_score, 3),
            "examples": len(trainset),
            "val_size": len(valset),
        },
        "output_path": output_path,
        "error": None,
    }


def save_trained_module(
    db_conn: sqlite3.Connection,
    task: str,
    optimizer: str,
    output_path: str,
    metrics: dict,
) -> int:
    """Register a trained module in the optimized_modules table.

    Returns the row ID.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Deactivate previous active module for this task
    db_conn.execute(
        "UPDATE optimized_modules SET is_active = 0 WHERE module_type = ? AND is_active = 1",
        (f"recall_{task}",),
    )

    cursor = db_conn.execute(
        """INSERT INTO optimized_modules
           (module_type, optimizer_used, file_path, training_count,
            metric_before, metric_after, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
        (
            f"recall_{task}",
            optimizer,
            output_path,
            metrics.get("examples", 0),
            metrics.get("before", 0),
            metrics.get("after", 0),
            now,
        ),
    )
    db_conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Inference (uses trained module if available, falls back to raw)
# ---------------------------------------------------------------------------


def infer_recall(
    query: str,
    raw_steps: str,
    db_conn: sqlite3.Connection | None = None,
) -> dict:
    """Run recall inference using trained DSPy module if available.

    Returns:
        {"runbook": str, "source": "dspy" | "raw", "model_path": str | None}
    """
    dspy = _get_dspy()
    if not dspy or not db_conn:
        return {"runbook": raw_steps, "source": "raw", "model_path": None}

    # Check for active trained module
    row = db_conn.execute(
        """SELECT file_path FROM optimized_modules
           WHERE module_type = 'recall_distiller' AND is_active = 1
           ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()

    if not row or not os.path.exists(row["file_path"]):
        return {"runbook": raw_steps, "source": "raw", "model_path": None}

    # Load and run trained module
    try:
        sigs = build_signatures()
        predictor = dspy.Predict(sigs["distiller"])
        predictor.load(row["file_path"])

        result = predictor(query=query, raw_steps=raw_steps[:3000])
        runbook = getattr(result, "runbook", raw_steps)

        return {
            "runbook": runbook,
            "source": "dspy",
            "model_path": row["file_path"],
        }
    except Exception as e:
        logger.warning("DSPy inference failed: %s. Falling back to raw.", e)
        return {"runbook": raw_steps, "source": "raw", "model_path": None}
