"""Shared file tail for the JSONL live streams (claude, pi/omp, codex, kimi).

The writer may be mid-line when we poll. Only ``\\n``-terminated lines are
handed out and the offset advances past exactly those, so a half-written line
stays pending and is read whole on the next poll instead of being dropped.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator


def tail_lines(
    path: str, *, poll_interval: float = 1.0, from_start: bool = False
) -> Iterator[str]:
    """Yield each complete, non-blank line appended to ``path``, forever.

    Starts at end of file unless ``from_start``. A file that shrinks
    (truncated / rotated) is re-read from the top. Runs until the caller
    stops iterating.
    """
    offset = 0 if from_start else os.path.getsize(path)
    while True:
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(poll_interval)
            continue
        if size < offset:  # file truncated / rotated
            offset = 0
        if size > offset:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
            end = data.rfind(b"\n") + 1  # 0 when no complete line yet
            offset += end
            for raw in data[:end].splitlines():
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    yield line
        time.sleep(poll_interval)
