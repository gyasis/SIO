"""JSON renderer for the cohort A/B report (T023).

Pure serialization — the report dict from ``build_report`` is already
JSON-safe (only str / int / float / None / list / dict). Stable key
order so diffs between two report runs are readable.
"""

from __future__ import annotations

import json
from typing import Any


def render_json(report: dict[str, Any], *, indent: int = 2) -> str:
    """Serialize the report dict to a pretty JSON string."""
    return json.dumps(report, indent=indent, sort_keys=True, default=str)
