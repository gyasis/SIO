---
name: sio-health
description: Show per-skill health metrics
requires:
  cli: "sio>=0.3.0"
  skills: []
  hooks: []
  optional: []
---

# SIO Health

## Dependencies
- **CLI:** `sio >= 0.3.0`
- **Skills:** none
- **Hooks:** none beyond SIO's telemetry hooks (registered by `sio init`)

Display per-skill performance metrics.

## Usage

```
sio health [--platform claude-code] [--skill Read] [--format table|json]
```
