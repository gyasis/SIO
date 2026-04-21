"""sio.applier.changelog — append entries to ~/.sio/changelog.md.

Public API
----------
    log_change(change) -> None
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_CHANGELOG = os.path.expanduser("~/.sio/changelog.md")


def log_change(change: dict, path: str | None = None) -> None:
    """Append a change record to the changelog file.

    Parameters
    ----------
    change : dict
        Must contain: target_file, change_id, suggestion_id.
        Optional: commit_sha, description.
    path : str | None
        Override the changelog path (default ~/.sio/changelog.md).
    """
    changelog_path = Path(path or _DEFAULT_CHANGELOG)
    changelog_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    target = change.get("target_file", "unknown")
    cid = change.get("change_id", "?")
    sid = change.get("suggestion_id", "?")
    sha = change.get("commit_sha", "n/a")
    desc = change.get("description", "")

    entry = f"- **{now}** | Change #{cid} (Suggestion #{sid}) | `{target}` | SHA: {sha}"
    if desc:
        entry += f"\n  {desc}"
    entry += "\n"

    # Create header if file doesn't exist
    if not changelog_path.exists():
        header = "# SIO Changelog\n\nApplied changes log.\n\n"
        changelog_path.write_text(header + entry)
    else:
        with open(changelog_path, "a") as f:
            f.write(entry)
