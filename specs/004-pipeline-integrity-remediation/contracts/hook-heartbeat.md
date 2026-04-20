# Contract — Hook Heartbeat (FR-016, SC-009)

**Branch**: `004-pipeline-integrity-remediation`
**Applies to**: `src/sio/adapters/claude_code/hooks/*`, `src/sio/cli/status.py`
**Reference**: `research.md` R-15 (TDD ordering), data-model.md §3.6

---

## 1. Purpose

Today every hook wraps its body in `except Exception: pass` (audit finding H8). A single misconfigured path silently drops 38k invocations and no one knows until a downstream query returns zero rows. FR-016 requires every hook to publish a heartbeat; `sio status` must surface degraded hooks within one heartbeat cycle (SC-009).

---

## 2. Storage

**File**: `~/.sio/hook_health.json` — single JSON file, atomic updates via temp + rename.

**Schema**:

```json
{
  "schema_version": 1,
  "updated_at": "2026-04-20T14:32:11+00:00",
  "hooks": {
    "post_tool_use": {
      "last_success":          "2026-04-20T14:32:11+00:00",
      "last_error":            null,
      "last_error_message":    null,
      "consecutive_failures":  0,
      "total_invocations":     38091,
      "last_session_id":       "abc123..."
    },
    "stop":           { ... },
    "pre_compact":    { ... }
  }
}
```

**Missing key** for a hook ⇒ `never-seen` (never fired).

---

## 3. Write Contract

```python
# src/sio/adapters/claude_code/hooks/_heartbeat.py
from __future__ import annotations
import json, os, time
from pathlib import Path
from sio.core.util.time import utc_now_iso

HEALTH_FILE = Path.home() / ".sio" / "hook_health.json"

def record_success(hook_name: str, session_id: str | None = None) -> None:
    _update(hook_name, success=True, session_id=session_id, error=None)

def record_failure(hook_name: str, error: BaseException) -> None:
    _update(hook_name, success=False, session_id=None, error=error)

def _update(hook_name: str, *, success: bool, session_id: str | None, error: BaseException | None) -> None:
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now_iso()
    try:
        data = json.loads(HEALTH_FILE.read_text()) if HEALTH_FILE.exists() else {"schema_version": 1, "hooks": {}}
    except Exception:
        data = {"schema_version": 1, "hooks": {}}
    h = data["hooks"].setdefault(hook_name, {
        "last_success": None, "last_error": None, "last_error_message": None,
        "consecutive_failures": 0, "total_invocations": 0, "last_session_id": None,
    })
    h["total_invocations"] = h.get("total_invocations", 0) + 1
    if success:
        h["last_success"] = now
        h["consecutive_failures"] = 0
        if session_id:
            h["last_session_id"] = session_id
    else:
        h["last_error"] = now
        h["last_error_message"] = f"{type(error).__name__}: {error}"
        h["consecutive_failures"] = h.get("consecutive_failures", 0) + 1
    data["updated_at"] = now
    tmp = HEALTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, HEALTH_FILE)
```

**Invariants**:
- Each hook entrypoint wraps its body in try/finally and calls `record_success()` or `record_failure()` (replaces bare `except: pass`).
- Atomic write (temp + `os.replace`) prevents partial JSON corruption if the hook is killed mid-write.
- `total_invocations` always monotonic — never reset on failure.
- Heartbeat write failures are themselves swallowed (we never want a corrupt health file to break the hook path), but logged to stderr.

---

## 4. Read Contract (`sio status`)

```python
# src/sio/cli/status.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

HEALTH_FILE = Path.home() / ".sio" / "hook_health.json"
WARN_STALE  = timedelta(hours=1)
ERROR_STALE = timedelta(hours=6)
EXPECTED_HOOKS = ("post_tool_use", "stop", "pre_compact")

def hook_health_rows() -> list[tuple[str, str, str]]:
    """Return [(hook_name, state, detail), ...] where state ∈ {healthy, warn, error, never-seen}."""
    if not HEALTH_FILE.exists():
        return [(h, "never-seen", "no heartbeat file") for h in EXPECTED_HOOKS]
    data = json.loads(HEALTH_FILE.read_text())
    rows = []
    now = datetime.now(timezone.utc)
    for hook in EXPECTED_HOOKS:
        h = data.get("hooks", {}).get(hook)
        if not h or not h.get("last_success"):
            rows.append((hook, "never-seen", "never fired"))
            continue
        last = datetime.fromisoformat(h["last_success"])
        age = now - last
        consec = h.get("consecutive_failures", 0)
        if consec >= 3:
            rows.append((hook, "error", f"{consec} consecutive failures; last error {h['last_error_message']}"))
        elif age > ERROR_STALE:
            rows.append((hook, "error", f"stale {age}"))
        elif age > WARN_STALE:
            rows.append((hook, "warn", f"stale {age}"))
        elif consec > 0:
            rows.append((hook, "warn", f"{consec} recent failures"))
        else:
            rows.append((hook, "healthy", f"last success {age} ago"))
    return rows
```

**Thresholds** (defaults; future-overridable via config):
- `healthy`: last_success < 1 h AND consecutive_failures == 0
- `warn`: last_success in [1 h, 6 h) OR consecutive_failures in {1, 2}
- `error`: last_success ≥ 6 h OR consecutive_failures ≥ 3
- `never-seen`: no entry at all

**Latency requirement**: `sio status` completes in < 2 s on a typical store (SC-009). Heartbeat read is O(1) file read — trivial contribution.

---

## 5. Hook Wrapping Pattern

Every Claude Code hook migrates to this pattern:

```python
# src/sio/adapters/claude_code/hooks/post_tool_use.py
from sio.adapters.claude_code.hooks._heartbeat import record_success, record_failure

def main() -> int:
    session_id = os.environ.get("CLAUDE_SESSION_ID")
    try:
        _do_work(session_id)     # all real hook logic here
    except Exception as e:       # NOT bare except: pass anymore
        record_failure("post_tool_use", e)
        # Hook still exits 0 so it doesn't break Claude Code's flow,
        # but the heartbeat now flags the issue.
        return 0
    record_success("post_tool_use", session_id=session_id)
    return 0
```

**Invariants**:
- Failure mode NEVER bubbles up to Claude Code (hook must not block the agent).
- Failure mode IS recorded (fixes audit finding H8).
- Hook-body failures are surfaced via `sio status`, not via hook exit code.

---

## 6. Test Coverage

| Test | Asserts |
|---|---|
| `tests/unit/hooks/test_heartbeat_success.py` | `record_success` increments counter, updates timestamps, resets consecutive_failures |
| `tests/unit/hooks/test_heartbeat_failure.py` | `record_failure` increments consecutive_failures, records error string |
| `tests/unit/hooks/test_heartbeat_atomic.py` | Simulated crash during `_update` leaves previous valid JSON intact |
| `tests/integration/test_sio_status_health.py` | (US6, SC-009) injected failure shows as `warn` within one cycle; 3 consecutive failures show as `error`; stale heartbeat shows as `stale` |
| `tests/unit/hooks/test_hook_body_failure_swallowed.py` | Raising inside hook body: hook exits 0, heartbeat records failure |

---

## 7. Future Extension

When a second platform adapter (Cursor, Codex) arrives, it adds its own hook names to `EXPECTED_HOOKS` and publishes to the same `~/.sio/hook_health.json`. The file remains shared across adapters; hooks are namespaced by `<platform>.<hook_name>` if collisions arise. Not needed yet (single-platform scope).
