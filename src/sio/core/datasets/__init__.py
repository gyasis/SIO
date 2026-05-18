"""Dataset versioning (proposed Principle XV — reproducibility).

Every training JSONL gets a content-hash + permanent storage location + a
datasets row in sio.db. Solves: "I can't reproduce module #15 from the DB
alone — the trainset is in /tmp/."
"""
from .registry import (
    DATASETS_DIR,
    ensure_schema,
    register_dataset,
    hash_file,
    find_by_hash,
    find_by_path,
    link_optimized_module,
)

__all__ = [
    "DATASETS_DIR",
    "ensure_schema",
    "register_dataset",
    "hash_file",
    "find_by_hash",
    "find_by_path",
    "link_optimized_module",
]
