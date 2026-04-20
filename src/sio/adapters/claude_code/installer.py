"""Claude Code installer — sets up SIO for Claude Code platform."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from sio.core.constants import DEFAULT_PLATFORM
from sio.core.db.connect import open_db
from sio.core.db.schema import ensure_schema_version, init_db

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

    # Honour SIO_HOME override (used by tests) to determine canonical DB location
    _sio_home = os.environ.get(
        "SIO_HOME", os.path.expanduser("~/.sio")
    )

    os.makedirs(db_dir, exist_ok=True)
    os.makedirs(claude_dir, exist_ok=True)

    # Create ground truth and optimized module directories
    sio_base = _sio_home
    os.makedirs(os.path.join(sio_base, "ground_truth"), exist_ok=True)
    os.makedirs(os.path.join(sio_base, "optimized"), exist_ok=True)

    # Create config.toml template if not present
    config_created = _install_config(sio_base)

    # Initialize per-platform database (FR-007: preserve per-platform DB)
    db_path = os.path.join(db_dir, "behavior_invocations.db")
    conn = init_db(db_path)

    # --- FR-007 + SC-014: Ensure canonical sio.db has schema_version ---
    # The canonical DB lives at ~/.sio/sio.db (or SIO_DB_PATH env override)
    canonical_db_path = os.environ.get(
        "SIO_DB_PATH", os.path.join(_sio_home, "sio.db")
    )
    os.makedirs(os.path.dirname(canonical_db_path), exist_ok=True)
    with open_db(canonical_db_path) as canonical_conn:
        # Ensure all base tables exist in canonical DB
        _ensure_canonical_schema(canonical_conn)
        # Ensure schema_version table + baseline row exist (idempotent)
        ensure_schema_version(canonical_conn)
        # Apply 004 migration if not yet applied (idempotent)
        _apply_004_migration_if_needed(canonical_db_path, canonical_conn)
    # One-time backfill: mirror per-platform rows into canonical DB
    _run_split_brain_backfill()

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


def _ensure_canonical_schema(conn) -> None:
    """Create all base SIO tables in the canonical sio.db if not present.

    Delegates to init_db logic but via an already-open connection.
    Uses CREATE TABLE IF NOT EXISTS so safe to call repeatedly.
    """
    from sio.core.db.schema import (  # noqa: PLC0415
        _APPLIED_CHANGES_DDL,
        _AUTORESEARCH_TXLOG_DDL,
        _BEHAVIOR_INVOCATIONS_DDL,
        _DATASETS_DDL,
        _ERROR_RECORDS_DDL,
        _FLOW_EVENTS_DDL,
        _GOLD_STANDARDS_DDL,
        _GROUND_TRUTH_DDL,
        _INDEXES,
        _OPTIMIZED_MODULES_DDL,
        _OPTIMIZATION_RUNS_DDL,
        _PATTERN_ERRORS_DDL,
        _PATTERNS_DDL,
        _PLATFORM_CONFIG_DDL,
        _POSITIVE_RECORDS_DDL,
        _PROCESSED_SESSIONS_DDL,
        _RECALL_EXAMPLES_DDL,
        _SESSION_METRICS_DDL,
        _SUGGESTIONS_DDL,
        _VELOCITY_SNAPSHOTS_DDL,
    )

    for ddl in [
        _BEHAVIOR_INVOCATIONS_DDL,
        _OPTIMIZATION_RUNS_DDL,
        _GOLD_STANDARDS_DDL,
        _PLATFORM_CONFIG_DDL,
        _ERROR_RECORDS_DDL,
        _PATTERNS_DDL,
        _PATTERN_ERRORS_DDL,
        _DATASETS_DDL,
        _SUGGESTIONS_DDL,
        _APPLIED_CHANGES_DDL,
        _FLOW_EVENTS_DDL,
        _RECALL_EXAMPLES_DDL,
        _GROUND_TRUTH_DDL,
        _OPTIMIZED_MODULES_DDL,
        _PROCESSED_SESSIONS_DDL,
        _SESSION_METRICS_DDL,
        _POSITIVE_RECORDS_DDL,
        _VELOCITY_SNAPSHOTS_DDL,
        _AUTORESEARCH_TXLOG_DDL,
    ]:
        conn.execute(ddl)

    for idx_sql in _INDEXES:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass  # Index may already exist

    conn.commit()


def _apply_004_migration_if_needed(
    canonical_db_path: str, conn
) -> None:
    """Apply 004 migration to canonical DB if not already applied. Idempotent."""
    import sqlite3  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    try:
        row = conn.execute(
            "SELECT status FROM schema_version WHERE version=2"
        ).fetchone()
        if row and row["status"] == "applied":
            return  # Already applied
    except Exception:
        pass  # schema_version table doesn't exist yet — migrate_004 will create it

    # Call migrate_004.migrate() which is idempotent
    try:
        from scripts.migrate_004 import migrate  # noqa: PLC0415

        migrate(canonical_db_path)
    except Exception:
        # Migration failure must not break installation
        pass


def _run_split_brain_backfill() -> None:
    """Run the one-time sync backfill. Swallows all errors — never breaks install."""
    try:
        from scripts.migrate_split_brain import main as split_brain_main  # noqa: PLC0415

        split_brain_main()
    except Exception:
        pass
