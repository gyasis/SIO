# Ground Truth Schema Contract

**Feature**: 003-dspy-suggestion-engine

## SQLite DDL

```sql
CREATE TABLE IF NOT EXISTS ground_truth (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL REFERENCES patterns(pattern_id),
    error_examples_json TEXT NOT NULL,
    error_type TEXT NOT NULL,
    pattern_summary TEXT NOT NULL,
    target_surface TEXT NOT NULL CHECK(target_surface IN (
        'claude_md_rule', 'skill_update', 'hook_config',
        'mcp_config', 'settings_config', 'agent_profile', 'project_config'
    )),
    rule_title TEXT NOT NULL,
    prevention_instructions TEXT NOT NULL,
    rationale TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT 'pending' CHECK(label IN ('pending', 'positive', 'negative')),
    source TEXT NOT NULL DEFAULT 'agent' CHECK(source IN ('agent', 'seed', 'approved', 'edited', 'rejected')),
    confidence REAL,
    user_note TEXT,
    file_path TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_gt_pattern ON ground_truth(pattern_id);
CREATE INDEX IF NOT EXISTS idx_gt_label ON ground_truth(label);
CREATE INDEX IF NOT EXISTS idx_gt_source ON ground_truth(source);
CREATE INDEX IF NOT EXISTS idx_gt_surface ON ground_truth(target_surface);
```

## Alter Existing Tables

```sql
-- Add columns to existing suggestions table
ALTER TABLE suggestions ADD COLUMN target_surface TEXT;
ALTER TABLE suggestions ADD COLUMN reasoning_trace TEXT;
```

## Optimized Modules Table

```sql
CREATE TABLE IF NOT EXISTS optimized_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_type TEXT NOT NULL,
    optimizer_used TEXT NOT NULL,
    file_path TEXT NOT NULL,
    training_count INTEGER NOT NULL,
    metric_before REAL,
    metric_after REAL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_om_active ON optimized_modules(module_type, is_active);
```

## Ground Truth Label State Machine

```
pending → positive    (user approves)
pending → negative    (user rejects)
pending → positive    (user edits → source becomes 'edited')
```

## Source Values

| Source | Meaning |
|--------|---------|
| `agent` | LLM-generated candidate, not yet reviewed |
| `seed` | Initial seed example (also agent-generated, reviewed during setup) |
| `approved` | Promoted from approved suggestion |
| `edited` | User-edited version of a rejected candidate |
| `rejected` | Negative training signal |
