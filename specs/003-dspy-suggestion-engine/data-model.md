# Data Model: DSPy Suggestion Engine

**Feature**: 003-dspy-suggestion-engine
**Date**: 2026-02-26

## New Tables

### ground_truth

Stores metadata for ground truth training examples (agent-generated, human-validated).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK AUTOINCREMENT | Row ID |
| pattern_id | TEXT | FK → patterns.pattern_id | Error pattern this ground truth addresses |
| error_examples_json | TEXT | NOT NULL | JSON: the input error examples |
| error_type | TEXT | NOT NULL | Error category |
| pattern_summary | TEXT | NOT NULL | Human-readable pattern description |
| target_surface | TEXT | NOT NULL | One of 7 surface types |
| rule_title | TEXT | NOT NULL | Generated improvement title |
| prevention_instructions | TEXT | NOT NULL | Generated improvement content |
| rationale | TEXT | NOT NULL | Why this improvement works |
| label | TEXT | NOT NULL DEFAULT 'pending' | pending, positive, negative |
| source | TEXT | NOT NULL DEFAULT 'agent' | agent, seed, approved, edited, rejected |
| confidence | REAL | | Quality metric score (0-1) |
| user_note | TEXT | | Human feedback note on reject/edit |
| file_path | TEXT | | Path to full JSON file |
| created_at | TEXT | NOT NULL | ISO-8601 timestamp |
| reviewed_at | TEXT | | When human reviewed |

**Indexes**:
- `idx_gt_pattern` ON (pattern_id)
- `idx_gt_label` ON (label)
- `idx_gt_source` ON (source)
- `idx_gt_surface` ON (target_surface)

### optimized_modules

Tracks saved DSPy optimized module versions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK AUTOINCREMENT | Row ID |
| module_type | TEXT | NOT NULL | 'suggestion', 'ground_truth', etc. |
| optimizer_used | TEXT | NOT NULL | 'bootstrap', 'miprov2', 'gepa' |
| file_path | TEXT | NOT NULL | Path to saved .json module |
| training_count | INTEGER | NOT NULL | Number of training examples used |
| metric_before | REAL | | Average metric score before optimization |
| metric_after | REAL | | Average metric score after optimization |
| is_active | INTEGER | NOT NULL DEFAULT 1 | 1 = currently loaded, 0 = superseded |
| created_at | TEXT | NOT NULL | ISO-8601 timestamp |

**Indexes**:
- `idx_om_active` ON (module_type, is_active)

## Modified Tables

### suggestions (existing)

Add column:
- `target_surface TEXT` — one of 7 surface types (nullable for backward compat with existing rows)
- `reasoning_trace TEXT` — DSPy ChainOfThought reasoning (nullable, only present for LLM-generated)

### SIOConfig dataclass (existing, in config.py)

Add fields:
```python
# [llm] section
llm_model: str | None = None
llm_api_key_env: str | None = None
llm_api_base_env: str | None = None
llm_temperature: float = 0.7
llm_max_tokens: int = 2000
# [llm.sub] section
llm_sub_model: str | None = None
```

## Data Flow

```
                  ┌──────────────┐
                  │ Error Records │ (from mining)
                  └──────┬───────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  Clustering  │
                  └──────┬───────┘
                         │
                         ▼
              ┌──────────────────┐
              │ Dataset Builder  │ → ~/.sio/datasets/{pattern_id}.json
              │ (error examples) │   INPUT: what went wrong
              └──────┬───────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   ┌─────────────┐     ┌───────────────┐
   │ Ground Truth │     │   DSPy        │
   │ Generator    │     │   Suggestion  │
   │ (candidates) │     │   Generator   │
   └──────┬──────┘     └───────┬───────┘
          │                    │
          ▼                    │
   ┌─────────────┐            │
   │ Human Review │            │ uses ground truth
   │ approve/     │            │ as trainset for
   │ reject/edit  │            │ optimizer
   └──────┬──────┘            │
          │                    │
          ▼                    │
   ┌─────────────┐            │
   │ Ground Truth │◄───────────┘
   │   Corpus     │
   │ (training    │ → ~/.sio/ground_truth/
   │  data)       │   OUTPUT: what ideal fix looks like
   └──────┬──────┘
          │
          ▼
   ┌─────────────┐
   │ DSPy        │
   │ Optimizers  │ BootstrapFewShot / MIPROv2
   └──────┬──────┘
          │
          ▼
   ┌─────────────┐
   │ Optimized   │ → ~/.sio/optimized/
   │ Module      │   Saved DSPy program
   └─────────────┘
```

## Entity Relationships

```
patterns 1──────* ground_truth      (one pattern has many GT candidates)
patterns 1──────* datasets          (existing)
patterns 1──────* suggestions       (existing)
datasets 1──────* suggestions       (existing)
ground_truth *──1 patterns          (GT references pattern)
suggestions 1──1 applied_changes    (existing)
optimized_modules (standalone — tracks module versions)
```

## Ground Truth JSON File Format

Location: `~/.sio/ground_truth/{pattern_id}_{candidate_num}.json`

```json
{
  "metadata": {
    "pattern_id": "tool_failure_bash_permission",
    "candidate_num": 1,
    "source": "agent",
    "label": "positive",
    "confidence": 0.87,
    "reviewed_at": "2026-02-26T14:30:00Z"
  },
  "input": {
    "error_examples": [
      {
        "error_text": "bash: /etc/hosts: Permission denied",
        "tool_name": "Bash",
        "user_message": "Add a hosts entry for dev.local",
        "error_type": "tool_failure",
        "session_id": "abc123"
      }
    ],
    "error_type": "tool_failure",
    "pattern_summary": "Bash tool fails with permission denied on /etc/* files"
  },
  "output": {
    "target_surface": "claude_md_rule",
    "rule_title": "Check file permissions before editing system files",
    "prevention_instructions": "Before using the Bash tool to write to files in /etc/ or other root-owned directories, first check if sudo is needed by running `ls -la <file>`. If the file requires root access, inform the user and ask them to confirm before using sudo.",
    "rationale": "Users repeatedly encounter permission denied errors when Claude attempts to edit system files without elevated privileges. This rule prevents the error by checking permissions first."
  }
}
```

## dspy.Example Conversion

```python
def ground_truth_to_dspy_example(gt_row: dict) -> dspy.Example:
    """Convert a ground_truth DB row to a dspy.Example for training."""
    return dspy.Example(
        error_examples=gt_row["error_examples_json"],
        error_type=gt_row["error_type"],
        pattern_summary=gt_row["pattern_summary"],
        target_surface=gt_row["target_surface"],
        rule_title=gt_row["rule_title"],
        prevention_instructions=gt_row["prevention_instructions"],
        rationale=gt_row["rationale"],
    ).with_inputs("error_examples", "error_type", "pattern_summary")
```
