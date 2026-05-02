---
name: sio-validate
description: Generate tool argument validation rules from SIO error patterns. Mines the SIO database for recurring tool_failure errors caused by bad arguments and proposes deny/auto-fix rules for the validate-args.js hook.
user-invocable: true
---

# SIO Validate — Generate Validation Rules from Error Patterns

Mine SIO error database and generate validation rules for the cascade-shield validate-args.js hook.

## Triggers
- "generate validation rules"
- "what tool arguments keep failing?"
- "update validations from errors"
- "/sio-validate"

## Execution

```bash
#!/bin/bash
set -e

echo "=== Stats ==="
python3 ~/.claude/hooks/cascade-shield/generate-validations.py --stats

echo ""
echo "=== Proposed Rules ==="
python3 ~/.claude/hooks/cascade-shield/generate-validations.py

echo ""
echo "=== Saving ==="
python3 ~/.claude/hooks/cascade-shield/generate-validations.py --apply
```

## After Running

1. Review the proposed rules and unmatched patterns
2. For high-count unmatched patterns, add new entries to `ARG_ERROR_PATTERNS` in `generate-validations.py`
3. For approved rules, add them to `validate-args.js` (DENY_RULES or AUTOFIX_RULES)
4. Test with: `echo '{"tool_name":"...","tool_input":{...}}' | node ~/.claude/hooks/cascade-shield/validate-args.js`

## Files
- Generator: `~/.claude/hooks/cascade-shield/generate-validations.py`
- Validator hook: `~/.claude/hooks/cascade-shield/validate-args.js`
- Generated rules: `~/.claude/hooks/cascade-shield/validate-args-generated.json`
- SIO database: `~/.sio/sio.db`
