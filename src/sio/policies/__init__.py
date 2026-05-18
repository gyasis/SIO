"""SIO Policy-as-Code v0.2.

Declarative YAML policies under ~/.sio/policies/{shadow,jit,enforced}/ get
compiled into ~/.sio/active_policies.json which the rules-injector PreToolUse
hook reads on every tool call.

Public API:
    compile_manifest()       — read YAMLs, write active_policies.json
    ingest_telemetry()       — drain telemetry.jsonl into policy_telemetry table
    compute_ia()             — Intervention Accuracy per policy
    check_health()           — lifecycle transitions based on IA + age
"""

from sio.policies.compile import compile_manifest
from sio.policies.lifecycle import check_health
from sio.policies.telemetry import compute_ia, ingest_telemetry

__all__ = ["compile_manifest", "ingest_telemetry", "compute_ia", "check_health"]
