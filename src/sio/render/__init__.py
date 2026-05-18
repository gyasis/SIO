"""sio render — turn optimized DSPy artifacts into deployable skills.

See: ~/dev/prd/scratch/sio_render_artifact_2026-05-16.md
"""
from .reader import load_artifact, load_module_metadata
from .templates import render_claude_md, render_json_prompt, render_skill, render_system_prompt

__all__ = [
    "load_artifact",
    "load_module_metadata",
    "render_skill",
    "render_system_prompt",
    "render_claude_md",
    "render_json_prompt",
]
