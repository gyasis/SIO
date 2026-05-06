"""Tooling for promoting a violated CLAUDE.md rule into a runtime hook.

See ``prds/prd-violated-rule-to-pretooluse-hook.md`` for the full design.
The package is organised as one module per pipeline stage so each
phase of the PRD lands as an isolated, reviewable diff:

  extractor.py  — Phase 3: DSPy module that reads (rule_text,
                  violating examples) and emits a structured
                  detection pattern (matcher tools + Python
                  expression + rationale)
  generator.py  — Phase 4 (TBD): turns the structured pattern into
                  an executable PreToolUse hook script
  verifier.py   — Phase 5 (TBD): replays the generated detection
                  against historical violations to report coverage
                  before the hook gets registered
"""

from sio.promote_rule.extractor import (
    DetectionPattern,
    ExtractDetectionPattern,
    extract_detection,
)
from sio.promote_rule.generator import (
    HookGenerationResult,
    generate_and_register,
)

__all__ = [
    "DetectionPattern",
    "ExtractDetectionPattern",
    "extract_detection",
    "HookGenerationResult",
    "generate_and_register",
]
