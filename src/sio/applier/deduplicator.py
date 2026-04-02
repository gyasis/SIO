"""sio.applier.deduplicator -- find and merge semantically duplicate rules.

Public API
----------
    find_duplicates(file_paths, threshold=0.85) -> list[DuplicatePair]
    propose_merge(pair) -> str
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import numpy as np

from sio.clustering.pattern_clusterer import _get_backend


class DuplicatePair(NamedTuple):
    """A pair of semantically duplicate rule blocks."""

    file_a: str
    line_a: int
    text_a: str
    file_b: str
    line_b: int
    text_b: str
    similarity: float


# ---------------------------------------------------------------------------
# Rule block parsing
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^#{1,6}\s+")


def _parse_rule_blocks(text: str) -> list[dict]:
    """Split *text* into rule blocks separated by blank lines or heading markers.

    Returns a list of dicts with keys:
        - ``text``: the block content (stripped)
        - ``start_line``: 1-based line number of the block's first line
    """
    lines = text.splitlines()
    blocks: list[dict] = []
    current_lines: list[str] = []
    start_line: int = 1

    for i, line in enumerate(lines):
        stripped = line.strip()
        line_num = i + 1  # 1-based

        is_blank = stripped == ""
        is_heading = bool(_HEADING_RE.match(stripped))

        if is_blank:
            if current_lines:
                blocks.append({
                    "text": "\n".join(current_lines),
                    "start_line": start_line,
                })
                current_lines = []
            continue

        if is_heading and current_lines:
            blocks.append({
                "text": "\n".join(current_lines),
                "start_line": start_line,
            })
            current_lines = []

        if not current_lines:
            start_line = line_num
        current_lines.append(line)

    if current_lines:
        blocks.append({
            "text": "\n".join(current_lines),
            "start_line": start_line,
        })

    return blocks


# ---------------------------------------------------------------------------
# Cosine similarity
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


def find_duplicates(
    file_paths: list[str | Path],
    threshold: float = 0.85,
) -> list[DuplicatePair]:
    """Find semantically duplicate rule blocks across *file_paths*.

    Parameters
    ----------
    file_paths:
        List of config file paths to scan for duplicate rules.
    threshold:
        Cosine similarity threshold for duplicate detection (default 0.85).

    Returns
    -------
    list[DuplicatePair]
        Pairs of duplicate blocks sorted by similarity descending.
    """
    # Collect all blocks across all files.
    all_blocks: list[dict] = []  # {text, start_line, file_path}

    for fp in file_paths:
        path = Path(fp)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        blocks = _parse_rule_blocks(text)
        for block in blocks:
            all_blocks.append({
                "text": block["text"],
                "start_line": block["start_line"],
                "file_path": str(path),
            })

    if len(all_blocks) < 2:
        return []

    backend = _get_backend()
    texts = [b["text"] for b in all_blocks]
    embeddings = backend.encode(texts)

    # Find all pairs above threshold.
    pairs: list[DuplicatePair] = []
    for i in range(len(all_blocks)):
        for j in range(i + 1, len(all_blocks)):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                pairs.append(DuplicatePair(
                    file_a=all_blocks[i]["file_path"],
                    line_a=all_blocks[i]["start_line"],
                    text_a=all_blocks[i]["text"],
                    file_b=all_blocks[j]["file_path"],
                    line_b=all_blocks[j]["start_line"],
                    text_b=all_blocks[j]["text"],
                    similarity=sim,
                ))

    # Sort by similarity descending.
    pairs.sort(key=lambda p: p.similarity, reverse=True)
    return pairs


def propose_merge(pair: DuplicatePair) -> str:
    """Generate a merged rule text from two duplicate blocks.

    Keeps the more specific (longer) text as the base and incorporates
    unique lines from the shorter text.

    Parameters
    ----------
    pair:
        A DuplicatePair with the two duplicate blocks.

    Returns
    -------
    str
        The proposed merged rule text.
    """
    text_a = pair.text_a
    text_b = pair.text_b

    # Use the longer text as the base.
    if len(text_a) >= len(text_b):
        base, other = text_a, text_b
    else:
        base, other = text_b, text_a

    base_lines = base.splitlines()
    base_lines_set = set(ln.strip() for ln in base_lines)

    # Collect unique non-blank lines from the other block.
    extra_lines: list[str] = []
    for line in other.splitlines():
        stripped = line.strip()
        if stripped and stripped not in base_lines_set:
            extra_lines.append(line)

    if extra_lines:
        return base + "\n" + "\n".join(extra_lines)
    return base
