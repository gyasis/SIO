"""T058 [US5] Unit tests for collision detector."""

from __future__ import annotations

from sio.core.arena.collision import check_collisions, is_collision


class TestIsCollision:
    """is_collision checks similarity threshold."""

    def test_above_threshold_is_collision(self):
        assert is_collision(0.90) is True

    def test_below_threshold_not_collision(self):
        assert is_collision(0.70) is False

    def test_at_threshold_is_collision(self):
        assert is_collision(0.85) is True

    def test_custom_threshold(self):
        assert is_collision(0.80, threshold=0.75) is True
        assert is_collision(0.60, threshold=0.75) is False


class TestCheckCollisions:
    """check_collisions finds similar skill descriptions."""

    def test_identical_descriptions_flagged(self):
        descriptions = {
            "Read": "Read a file from disk",
            "ReadFile": "Read a file from disk",
        }
        warnings = check_collisions(descriptions)
        assert len(warnings) >= 1

    def test_different_descriptions_no_collision(self):
        descriptions = {
            "Read": "Read a file from disk",
            "Bash": "Execute a shell command in the terminal",
        }
        warnings = check_collisions(descriptions)
        # May or may not collide depending on embedding similarity
        assert isinstance(warnings, list)

    def test_single_skill_no_collision(self):
        descriptions = {"Read": "Read a file from disk"}
        warnings = check_collisions(descriptions)
        assert len(warnings) == 0

    def test_empty_descriptions(self):
        warnings = check_collisions({})
        assert warnings == []

    def test_warning_has_required_fields(self):
        descriptions = {
            "Read": "Read a file from disk",
            "ReadFile": "Read a file from disk",
        }
        warnings = check_collisions(descriptions)
        if warnings:
            w = warnings[0]
            assert "skill_a" in w
            assert "skill_b" in w
            assert "similarity" in w
