"""Claude Code installer — sets up SIO for Claude Code platform."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sio.core.db.schema import init_db


def install(
    db_dir: str | None = None,
    claude_dir: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Install SIO for Claude Code.

    Creates DB, registers hooks, saves platform config.

    Args:
        db_dir: Path to SIO data directory. Default: ~/.sio/claude-code
        claude_dir: Path to Claude config directory. Default: ~/.claude
        dry_run: If True, still creates files but marks as dry run.

    Returns:
        Summary dict with installation results.
    """
    if db_dir is None:
        db_dir = os.path.expanduser("~/.sio/claude-code")
    if claude_dir is None:
        claude_dir = os.path.expanduser("~/.claude")

    os.makedirs(db_dir, exist_ok=True)
    os.makedirs(claude_dir, exist_ok=True)

    # Initialize database
    db_path = os.path.join(db_dir, "behavior_invocations.db")
    conn = init_db(db_path)

    # Register hooks in settings.json
    settings_path = os.path.join(claude_dir, "settings.json")
    hooks_registered = _register_hooks(settings_path)

    # Save platform config
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO platform_config "
        "(platform, db_path, hooks_installed, skills_installed, "
        "config_updated, capability_tier, installed_at) "
        "VALUES (?, ?, 1, 1, 1, 1, ?)",
        ("claude-code", db_path, now),
    )
    conn.commit()
    conn.close()

    return {
        "platform": "claude-code",
        "db_created": True,
        "db_path": db_path,
        "hooks_registered": hooks_registered,
        "platform_config_saved": True,
        "dry_run": dry_run,
    }


def _register_hooks(settings_path: str) -> bool:
    """Register SIO hooks in Claude settings.json.

    Merges with existing hooks — never overwrites.
    """
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            settings = json.load(f)
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    sio_hook = {
        "type": "command",
        "command": "python3 -m sio.adapters.claude_code.hooks.post_tool_use",
    }

    post_hooks = hooks.setdefault("PostToolUse", [])

    # Check if SIO hook already registered
    existing_cmds = {
        h.get("command", "") for h in post_hooks if isinstance(h, dict)
    }
    if sio_hook["command"] not in existing_cmds:
        post_hooks.append(sio_hook)

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    return True
