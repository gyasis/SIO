"""Single source of truth for SIO's on-disk home and database location.

Every other module that needs ``~/.sio`` or ``~/.sio/sio.db`` MUST resolve
it through :func:`sio_home` / :func:`db_path` instead of re-deriving the
default inline. This is what lets a second, independent SIO installation
(e.g. a per-product or per-office home) be selected purely via env vars —
``$SIO_HOME`` and/or ``$SIO_DB_PATH`` — with no code path silently falling
back to the default ``~/.sio``.

Both functions read the environment at CALL TIME, not at import time, so
tests (and any caller that mutates ``os.environ`` mid-process) see the
override take effect immediately without a reload.
"""

from __future__ import annotations

import os
from pathlib import Path


def sio_home() -> Path:
    """Return the SIO home directory.

    Honors ``$SIO_HOME`` (expanded via ``expanduser``) when set, else
    defaults to ``~/.sio``. Read at call time — never cache the result
    across calls.
    """
    env = os.environ.get("SIO_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sio"


def db_path() -> Path:
    """Return the canonical SIO database path.

    Honors ``$SIO_DB_PATH`` (expanded via ``expanduser``) when set, else
    defaults to ``sio_home() / "sio.db"`` — which itself honors
    ``$SIO_HOME``. Read at call time — never cache the result across
    calls.
    """
    env = os.environ.get("SIO_DB_PATH")
    if env:
        return Path(env).expanduser()
    return sio_home() / "sio.db"


def claude_rule_dirs(claude_home: Path | None = None) -> list[Path]:
    """Return every directory that holds Claude Code rule markdown.

    ``~/.claude/rules/`` is auto-loaded by Claude Code into EVERY session, so
    on-demand (tier 2/3) rules live in ``~/.claude/rulebook/`` instead, where
    the rules-injector hook reads them. A reader that scans only ``rules/``
    sees the always-loaded core and silently misses the whole rulebook — which
    is how violations / budget / dedupe / rule-audit / the suggest consultant
    went blind to most rules after the 2026-10-01 split. Every rule reader MUST
    resolve its directories here. Missing directories are skipped by callers;
    this function only names them.
    """
    base = claude_home if claude_home is not None else Path.home() / ".claude"
    return [base / "rules", base / "rulebook"]


def iter_claude_rule_files(claude_home: Path | None = None) -> list[Path]:
    """Every ``*.md`` rule file across :func:`claude_rule_dirs`, sorted,
    de-duplicated by resolved path (alias symlinks count once)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for d in claude_rule_dirs(claude_home):
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            try:
                real = f.resolve()
            except OSError:
                continue
            if real in seen or not real.is_file():
                continue
            seen.add(real)
            out.append(f)
    return out
