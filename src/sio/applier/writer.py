"""sio.applier.writer — apply approved changes to config files.

Public API
----------
    apply_change(db, suggestion_id, config=None) -> dict
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from sio.clustering.pattern_clusterer import _get_backend
from sio.core.config import SIOConfig

_ALLOWED_ROOTS: list[Path] = [
    Path.home() / ".sio",
    Path.home() / ".claude",
]


def _validate_target_path(
    path: Path, *, extra_roots: tuple[Path, ...] = (),
) -> str | None:
    """Return an error message if path is outside allowed roots or cwd."""
    resolved = path.resolve()
    allowed = (*_ALLOWED_ROOTS, Path.cwd(), *extra_roots)
    for root in allowed:
        try:
            resolved.relative_to(root.resolve())
            return None
        except ValueError:
            continue
    return (
        f"Target path {resolved} is outside allowed directories: "
        f"{', '.join(str(r) for r in allowed)}"
    )


# ---------------------------------------------------------------------------
# Delta-based writing helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^#{1,6}\s+")


def _parse_rule_blocks(text: str) -> list[dict]:
    """Split *text* into rule blocks separated by blank lines or headings.

    Returns a list of dicts: {text, start_line, end_line} (0-based).
    """
    lines = text.splitlines()
    blocks: list[dict] = []
    current_lines: list[str] = []
    start_line: int = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        is_blank = stripped == ""
        is_heading = bool(_HEADING_RE.match(stripped))

        if is_blank:
            if current_lines:
                blocks.append({
                    "text": "\n".join(current_lines),
                    "start_line": start_line,
                    "end_line": i - 1,
                })
                current_lines = []
            continue

        if is_heading and current_lines:
            blocks.append({
                "text": "\n".join(current_lines),
                "start_line": start_line,
                "end_line": i - 1,
            })
            current_lines = []

        if not current_lines:
            start_line = i
        current_lines.append(line)

    if current_lines:
        blocks.append({
            "text": "\n".join(current_lines),
            "start_line": start_line,
            "end_line": len(lines) - 1,
        })

    return blocks


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return cosine similarity between two 1-D numpy vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _find_best_merge_target(
    proposed_text: str,
    existing_blocks: list[dict],
    threshold: float,
) -> tuple[int, float] | None:
    """Find the existing block most similar to *proposed_text*.

    Returns (block_index, similarity) if above *threshold*, else ``None``.
    """
    if not existing_blocks:
        return None

    backend = _get_backend()
    all_texts = [proposed_text] + [b["text"] for b in existing_blocks]
    embeddings = backend.encode(all_texts)

    proposed_emb = embeddings[0]
    best_idx: int | None = None
    best_sim: float = -1.0

    for i in range(1, len(embeddings)):
        sim = _cosine_similarity(proposed_emb, embeddings[i])
        if sim >= threshold and sim > best_sim:
            best_sim = sim
            best_idx = i - 1  # index into existing_blocks

    if best_idx is not None:
        return best_idx, best_sim
    return None


def _merge_texts(existing: str, proposed: str) -> str:
    """Merge *proposed* into *existing*, keeping the longer as base.

    Appends unique non-blank lines from the shorter text to the longer one.
    """
    if len(existing) >= len(proposed):
        base, other = existing, proposed
    else:
        base, other = proposed, existing

    base_lines_set = set(ln.strip() for ln in base.splitlines())
    extra = [
        ln for ln in other.splitlines()
        if ln.strip() and ln.strip() not in base_lines_set
    ]
    if extra:
        return base + "\n" + "\n".join(extra)
    return base


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def apply_change(
    db: sqlite3.Connection,
    suggestion_id: int,
    config: SIOConfig | None = None,
) -> dict:
    """Apply an approved suggestion to its target file.

    Uses delta-based writing when a config is provided: if the proposed
    change is >similarity_threshold similar to an existing rule block,
    it merges in place instead of appending.

    Returns a dict with keys: success, change_id, diff_before, diff_after,
    target_file, delta_type, reason (on failure).
    """
    row = db.execute(
        "SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)
    ).fetchone()

    if row is None:
        return {"success": False, "reason": "Suggestion not found"}

    suggestion = dict(row)

    if suggestion["status"] not in ("approved", "auto_approved"):
        return {
            "success": False,
            "reason": f"Suggestion is not approved (status: {suggestion['status']})",
        }

    target_path = Path(suggestion["target_file"])

    path_error = _validate_target_path(target_path)
    if path_error:
        return {"success": False, "reason": path_error}

    proposed_change = suggestion["proposed_change"]

    # Read existing content (or empty if file doesn't exist)
    if target_path.exists():
        diff_before = target_path.read_text()
    else:
        diff_before = ""
        target_path.parent.mkdir(parents=True, exist_ok=True)

    # Delta-based writing: check for similar existing rules.
    delta_type = "append"
    similarity_threshold = 0.80
    if config is not None:
        similarity_threshold = config.similarity_threshold

    if diff_before.strip() and config is not None:
        existing_blocks = _parse_rule_blocks(diff_before)
        merge_result = _find_best_merge_target(
            proposed_change, existing_blocks, similarity_threshold,
        )

        if merge_result is not None:
            block_idx, _sim = merge_result
            target_block = existing_blocks[block_idx]
            merged_text = _merge_texts(target_block["text"], proposed_change)

            # Replace the target block in the original content.
            lines = diff_before.splitlines()
            new_lines = (
                lines[:target_block["start_line"]]
                + merged_text.splitlines()
                + lines[target_block["end_line"] + 1:]
            )
            diff_after = "\n".join(new_lines)
            if not diff_after.endswith("\n"):
                diff_after += "\n"
            delta_type = "merge"
        else:
            # No similar block found -- append as before.
            diff_after = diff_before
            if diff_before and not diff_before.endswith("\n"):
                diff_after += "\n"
            if diff_before:
                diff_after += "\n"
            diff_after += proposed_change
            if not diff_after.endswith("\n"):
                diff_after += "\n"
            delta_type = "append"
    else:
        # No config or empty file -- original append behavior.
        diff_after = diff_before
        if diff_before and not diff_before.endswith("\n"):
            diff_after += "\n"
        if diff_before:
            diff_after += "\n"
        diff_after += proposed_change
        if not diff_after.endswith("\n"):
            diff_after += "\n"

    # Write the file
    target_path.write_text(diff_after)

    # Record in applied_changes table with delta_type
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO applied_changes "
        "(suggestion_id, target_file, diff_before, diff_after, "
        "applied_at, delta_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (suggestion_id, str(target_path), diff_before, diff_after,
         now, delta_type),
    )
    change_id = cur.lastrowid

    # Update suggestion status to 'applied'
    db.execute(
        "UPDATE suggestions SET status = 'applied' WHERE id = ?",
        (suggestion_id,),
    )
    db.commit()

    return {
        "success": True,
        "change_id": change_id,
        "diff_before": diff_before,
        "diff_after": diff_after,
        "target_file": str(target_path),
        "delta_type": delta_type,
    }
