# CLI Command Contracts

**Feature**: 003-dspy-suggestion-engine

## New Commands

### `sio ground-truth seed`
Generate initial seed ground truth using the LLM.

```
sio ground-truth seed [--count N] [--surface TYPE]
```

| Flag | Default | Description |
|------|---------|-------------|
| --count | 10 | Number of seed examples to generate |
| --surface | all | Filter to specific surface type |

**Output**: Rich table showing generated candidates with ID, surface, title, confidence.
**Exit**: Returns to shell; user runs `sio ground-truth review` next.

### `sio ground-truth generate [PATTERN_ID]`
Generate ground truth candidates for a specific pattern or all patterns.

```
sio ground-truth generate [PATTERN_ID] [--candidates N]
```

| Flag | Default | Description |
|------|---------|-------------|
| PATTERN_ID | all | Generate for specific pattern or all |
| --candidates | 3 | Number of candidates per pattern |

### `sio ground-truth review`
Interactive review of pending ground truth candidates.

```
sio ground-truth review [--surface TYPE]
```

**Interactive Flow**:
1. Shows candidate: pattern summary + generated output + target surface
2. Prompts: [a]pprove / [r]eject / [e]dit / [s]kip / [q]uit
3. On reject: optional note
4. On edit: opens $EDITOR with candidate content
5. Updates label and source accordingly

### `sio ground-truth status`
Show ground truth corpus statistics.

```
sio ground-truth status
```

**Output**: Table with per-surface counts (pending, approved, rejected, edited).

### `sio optimize suggestions`
Run DSPy optimizer on the suggestion generation module.

```
sio optimize suggestions [--optimizer TYPE] [--dry-run]
```

| Flag | Default | Description |
|------|---------|-------------|
| --optimizer | auto | `bootstrap`, `miprov2`, or `auto` (selects by data volume) |
| --dry-run | false | Show what would happen without running |

### `sio datasets inspect [PATTERN_ID]`
Show full dataset details for HITL analysis mode.

```
sio datasets inspect PATTERN_ID
```

**Output**: Error distribution, session timeline, ground truth entries, coverage gaps.

## Modified Commands

### `sio suggest` (existing — modified)
Now routes through DSPy when LLM is available.

```
sio suggest [--grep PATTERN] [--type TYPE] [--auto] [--analyze]
```

| New Flag | Description |
|----------|-------------|
| --auto | Force automated mode (skip HITL review) |
| --analyze | Force human-in-the-middle mode |

**Behavior Change**: When LLM is configured, calls DSPy module instead of string templates. Falls back to templates with warning when no LLM available.
