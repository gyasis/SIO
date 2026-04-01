# Quickstart: SIO Competitive Enhancement

**Branch**: `001-competitive-enhancement` | **Date**: 2026-04-01

## Prerequisites

- Python 3.11+
- Existing SIO installation (`pip install -e .` from repo root)
- Git (for experiment branching)
- Claude Code with existing JSONL session files at `~/.claude/projects/`

## Development Setup

```bash
# 1. Switch to feature branch
git checkout 001-competitive-enhancement

# 2. Install in development mode
cd /home/gyasisutton/dev/projects/SIO
pip install -e ".[dev]"

# 3. Run existing tests to verify baseline
pytest tests/ -v

# 4. Verify database
python -c "from sio.core.db.schema import init_db; db = init_db(':memory:'); print('Schema OK')"
```

## Key Files to Understand First

| File | Why | Read first N lines |
|------|-----|-------------------|
| `src/sio/mining/jsonl_parser.py` | Parser being extended — understand current extraction | Full file (~150 lines) |
| `src/sio/mining/error_extractor.py` | Pattern for the new positive_extractor | First 60 lines |
| `src/sio/mining/flow_extractor.py` | Has `_POSITIVE_KEYWORDS` to coordinate with | First 40 lines |
| `src/sio/core/db/schema.py` | All table DDLs — adding 5 new tables here | Full file (~300 lines) |
| `src/sio/suggestions/confidence.py` | Adding temporal decay to this module | Full file (~70 lines) |
| `src/sio/adapters/claude_code/hooks/post_tool_use.py` | Reference pattern for new hooks | Full file |
| `src/sio/clustering/pattern_clusterer.py` | Has `_get_backend()` singleton to reuse | First 80 lines |
| `src/sio/applier/writer.py` | Being modified for delta-based writing | Full file |

## Implementation Order (dependency chain)

```
Wave 1: Data Foundation (no dependencies)
  ├── Enhanced JSONL parser (FR-001)
  ├── New DB tables (schema.py)
  └── Processed sessions tracking (FR-003)

Wave 2: Extractors (depends on Wave 1)
  ├── Positive extractor (FR-007–009)
  ├── Approval detector (FR-010–011)
  ├── Sentiment scorer (FR-012–013)
  └── Pipeline integration (FR-002, 004–006)

Wave 3: Intelligence (depends on Wave 2)
  ├── Temporal decay (FR-017–018)
  ├── Pattern grading (FR-019–020)
  ├── Learning velocity (FR-014–016)
  └── Violation detector (FR-026–027)

Wave 4: Rule Management (depends on Wave 3)
  ├── Budget enforcement (FR-021–025)
  ├── Deduplicator (FR-028)
  └── Delta writer (FR-029–030)

Wave 5: Hooks (independent of Waves 3-4)
  ├── PreCompact hook (FR-031–032)
  ├── Stop hook (FR-033)
  ├── UserPromptSubmit hook (FR-034–035)
  └── Hook installer update (FR-036)

Wave 6: Arena (depends on Waves 3-4)
  ├── Binary assertions (FR-037–038)
  ├── Experiment engine (FR-039–041)
  ├── Anomaly detection (FR-046)
  └── AutoResearch loop (FR-042–045)

Wave 7: Reporting (depends on Waves 2-3)
  ├── HTML report (FR-047–048)
  └── Session facets (FR-049–050)
```

## Testing Strategy

- **Unit tests**: Each new module gets its own test file with fixtures from real JSONL data
- **Integration tests**: Pipeline tests that mine a sample session end-to-end and verify all new tables populated
- **TDD enforcement**: Write test file BEFORE implementation file per Constitution Principle IV
- **Run**: `pytest tests/test_<module>.py -v` after each module
- **Lint**: `ruff check src/sio/ --fix` after each wave
