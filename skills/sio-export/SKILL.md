---
name: sio-export
description: Export structured training datasets from mined sessions for DSPy/ML optimization. Generates routing, recovery, and flow prediction training pairs.
user-invocable: true
---

# SIO Export — Training Data for DSPy/ML

## When to Use
- "Export training data for DSPy"
- "Generate routing dataset"
- "Create error recovery training pairs"
- "Build flow prediction data"
- Before running DSPy GEPA optimization

## User Input

Parse natural language into CLI options:

| User says | Options |
|-----------|---------|
| "export all training data" | `--task all` |
| "routing pairs" | `--task routing` |
| "error recovery data" | `--task recovery` |
| "flow prediction" | `--task flow` |
| "export as parquet" | `--format parquet` |
| "last month" | `--since "30 days"` |

## Execution

```bash
sio export-dataset --task ${TASK:-all} --since "${SINCE:-14 days}" --format ${FORMAT:-jsonl}
```

## Dataset Types

### routing (largest)
- **Pairs:** (user_query, tool_choice, was_successful)
- **Use:** Train tool routing — which tool for which task
- **DSPy module:** `AgentRouter(dspy.Signature)`

### recovery (most valuable)
- **Triples:** (error_message, failed_tool, recovery_tool, was_successful)
- **Use:** Train self-correction — what to do after a failure
- **DSPy module:** `ErrorRecovery(dspy.Signature)`

### flow (sequence prediction)
- **Pairs:** (current_tools, next_tool, confidence)
- **Use:** Predict next best tool in a sequence
- **DSPy module:** `FlowPredictor(dspy.Signature)`

## Output Location

Default: `~/.sio/datasets/<task>_<date>.jsonl`

## JSONL Schema
```json
{
  "inputs": {"user_query": "...", "context": "..."},
  "outputs": {"tool_choice": "...", "was_successful": true},
  "metadata": {"session_id": "...", "task": "routing"}
}
```

## Follow-up
- Load into DSPy: `dspy.Example(**record["inputs"], **record["outputs"])`
- Run GEPA optimizer on routing dataset for best results
- Use BootstrapFewShot for <50 examples, MIPROv2 for 50+
