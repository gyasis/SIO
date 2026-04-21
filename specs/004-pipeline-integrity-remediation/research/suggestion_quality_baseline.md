# Suggestion Quality Baseline (T110)

## Context

The SIO pipeline's SuggestionGenerator converts clustered error patterns into
CLAUDE.md rule candidates. This document captures the baseline approval rate
before the Pipeline Integrity Remediation (004) improvements and defines the
measurement methodology.

## Baseline (Pre-Remediation)

- **Approval rate**: ~8%
  - Source: PRD §2 "Problem Statement" — "8% approval rate (goal: 30%)"
  - Measurement period: sessions mined before 2026-04-01
  - Method: COUNT(approved=1) / COUNT(*) from `suggestions` table

## Measurement Methodology

```sql
-- Batch approval rate (run against ~/.sio/sio.db)
SELECT
    COUNT(*) AS total_suggestions,
    SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END) AS approved_count,
    ROUND(
        100.0 * SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END) / COUNT(*), 2
    ) AS approval_rate_pct
FROM suggestions
WHERE created_at >= '<batch_start_iso>' AND created_at < '<batch_end_iso>';
```

A "batch" is a single `sio suggest` run on a set of patterns. Track results
per batch by adding `batch_id TEXT` to the `suggestions` table (future work).

## Target

- **SC-012**: 30% approval rate on real-world batches post-optimization
- GEPA optimizer with the updated PatternToRule signature (T109) is expected
  to drive approval from 8% toward 30% across 3–5 optimization rounds.

## Current Status

**Baseline documented; tuning iterations pending real-world batch.**

The instrumentation pipeline (T107/T108) now captures per-run counters
(`backtrack_count`, `forward_count`, `rejection_reasons`) to support
finer-grained approval-rate debugging. Once a post-remediation batch of ≥ 50
suggestions has been reviewed, re-run the SQL above and update this doc.

## Notes

- Approval is currently binary (0/1) in the `suggestions` table.
- Future work: continuous scoring via `llm_judge_recall` metric for each
  suggestion before human review.
