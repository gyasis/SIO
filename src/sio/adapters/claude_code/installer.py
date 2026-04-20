"""Claude Code installer — sets up SIO for Claude Code platform."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from sio.core.constants import DEFAULT_PLATFORM
from sio.core.db.schema import init_db

_CONFIG_TEMPLATE = """\
# SIO configuration — ~/.sio/config.toml
# Uncomment and edit the provider you want to use.

[llm]
# model = "azure/DeepSeek-R1-0528"
# api_key_env = "AZURE_OPENAI_API_KEY"
# api_base_env = "AZURE_OPENAI_ENDPOINT"

# model = "anthropic/claude-sonnet-4-20250514"
# api_key_env = "ANTHROPIC_API_KEY"

# model = "openai/gpt-4o"
# api_key_env = "OPENAI_API_KEY"

# model = "ollama/llama3"
# api_base_env = "OLLAMA_HOST"

temperature = 0.7
max_tokens = 2000

[llm.sub]
# model = "openai/gpt-4o-mini"
"""

# Skills bundled with SIO
_SKILLS_DIR = Path(__file__).parent / "skills"
_SKILL_NAMES = [
    "sio-scan",
    "sio-suggest",
    "sio-apply",
    "sio-review",
    "sio-status",
    "sio-optimize",
    "sio-health",
    "sio-feedback",
    "sio-briefing",
    "sio-velocity",
    "sio-violations",
    "sio-discover",
    "sio-report",
    "sio-promote-flow",
    "sio-budget",
    "sio-consultant",
]


def install(
    db_dir: str | None = None,
    claude_dir: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Install SIO for Claude Code.

    Creates DB, registers hooks, installs skills, saves platform config.

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

    # Create ground truth and optimized module directories
    sio_base = os.path.expanduser("~/.sio")
    os.makedirs(os.path.join(sio_base, "ground_truth"), exist_ok=True)
    os.makedirs(os.path.join(sio_base, "optimized"), exist_ok=True)

    # Create config.toml template if not present
    config_created = _install_config(sio_base)

    # Initialize database
    db_path = os.path.join(db_dir, "behavior_invocations.db")
    conn = init_db(db_path)

    # Register hooks in settings.json
    settings_path = os.path.join(claude_dir, "settings.json")
    hooks_registered = _register_hooks(settings_path)

    # Install skills to ~/.claude/skills/
    skills_installed = _install_skills(claude_dir)

    # Save platform config
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO platform_config "
        "(platform, db_path, hooks_installed, skills_installed, "
        "config_updated, capability_tier, installed_at) "
        "VALUES (?, ?, 1, 1, 1, 1, ?)",
        (DEFAULT_PLATFORM, db_path, now),
    )
    conn.commit()
    conn.close()

    return {
        "platform": DEFAULT_PLATFORM,
        "db_created": True,
        "db_path": db_path,
        "hooks_registered": hooks_registered,
        "skills_installed": skills_installed,
        "config_created": config_created,
        "platform_config_saved": True,
        "dry_run": dry_run,
    }


def _install_config(sio_base: str) -> bool:
    """Create ~/.sio/config.toml with a template [llm] section if not present.

    Never overwrites an existing config file.

    Args:
        sio_base: Path to SIO base directory (e.g. ~/.sio).

    Returns:
        True if the file was created, False if it already existed.
    """
    config_path = os.path.join(sio_base, "config.toml")
    if os.path.exists(config_path):
        return False
    os.makedirs(sio_base, exist_ok=True)
    with open(config_path, "w") as f:
        f.write(_CONFIG_TEMPLATE)
    return True


def _register_hooks(settings_path: str) -> bool:
    """Register SIO hooks in Claude settings.json.

    Merges with existing hooks — never overwrites.
    """
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, ValueError):
            # Back up corrupt file and start fresh
            backup = settings_path + ".bak"
            shutil.copy2(settings_path, backup)
            settings = {}
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    # Migrate legacy bare hooks to wrapped format.
    # Bare: {"type": "command", "command": "..."}
    # Wrapped: {"matcher": "", "hooks": [{"type": "command", "command": "..."}]}
    for event_name, event_hooks in hooks.items():
        if not isinstance(event_hooks, list):
            continue
        for i, entry in enumerate(event_hooks):
            if (
                isinstance(entry, dict)
                and "type" in entry
                and "command" in entry
                and "hooks" not in entry
            ):
                event_hooks[i] = {
                    "matcher": "",
                    "hooks": [entry],
                }

    # All SIO hook registrations: (event_name, module_path)
    _HOOK_DEFS = [
        ("PostToolUse", "sio.adapters.claude_code.hooks.post_tool_use"),
        ("PreCompact", "sio.adapters.claude_code.hooks.pre_compact"),
        ("Stop", "sio.adapters.claude_code.hooks.stop"),
        ("UserPromptSubmit", "sio.adapters.claude_code.hooks.user_prompt_submit"),
        ("SessionStart", "sio.adapters.claude_code.hooks.session_start"),
    ]

    for event_name, module_path in _HOOK_DEFS:
        sio_hook_entry = {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": f"{sys.executable} -m {module_path}",
                }
            ],
        }

        event_hooks = hooks.setdefault(event_name, [])

        # Check if SIO hook already registered (match on module path,
        # not full command, since the python path differs across installs).
        # Must check both bare dicts (legacy) and wrapped {"hooks": [...]}
        # entries since older installs may have the bare format.
        already_registered = False
        for h in event_hooks:
            if not isinstance(h, dict):
                continue
            # Check bare format (legacy): {"type": "command", "command": "..."}
            if module_path in h.get("command", ""):
                already_registered = True
                break
            # Check wrapped format: {"matcher": ..., "hooks": [...]}
            for inner in h.get("hooks", []):
                if isinstance(inner, dict) and module_path in inner.get("command", ""):
                    already_registered = True
                    break
            if already_registered:
                break

        if not already_registered:
            event_hooks.append(sio_hook_entry)

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    return True


def _install_skills(claude_dir: str) -> list[str]:
    """Copy SIO skill SKILL.md files to ~/.claude/skills/.

    Each skill gets its own directory under ~/.claude/skills/sio-<name>/.
    Existing skills are overwritten (updated to latest version).

    Returns list of skill names installed.
    """
    target_base = Path(claude_dir) / "skills"
    installed: list[str] = []

    for skill_name in _SKILL_NAMES:
        src = _SKILLS_DIR / skill_name / "SKILL.md"
        if not src.exists():
            continue

        dest_dir = target_base / skill_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest_dir / "SKILL.md"))
        installed.append(skill_name)

    return installed
