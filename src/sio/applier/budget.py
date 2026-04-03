"""sio.applier.budget -- instruction budget management for config files.

Public API
----------
    count_meaningful_lines(file_path) -> int
    check_budget(file_path, new_rule_lines, config) -> BudgetResult
    trigger_consolidation(file_path, config) -> bool
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import numpy as np

from sio.clustering.pattern_clusterer import _get_backend
from sio.core.config import SIOConfig


class BudgetResult(NamedTuple):
    """Result of a budget check."""

    status: str  # 'ok' | 'consolidate' | 'blocked'
    current_lines: int
    cap: int
    message: str


# ---------------------------------------------------------------------------
# HTML comment stripping
# ---------------------------------------------------------------------------

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    """Remove all HTML comments (including multi-line) from *text*."""
    return _HTML_COMMENT_RE.sub("", text)


# ---------------------------------------------------------------------------
# Rule block parsing
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^#{1,6}\s+")


def _parse_rule_blocks(text: str) -> list[dict]:
    """Split *text* into rule blocks separated by blank lines or heading markers.

    Returns a list of dicts with keys:
        - ``text``: the block content (stripped)
        - ``start_line``: 0-based line index of the block's first line
        - ``end_line``: 0-based line index of the block's last line (inclusive)
    """
    lines = text.splitlines()
    blocks: list[dict] = []
    current_lines: list[str] = []
    start_line: int = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # A blank line or a heading line starts a new block boundary.
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
            # Flush the previous block before starting a heading block.
            blocks.append({
                "text": "\n".join(current_lines),
                "start_line": start_line,
                "end_line": i - 1,
            })
            current_lines = []

        if not current_lines:
            start_line = i
        current_lines.append(line)

    # Flush remaining.
    if current_lines:
        blocks.append({
            "text": "\n".join(current_lines),
            "start_line": start_line,
            "end_line": len(lines) - 1,
        })

    return blocks


# ---------------------------------------------------------------------------
# Cosine similarity (reused from pattern_clusterer pattern)
# ---------------------------------------------------------------------------

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return cosine similarity between two 1-D numpy vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def count_meaningful_lines(file_path: str | Path) -> int:
    """Count non-blank lines that are not inside HTML comments.

    Parameters
    ----------
    file_path:
        Path to a markdown (or any text) file.

    Returns
    -------
    int
        Number of meaningful lines (non-blank after HTML comment removal).
    """
    path = Path(file_path)
    if not path.exists():
        return 0

    text = path.read_text(encoding="utf-8")
    cleaned = _strip_html_comments(text)
    return sum(1 for line in cleaned.splitlines() if line.strip())


def check_budget(
    file_path: str | Path,
    new_rule_lines: int,
    config: SIOConfig,
) -> BudgetResult:
    """Check whether the file has room for *new_rule_lines* more lines.

    Parameters
    ----------
    file_path:
        Target config file.
    new_rule_lines:
        Number of meaningful lines the new rule would add.
    config:
        SIO configuration (provides ``budget_cap_primary`` and
        ``budget_cap_supplementary``).

    Returns
    -------
    BudgetResult
        A named tuple with ``status``, ``current_lines``, ``cap``, ``message``.
    """
    path = Path(file_path)
    current = count_meaningful_lines(path)

    # Determine cap: primary (CLAUDE.md) vs supplementary (everything else).
    name = path.name.upper()
    if name == "CLAUDE.MD":
        cap = config.budget_cap_primary
    else:
        cap = config.budget_cap_supplementary

    projected = current + new_rule_lines
    utilization = current / cap if cap > 0 else 1.0

    if projected <= cap and utilization < 0.90:
        return BudgetResult(
            status="ok",
            current_lines=current,
            cap=cap,
            message=f"Budget: {current}/{cap} lines ({utilization:.0%})",
        )

    if projected <= cap:
        # Near cap (>=90% utilization) but still fits.
        return BudgetResult(
            status="ok",
            current_lines=current,
            cap=cap,
            message=(
                f"Budget: {current}/{cap} lines ({utilization:.0%}) "
                f"-- near capacity"
            ),
        )

    # Already at or over cap — no room even after consolidation.
    if current >= cap:
        return BudgetResult(
            status="blocked",
            current_lines=current,
            cap=cap,
            message=(
                f"Budget: {current}/{cap} lines ({utilization:.0%}) "
                f"-- at capacity, cannot add {new_rule_lines} lines"
            ),
        )

    # Over budget with new lines but still below cap -- need consolidation.
    return BudgetResult(
        status="consolidate",
        current_lines=current,
        cap=cap,
        message=(
            f"Budget: {current}/{cap} lines ({utilization:.0%}) "
            f"-- adding {new_rule_lines} lines exceeds cap"
        ),
    )


def trigger_consolidation(
    file_path: str | Path,
    config: SIOConfig,
) -> bool:
    """Attempt to consolidate semantically similar rule blocks in *file_path*.

    Uses FastEmbed (via the module-level singleton from ``pattern_clusterer``)
    to embed all rule blocks, finds pairs above ``config.dedup_threshold``,
    and merges them (keeping the longer/more-specific text).

    Parameters
    ----------
    file_path:
        Path to the config file to consolidate.
    config:
        SIO configuration (provides ``dedup_threshold``).

    Returns
    -------
    bool
        ``True`` if any blocks were merged and the file was rewritten,
        ``False`` if no consolidation candidates were found.
    """
    path = Path(file_path)
    if not path.exists():
        return False

    text = path.read_text(encoding="utf-8")
    blocks = _parse_rule_blocks(text)

    if len(blocks) < 2:
        return False

    backend = _get_backend()
    block_texts = [b["text"] for b in blocks]
    embeddings = backend.encode(block_texts)

    # Find pairs above threshold (greedy: merge first pair found, then repeat).
    threshold = config.dedup_threshold
    merged_any = False

    while True:
        best_sim = -1.0
        best_pair: tuple[int, int] | None = None

        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                sim = _cosine_similarity(embeddings[i], embeddings[j])
                if sim >= threshold and sim > best_sim:
                    best_sim = sim
                    best_pair = (i, j)

        if best_pair is None:
            break

        # Merge: keep the longer block, drop the shorter one.
        i, j = best_pair
        if len(block_texts[i]) >= len(block_texts[j]):
            keep, drop = i, j
        else:
            keep, drop = j, i

        # Combine: use the longer as base, append unique lines from shorter.
        keep_lines = set(block_texts[keep].splitlines())
        extra_lines = [
            ln for ln in block_texts[drop].splitlines()
            if ln.strip() and ln not in keep_lines
        ]
        if extra_lines:
            block_texts[keep] = block_texts[keep] + "\n" + "\n".join(extra_lines)

        # Remove the dropped block.
        block_texts.pop(drop)
        embeddings = np.delete(embeddings, drop, axis=0)
        # Re-encode the merged block.
        embeddings[keep] = backend.encode([block_texts[keep]])[0]

        merged_any = True

    if not merged_any:
        return False

    # Rewrite the file from merged blocks.
    new_content = "\n\n".join(block_texts)
    if not new_content.endswith("\n"):
        new_content += "\n"
    path.write_text(new_content, encoding="utf-8")
    return True
