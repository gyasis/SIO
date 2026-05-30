---
name: sio-validate
description: Generate tool argument validation rules from SIO error patterns. Mines the SIO database for recurring tool_failure errors caused by bad arguments and proposes deny/auto-fix rules for the validate-args.js hook.
user-invocable: true
requires:
  cli: "sio>=0.3.0"
  skills: [sio]
  hooks: []
  optional: [cascade-shield]   # EXTERNAL hook — NOT shipped by SIO (see Dependencies note)
---

# SIO Validate — Generate Validation Rules from Error Patterns

## Dependencies
- **CLI:** `sio >= 0.3.0` (reads the SIO error database)
- **Skills:** `/sio` — master router; this skill is a sub-path of it
- **Hooks:** none of SIO's own hooks are required.
- **External (optional):** `cascade-shield` — a tool-argument-validation hook (`generate-validations.py` + `validate-args.js`) that is **NOT part of SIO and is not installed by `sio init`**. This skill mines SIO's error DB and feeds that external hook; it only does useful work if you have cascade-shield installed separately.
> **Portability note:** `sio-validate` is a bridge skill — SIO supplies the error data, but the `cascade-shield` hook it drives is a separate component you must install yourself. Without it, the generator commands below will not be present. The SIO-side value (mining recurring bad-argument `tool_failure` patterns) still works via `sio errors`/`sio patterns`.

Mine the SIO error database and generate validation rules for an external `cascade-shield` `validate-args.js` hook.

## Triggers
- "generate validation rules"
- "what tool arguments keep failing?"
- "update validations from errors"
- "/sio-validate"

## Execution

```bash
#!/bin/bash
set -e

# NOTE: these scripts belong to the EXTERNAL `cascade-shield` hook, which is
# NOT shipped or installed by SIO. Install cascade-shield separately first.
# If the paths below don't exist, this skill's generator step does not apply.

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
External `cascade-shield` hook (install separately — NOT shipped by SIO):
- Generator: `~/.claude/hooks/cascade-shield/generate-validations.py`
- Validator hook: `~/.claude/hooks/cascade-shield/validate-args.js`
- Generated rules: `~/.claude/hooks/cascade-shield/validate-args-generated.json`

SIO-owned (always present after `sio init`):
- SIO database: `~/.sio/sio.db`
