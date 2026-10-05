"""SIO's own durable copy of every coding-agent session store.

Harnesses delete their own history (Claude Code: ``cleanupPeriodDays``, default 30),
so SIO keeps the raw files itself. See ``sio.archive.sync``.
"""
