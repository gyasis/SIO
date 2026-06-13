"""Stage-1 error tagging — project-agnostic derivation of structural tags.

Single source of truth for the tags persisted on ``error_records`` and read by the
autopsy/cluster stage. Everything is DERIVED from the session (path + command), never
hardcoded per project, so the same logic serves any repo (cadastre, SIO, hh, …).

Tags:
  project_tag       stable project key from the Claude-projects dir name
  command_category  binary+subcommand from a Bash command (scaffolding skipped); else tool name
  time_bucket       day bucket (YYYY-MM-DD) for cheap temporal grouping

Used by:
  scripts/tag_errors.py   (backfill / post-mine tagging)
  scripts/autopsy.py       (reads the persisted columns; falls back to these fns)
"""
from __future__ import annotations

import json
import os
import re

# leading shell scaffolding to skip when finding the "real" command in a chain —
# cd/source/env-assignment plus diagnostic no-ops (echo/printf/:/true/test) that are
# header noise around the command that actually failed (`echo "=== x ==="; ls …`).
_SCAFFOLD = re.compile(
    r"^(cd\s+\S+|source\s+\S+|\.\s+\S+|set\s+[-+]\w+|export\s+\S+|[A-Z_][A-Z0-9_]*=\S+"
    r"|echo\b.*|printf\b.*|sleep\s+\S+|true|:|test\b.*|\[.*)$"
)

_PROJECT_PREFIX = re.compile(r"^-home-gyasisutton-dev-")


def project_tag(source_file: str | None) -> str:
    """Stable project key from the Claude-projects dir name. Generic, no hardcoding."""
    m = re.search(r"/\.claude/projects/([^/]+)/", source_file or "")
    if not m:
        return "unknown"
    name = m.group(1)
    return _PROJECT_PREFIX.sub("", name) or name


def command_category(tool_name: str | None, tool_input: str | None) -> str:
    """binary+subcommand from a Bash command (scaffolding skipped); non-Bash → tool name."""
    if (tool_name or "") != "Bash" or not tool_input:
        return (tool_name or "unknown").lower()
    try:
        cmd = (json.loads(tool_input).get("command") or "").strip()
    except Exception:
        return "bash-unparsed"
    if not cmd:
        return "bash-empty"
    low = cmd.lower()
    if "docker exec" in low and "psql" in low:
        return "docker-exec-psql"
    for seg in re.split(r"&&|\|\||;|\n", cmd):
        seg = seg.strip()
        if not seg or _SCAFFOLD.match(seg):
            continue
        toks = [t for t in seg.split() if not re.match(r"^[A-Z_][A-Z0-9_]*=", t)]
        if not toks:
            continue
        binary = os.path.basename(toks[0])
        sub = ""
        if len(toks) > 1 and re.match(r"^[a-zA-Z][\w-]*$", toks[1]):
            sub = toks[1]
        return f"{binary}-{sub}" if sub else binary
    return "bash-other"


def time_bucket(timestamp: str | None) -> str:
    """Coarse day bucket for grouping/trend. (Active work-block math lives in Stage 4.)"""
    return (timestamp or "")[:10] or "unknown"


TAG_COLUMNS = ("project_tag", "command_category", "time_bucket")


def derive_all(source_file: str | None, tool_name: str | None,
               tool_input: str | None, timestamp: str | None) -> dict:
    """All Stage-1 tags for one row."""
    return {
        "project_tag": project_tag(source_file),
        "command_category": command_category(tool_name, tool_input),
        "time_bucket": time_bucket(timestamp),
    }
