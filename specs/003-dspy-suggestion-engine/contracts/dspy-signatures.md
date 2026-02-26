# DSPy Signature Contracts

**Feature**: 003-dspy-suggestion-engine

## Primary Signature: SuggestionGenerator

**Purpose**: Generate a targeted improvement from error patterns.

### Input Fields
| Field | Type | Description |
|-------|------|-------------|
| error_examples | str (JSON) | Array of error dicts: `[{error_text, tool_name, user_message, error_type, session_id}]` |
| error_type | str | Category: `tool_failure`, `user_correction`, `agent_admission`, `repeated_attempt`, `undo` |
| pattern_summary | str | Human-readable pattern description (from clustering) |

### Output Fields
| Field | Type | Description |
|-------|------|-------------|
| target_surface | str | One of: `claude_md_rule`, `skill_update`, `hook_config`, `mcp_config`, `settings_config`, `agent_profile`, `project_config` |
| rule_title | str | Concise improvement title (1 line) |
| prevention_instructions | str | Actionable prevention/improvement text (markdown) |
| rationale | str | Why this addresses the root cause |

### ChainOfThought Reasoning
When wrapped in `dspy.ChainOfThought`, produces intermediate `reasoning` field that explains:
1. What error pattern was identified
2. Which surface is the correct target and why
3. What specific prevention steps will address the root cause

---

## Ground Truth Generator Signature: GroundTruthCandidate

**Purpose**: Generate candidate ideal outputs for training data.

### Input Fields
Same as SuggestionGenerator inputs.

### Output Fields
Same as SuggestionGenerator outputs, plus:
| Field | Type | Description |
|-------|------|-------------|
| quality_assessment | str | Self-assessment of the candidate's quality |

---

## Metric Function Contract

**Input**: `(example: dspy.Example, pred: dspy.Prediction, trace=None) -> float`

**Scoring Dimensions** (each 0-1, averaged):
1. **Specificity** (0.35 weight): Does the output reference concrete details from error_examples?
2. **Actionability** (0.35 weight): Does prevention_instructions contain concrete steps?
3. **Surface Accuracy** (0.30 weight): Is target_surface correct for the error_type and context?

**Returns**: Float 0.0-1.0

**When `trace` is not None** (optimization mode): Returns bool (score > 0.5) for DSPy's optimization loop.
