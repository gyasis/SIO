---
name: sio-optimize
description: Run DSPy prompt optimization for a skill
requires:
  cli: "sio>=0.3.0"
  skills: []
  hooks: []
  optional: []
---

# SIO Optimize

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** none
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)

Trigger prompt optimization for a consistently failing skill.

## Usage

```
sio optimize <skill_name> [--dry-run] [--optimizer gepa|miprov2|bootstrap]
```
