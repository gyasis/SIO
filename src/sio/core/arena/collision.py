"""Collision detector — finds skill description overlaps."""

from __future__ import annotations

from difflib import SequenceMatcher
from itertools import combinations


def is_collision(
    sim_score: float, threshold: float = 0.85,
) -> bool:
    """Check if similarity score indicates a trigger collision.

    Args:
        sim_score: Similarity score (0 to 1).
        threshold: Collision threshold (default 0.85).

    Returns:
        True if the skills likely collide.
    """
    return sim_score >= threshold


def check_collisions(
    skill_descriptions: dict[str, str],
    embedder=None,
) -> list[dict]:
    """Find colliding skill descriptions.

    V0.1: Uses SequenceMatcher as proxy for embedding similarity.

    Args:
        skill_descriptions: Map of skill_name -> description.
        embedder: Optional embedding backend.

    Returns:
        List of collision warnings with skill_a, skill_b, similarity.
    """
    if len(skill_descriptions) < 2:
        return []

    warnings = []
    for (name_a, desc_a), (name_b, desc_b) in combinations(
        skill_descriptions.items(), 2,
    ):
        similarity = SequenceMatcher(
            None, desc_a.lower(), desc_b.lower(),
        ).ratio()

        if is_collision(similarity):
            warnings.append({
                "skill_a": name_a,
                "skill_b": name_b,
                "similarity": similarity,
            })

    return warnings
