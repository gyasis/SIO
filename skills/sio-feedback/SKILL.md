---
name: sio-feedback
description: Label the last AI action with satisfaction feedback (++ or --)
trigger: "^(\\+\\+|--)"
requires:
  cli: "sio>=0.3.0"
  skills: []
  hooks: []
  optional: []
---

# SIO Feedback

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** none
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)

Rate the last AI action:
- `++` — satisfied (action was helpful)
- `--` — dissatisfied (action was wrong/unhelpful)
- `++ great suggestion` — satisfied with note
- `-- wrong file` — dissatisfied with note

## Execution

```bash
#!/bin/bash
SESSION_ID="${CLAUDE_SESSION_ID:-unknown}"
SIGNAL="$(echo "$USER_INPUT" | head -c2)"
NOTE="$(echo "$USER_INPUT" | cut -c3- | sed 's/^ *//')"

SIO_PYTHON="$(command -v sio | xargs head -1 | sed 's/^#!//' || echo python3)"
"$SIO_PYTHON" -m sio.core.feedback.labeler_cli \
    --session "$SESSION_ID" \
    --signal "$SIGNAL" \
    ${NOTE:+--note "$NOTE"}
```
