# Hook Contracts: Claude Code Adapter

**Platform**: Claude Code
**Hook Type**: command (shell scripts reading JSON from stdin)

## PostToolUse Hook — Telemetry Capture

**Event**: Fires after every tool call completes.
**Handler**: `adapters/claude_code/hooks/post_tool_use.sh`

### Input (stdin JSON)

```json
{
  "session_id": "abc-123-def",
  "tool_name": "Read",
  "tool_input": { "file_path": "/path/to/file" },
  "tool_output": "file contents...",
  "error": null,
  "user_message": "Read the config file at /path/to/file"
}
```

> **Note**: `user_message` is the user's prompt that triggered this
> tool call. If the platform's hook payload does not include it
> natively, the hook handler MUST extract it from the most recent
> user turn in the session transcript (e.g., via the JSONL
> transcript at `~/.claude/projects/<project>/*.jsonl`). If
> extraction fails, set to `"[UNAVAILABLE]"` rather than blocking
> the telemetry write.

### Behavior

1. Parse stdin JSON
2. Extract `user_message` (from stdin if present, else from latest JSONL transcript entry, else `[UNAVAILABLE]`)
3. Call `sio core telemetry log` with extracted fields
4. Secret-scrub user_message before DB write
5. Exit 0 (never block the user's session)

### Output (stdout JSON)

```json
{ "action": "allow" }
```

Always returns allow — telemetry is passive observation.

### Error Handling

On any error: log the full invocation JSON and error trace to
`~/.sio/claude-code/error.log`, exit 0. NEVER disrupt the user session.
No retry, no queue — the invocation is dropped. This is acceptable
because the optimizer needs patterns across many invocations, not every
single one. The error log preserves the raw data for manual recovery
if needed.

---

## PreToolUse Hook — Real-Time Correction (V0.2+)

**Event**: Fires before a tool call executes.
**Handler**: `adapters/claude_code/hooks/pre_tool_use.sh`

### Input (stdin JSON)

```json
{
  "session_id": "abc-123-def",
  "tool_name": "WebSearch",
  "tool_input": { "query": "DSPy documentation" }
}
```

### Behavior (V0.1 — no-op pass-through)

1. Parse stdin JSON
2. Write `{"action": "allow"}` to stdout
3. Exit 0

> V0.1: No-op pass-through. Registered so the hook exists for V0.2
> active correction readiness. No DB writes, no logging.

### Behavior (V0.2+ — active correction)

1. Parse stdin JSON
2. Check if this tool_name + intent has a known correction pattern
3. If correction exists: return modified tool_input or deny + suggest
4. If no correction: allow

### Output (stdout JSON)

```json
// Allow (default)
{ "action": "allow" }

// Deny with reason (V0.2+)
{ "action": "deny", "reason": "Use gemini_research instead of WebSearch for documentation queries" }

// Modify input (V0.2+)
{ "action": "allow", "tool_input": { "modified": "fields" } }
```

---

## Notification Hook — Feedback Entry

**Event**: Fires on user messages matching `++` or `--` patterns.
**Handler**: `adapters/claude_code/hooks/notification.sh`

> **Implementation Note**: Claude Code's Notification event fires on
> assistant-to-user notifications, not user input. For `++`/`--`
> capture, the adapter MUST register a `UserPromptSubmit` hook (if
> available) or implement `++`/`--` parsing as a skill trigger
> (sio-feedback) that invokes the labeler via the CLI. The handler
> contract below applies regardless of which hook event delivers the
> message.

### Input (stdin JSON)

```json
{
  "session_id": "abc-123-def",
  "message": "-- should have used gemini_research"
}
```

### Behavior

1. Parse message for `++` or `--` prefix
2. Extract optional note (everything after `++`/`--`)
3. Call `sio core feedback label` with session_id, signal, note
4. Update most recent invocation's user_satisfied and user_note

### Output

```json
{ "action": "allow" }
```

---

## Skill Contracts

### sio-feedback/SKILL.md

```yaml
---
description: "Rate the AI's last action as helpful (++) or not helpful (--)"
allowed_tools: ["Bash"]
---
```

Trigger: User types `++`, `--`, or asks to rate/label an action.

> **V0.1 feedback mechanism**: `++`/`--` capture is handled by this
> skill trigger, NOT by a Notification hook. When the user types `++`
> or `--`, Claude Code matches the sio-feedback skill, which calls
> `python3 -m sio.core.feedback.labeler` via Bash with the session_id
> and signal. This is simpler and more reliable than a Notification
> hook (which fires on assistant→user events, not user input).

### sio-optimize/SKILL.md

```yaml
---
description: "Optimize a skill's prompts using accumulated feedback data"
allowed_tools: ["Bash", "Read", "Write", "Edit"]
---
```

Trigger: User asks to optimize, improve, or fix a skill's behavior.

### sio-health/SKILL.md

```yaml
---
description: "Show health metrics for AI skills and tools"
allowed_tools: ["Bash"]
---
```

Trigger: User asks about skill performance, health, or satisfaction rates.

### sio-review/SKILL.md

```yaml
---
description: "Review and label recent unlabeled AI interactions"
allowed_tools: ["Bash"]
---
```

Trigger: User asks to review, label, or rate past interactions.
