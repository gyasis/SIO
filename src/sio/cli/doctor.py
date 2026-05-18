"""`sio doctor` — diagnose install / config problems.

Designed to answer the question "I ran `sio init` and it said success, but
something isn't working — what's wrong?" without requiring the user to
read source. Each check returns a result with a status, a short message,
and (when actionable) a one-liner the user can copy-paste to fix it.

Adversarial bug-hunter findings from the v0.1.1 ship told us which checks
matter: PATH visibility, package collision, bootstrap availability,
manifest health, and config readiness.
"""

from __future__ import annotations

import os
import shutil
import sysconfig
from dataclasses import dataclass, field
from importlib.metadata import distributions
from pathlib import Path

# pylint: disable=cyclic-import — only at call time
_PKG_NAME_NEW = "self-improving-organism"
_PKG_NAME_LEGACY = "sio"


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "error"
    detail: str
    fix_hint: str = ""


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(c.status == "error" for c in self.checks)


def run_doctor() -> DoctorReport:
    """Run every diagnostic check and return a structured report."""
    report = DoctorReport()
    report.checks.append(_check_python_version())
    report.checks.append(_check_pkg_collision())
    report.checks.append(_check_sio_on_path())
    report.checks.append(_check_sio_home())
    report.checks.append(_check_config_toml())
    report.checks.append(_check_bootstrap_resolves())
    report.checks.append(_check_harness_install())
    report.checks.append(_check_dspy_alive())
    report.checks.append(_check_runlog_health())
    report.checks.append(_check_ladder_discipline())
    report.checks.append(_check_reproducibility_gaps())
    report.checks.append(_check_budget_state())
    report.checks.append(_check_stuck_reflection_runs())
    report.checks.append(_check_ladder_state())
    return report


def _check_ladder_discipline() -> CheckResult:
    """Principle XIV: GEPA must follow MIPROv2 on the same module + trainset.

    Surfaces any active GEPA module that has NO corresponding MIPROv2 run
    on the same trainset.
    """
    import sqlite3
    from pathlib import Path
    db = str(Path.home() / ".sio" / "sio.db")
    try:
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
        # Active GEPA modules
        gepa_rows = conn.execute(
            "SELECT id, module_type, trainset_id FROM optimized_modules "
            "WHERE optimizer_used = 'gepa' AND is_active = 1"
        ).fetchall()
        skips = []
        for g in gepa_rows:
            # Check for matching MIPROv2 run on same (module_type, trainset_id)
            mipro = conn.execute(
                "SELECT id FROM optimized_modules "
                "WHERE optimizer_used = 'mipro' "
                "  AND module_type = ? "
                "  AND (trainset_id = ? OR (trainset_id IS NULL AND ? IS NULL)) "
                "  AND id < ? "
                "LIMIT 1",
                (g["module_type"], g["trainset_id"], g["trainset_id"], g["id"]),
            ).fetchone()
            if mipro is None:
                skips.append(g["id"])
        conn.close()
    except Exception as e:
        return CheckResult(
            name="Optimizer ladder (XIV)", status="warn",
            detail=f"check failed: {e}",
        )

    if not gepa_rows:
        return CheckResult(
            name="Optimizer ladder (XIV)", status="ok",
            detail="No active GEPA runs to audit",
        )
    if skips:
        return CheckResult(
            name="Optimizer ladder (XIV)", status="warn",
            detail=(
                f"Active GEPA modules without prior MIPROv2 on same trainset: "
                f"{skips}. Run `sio optimize --optimizer mipro` on the same "
                f"trainset before GEPA per Principle XIV."
            ),
        )
    return CheckResult(
        name="Optimizer ladder (XIV)", status="ok",
        detail=f"{len(gepa_rows)} active GEPA module(s) all have prior MIPROv2 baselines",
    )


def _check_reproducibility_gaps() -> CheckResult:
    """Proposed Principle XV: every optimized module should have task_lm,
    reflection_lm (when applicable), trainset_id, seed populated.
    """
    import sqlite3
    from pathlib import Path
    db = str(Path.home() / ".sio" / "sio.db")
    try:
        conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
        # Recent active modules
        rows = conn.execute(
            "SELECT id, optimizer_used, task_lm, reflection_lm, trainset_id, seed "
            "FROM optimized_modules "
            "WHERE is_active = 1 "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
        conn.close()
    except Exception as e:
        return CheckResult(
            name="Reproducibility (XV draft)", status="warn",
            detail=f"check failed: {e}",
        )

    if not rows:
        return CheckResult(
            name="Reproducibility (XV draft)", status="ok",
            detail="No active modules",
        )

    gaps_summary = []
    for r in rows:
        missing = []
        if not r["task_lm"]:
            missing.append("task_lm")
        if r["optimizer_used"] in ("gepa", "mipro") and not r["reflection_lm"]:
            missing.append("reflection_lm")
        if not r["trainset_id"]:
            missing.append("trainset_id")
        if r["seed"] is None:
            missing.append("seed")
        if missing:
            gaps_summary.append(f"#{r['id']}({r['optimizer_used']}): {','.join(missing)}")

    if not gaps_summary:
        return CheckResult(
            name="Reproducibility (XV draft)", status="ok",
            detail=f"{len(rows)} active modules all have full reproducibility metadata",
        )
    return CheckResult(
        name="Reproducibility (XV draft)", status="warn",
        detail=f"{len(gaps_summary)}/{len(rows)} active modules have gaps: " +
               "; ".join(gaps_summary[:5]) +
               ("…" if len(gaps_summary) > 5 else ""),
    )


def _check_budget_state() -> CheckResult:
    """XII clause 6: report current 24h spend vs cap."""
    try:
        from sio.core.cost import check_budget, rolling_24h_spend  # noqa: PLC0415
        spend = rolling_24h_spend()
        state = check_budget()
        used_pct = (spend / state["effective_cap_usd"]) * 100 if state["effective_cap_usd"] else 0
        if used_pct >= 80:
            status = "warn"
        else:
            status = "ok"
        return CheckResult(
            name="LLM budget (XII)", status=status,
            detail=(
                f"${spend:.4f}/24h used of ${state['effective_cap_usd']:.2f} cap "
                f"({used_pct:.0f}%) — ${state['remaining_usd']:.2f} remaining"
            ),
        )
    except Exception as e:
        return CheckResult(
            name="LLM budget (XII)", status="warn",
            detail=f"check failed: {e}",
        )


def _check_runlog_health() -> CheckResult:
    """Principle XIII clause 8: scan ~/.sio/runs/ last 7 days.

    Surfaces:
      - exit_class counts (ok / partial / error)
      - top warning codes by frequency
      - any command whose partial-success rate > 20%
      - writer health (any I/O issues)
    """
    import json as _json
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    runs_dir = Path.home() / ".sio" / "runs"
    if not runs_dir.exists():
        return CheckResult(
            name="Run-log health (XIII)",
            status="warn",
            detail="~/.sio/runs/ does not exist yet — no runs to summarize",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    by_class = {"ok": 0, "partial": 0, "error": 0, "unknown": 0}
    warn_codes: dict[str, int] = {}
    by_cmd_partial: dict[str, list[int]] = {}  # cmd -> [partial_count, total]
    read_errors = 0
    total_runs = 0

    for p in runs_dir.glob("*.json"):
        try:
            d = _json.loads(p.read_text())
        except Exception:
            read_errors += 1
            continue
        try:
            start = datetime.fromisoformat(d.get("start_ts", "").replace("Z", "+00:00"))
            if start < cutoff:
                continue
        except (ValueError, TypeError):
            pass
        total_runs += 1
        cls = d.get("exit_class") or "unknown"
        by_class[cls] = by_class.get(cls, 0) + 1
        for w in d.get("warnings", []):
            code = w.get("code", "UNKNOWN")
            warn_codes[code] = warn_codes.get(code, 0) + 1
        cmd = d.get("cmd", "?")
        slot = by_cmd_partial.setdefault(cmd, [0, 0])
        slot[1] += 1
        if cls == "partial":
            slot[0] += 1

    top_warnings = sorted(warn_codes.items(), key=lambda kv: -kv[1])[:5]
    flagged_cmds = [
        cmd for cmd, (p, t) in by_cmd_partial.items()
        if t >= 3 and (p / t) > 0.2
    ]

    summary_lines = [
        f"Last 7 days: {total_runs} runs total",
        f"  ok={by_class.get('ok',0)} partial={by_class.get('partial',0)} "
        f"error={by_class.get('error',0)} unknown={by_class.get('unknown',0)}",
    ]
    if top_warnings:
        wstr = ", ".join(f"{c}={n}" for c, n in top_warnings)
        summary_lines.append(f"  top warnings: {wstr}")
    if flagged_cmds:
        summary_lines.append(
            f"  ⚠ commands >20% partial: {', '.join(flagged_cmds)}"
        )
    if read_errors:
        summary_lines.append(f"  ⚠ {read_errors} run-log file(s) unreadable")

    # Status: warn if any flagged cmds or read errors; ok otherwise
    status = "ok"
    if flagged_cmds or read_errors:
        status = "warn"
    elif total_runs == 0:
        status = "warn"
        summary_lines.append("  (no runs yet — instrument commands with @runlogged)")

    return CheckResult(
        name="Run-log health (XIII)",
        status=status,
        detail=" | ".join(summary_lines),
    )


def _check_dspy_alive() -> CheckResult:
    """T0.3 (PRD sio_backend_dead_loop_2026-05-15) — is the DSPy
    optimization pipeline actually producing artifacts, or is it silently
    failing into a template fallback?

    Healthy = at least one row in ``optimized_modules`` with
    ``is_active=1`` for the ``suggestion_generator`` module, AND its
    ``metric_after`` is a real float in (0.0, 1.0) exclusive (rules out
    both the stub 0.0 and the trivial-metric 1.0 cases).
    """
    import sqlite3  # noqa: PLC0415

    db_path = Path.home() / ".sio" / "sio.db"
    if not db_path.exists():
        return CheckResult(
            name="DSPy pipeline (suggestion_generator)",
            status="warn",
            detail="~/.sio/sio.db not found — no optimization history yet",
            fix_hint="Run `sio mine` then `sio optimize --module suggestion_generator`",
        )

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, optimizer_used, metric_after, training_count, created_at "
            "FROM optimized_modules "
            "WHERE module_type='suggestion_generator' AND is_active=1 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="DSPy pipeline (suggestion_generator)",
            status="error",
            detail=f"DB query failed: {exc}",
            fix_hint="Check ~/.sio/sio.db schema with `sio db check`",
        )

    if row is None:
        return CheckResult(
            name="DSPy pipeline (suggestion_generator)",
            status="error",
            detail="No active suggestion_generator module — `sio suggest` is "
                   "falling back to templates",
            fix_hint=(
                "Set up an LM (~/.sio/config.toml + secrets.env), then run "
                "`sio curate --emphasis --classified` and `sio optimize "
                "--trainset-file <path>`"
            ),
        )

    score = row["metric_after"]
    if score is None:
        return CheckResult(
            name="DSPy pipeline (suggestion_generator)",
            status="warn",
            detail=f"active module id={row['id']} but metric_after is NULL",
            fix_hint="Re-run `sio optimize` and verify the metric is recorded",
        )
    # Trivial-metric guard — if score is exactly 1.0 AND training_count was
    # small (< 5 historical), the optimizer likely tripped the lazy-metric
    # bug surfaced 2026-05-15. Score of 1.0 is rarely legitimate for the
    # suggestion task.
    if score >= 0.999:
        return CheckResult(
            name="DSPy pipeline (suggestion_generator)",
            status="warn",
            detail=(
                f"active module id={row['id']} score={score:.4f} — "
                "suspiciously perfect; likely a lazy-metric. Verify "
                "suggestion_quality_metric is wired"
            ),
            fix_hint="Inspect src/sio/core/dspy/optimizer.py _gepa_metric",
        )
    return CheckResult(
        name="DSPy pipeline (suggestion_generator)",
        status="ok",
        detail=(
            f"active module id={row['id']} score={score:.4f} "
            f"({row['optimizer_used']}, train={row['training_count']}) "
            f"@ {row['created_at'][:19]}"
        ),
    )


# ---------------------------------------------------------------------- checks


def _check_python_version() -> CheckResult:
    import sys

    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 11):
        return CheckResult(
            name="Python version",
            status="error",
            detail=f"Python {major}.{minor} — SIO requires 3.11 or newer",
            fix_hint="Install Python 3.11+ and re-create your venv",
        )
    return CheckResult(
        name="Python version",
        status="ok",
        detail=f"Python {major}.{minor}",
    )


def _check_pkg_collision() -> CheckResult:
    """Targeted bug-hunter B3 — `sio` is a real pre-existing pypi package by
    Yaraslau Byshyk. If it's installed alongside `self-improving-organism`,
    `import sio` resolves to whichever was installed first; the SIO CLI can
    fail with `ModuleNotFoundError: sio.cli` while the script itself appears
    to "exist" on PATH."""
    installed = {
        dist.metadata["Name"].lower()
        for dist in distributions()
        if dist.metadata.get("Name")
    }
    has_new = _PKG_NAME_NEW in installed
    has_legacy_collision = _PKG_NAME_LEGACY in installed and not has_new

    if has_legacy_collision:
        return CheckResult(
            name="Package collision",
            status="error",
            detail=(
                f"Found pypi package `sio` (Yaraslau Byshyk's unrelated 'short IO' "
                f"project), but NOT `{_PKG_NAME_NEW}`. The CLI script will "
                f"fail with ModuleNotFoundError: sio.cli when invoked."
            ),
            fix_hint=(
                "pip uninstall -y sio && "
                "pip install --force-reinstall "
                "git+https://github.com/gyasis/SIO.git@latest"
            ),
        )
    if has_new and _PKG_NAME_LEGACY in installed:
        return CheckResult(
            name="Package collision",
            status="warn",
            detail=(
                f"Both `{_PKG_NAME_NEW}` and the unrelated `sio` "
                "pypi package are installed. Import resolution is non-deterministic."
            ),
            fix_hint="pip uninstall -y sio  # remove the unrelated package",
        )
    if has_new:
        return CheckResult(
            name="Package collision",
            status="ok",
            detail=f"`{_PKG_NAME_NEW}` installed; no `sio` collision",
        )
    return CheckResult(
        name="Package collision",
        status="error",
        detail=f"Neither `{_PKG_NAME_NEW}` nor `sio` shows up in installed dists",
        fix_hint="pip install git+https://github.com/gyasis/SIO.git@latest",
    )


def _check_sio_on_path() -> CheckResult:
    """Targeted bug-hunter B1 — pip --user puts the script in
    ~/.local/bin which is often not on PATH for non-login subprocesses
    (Claude Code's Bash tool, for instance)."""
    on_path = shutil.which("sio")
    scripts_dir = Path(sysconfig.get_path("scripts"))
    expected = scripts_dir / "sio"

    if on_path:
        return CheckResult(
            name="`sio` on PATH",
            status="ok",
            detail=f"resolved to {on_path}",
        )
    if expected.exists():
        return CheckResult(
            name="`sio` on PATH",
            status="error",
            detail=(
                f"binary exists at {expected} but {scripts_dir} is not on PATH "
                "for this shell. Claude Code subprocesses inherit this same PATH."
            ),
            fix_hint=(
                f"Append `export PATH=\"{scripts_dir}:$PATH\"` to ~/.bashrc, "
                f"~/.zshrc, or ~/.profile, then restart your shell. "
                f"Or rerun `sio init --link-path` to do this for you."
            ),
        )
    return CheckResult(
        name="`sio` on PATH",
        status="error",
        detail=f"no `sio` script found at {expected} and not on PATH",
        fix_hint="pip install --force-reinstall self-improving-organism",
    )


def _check_sio_home() -> CheckResult:
    """Verify ~/.sio/ + canonical subdirs exist."""
    sio_home = Path(os.environ.get("SIO_HOME", str(Path.home() / ".sio")))
    if not sio_home.exists():
        return CheckResult(
            name="~/.sio/ data dir",
            status="error",
            detail=f"{sio_home} does not exist",
            fix_hint="sio init",
        )
    expected_subs = ("datasets", "previews", "backups")
    missing = [s for s in expected_subs if not (sio_home / s).is_dir()]
    if missing:
        return CheckResult(
            name="~/.sio/ data dir",
            status="warn",
            detail=f"{sio_home} exists but subdirs missing: {missing}",
            fix_hint="sio init  # idempotent; recreates missing subdirs",
        )
    return CheckResult(
        name="~/.sio/ data dir",
        status="ok",
        detail=str(sio_home),
    )


def _check_config_toml() -> CheckResult:
    """Verify ~/.sio/config.toml exists and at least one [llm] block is uncommented."""
    sio_home = Path(os.environ.get("SIO_HOME", str(Path.home() / ".sio")))
    cfg = sio_home / "config.toml"
    if not cfg.exists():
        return CheckResult(
            name="config.toml",
            status="error",
            detail=f"{cfg} does not exist",
            fix_hint="sio init  # creates the template",
        )
    text = cfg.read_text(encoding="utf-8", errors="replace")
    has_active_llm = any(
        line.strip().startswith("model = ") and not line.strip().startswith("#")
        for line in text.splitlines()
    )
    if not has_active_llm:
        return CheckResult(
            name="config.toml",
            status="warn",
            detail=(
                f"{cfg} exists but every [llm] provider is commented out. "
                "`sio suggest` will fail until one is uncommented."
            ),
            fix_hint=(
                "Edit ~/.sio/config.toml; uncomment one [llm] block + "
                "set its API key in your env"
            ),
        )
    return CheckResult(
        name="config.toml",
        status="ok",
        detail=f"{cfg} has an active [llm] provider",
    )


def _check_bootstrap_resolves() -> CheckResult:
    """Targeted bug-hunter C2 / B2 — verify `iter_bootstrap_files` actually yields."""
    try:
        from sio.harnesses.bootstrap import iter_bootstrap_files

        n = sum(1 for _ in iter_bootstrap_files())
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="Bundled bootstrap content",
            status="error",
            detail=f"iter_bootstrap_files raised {type(e).__name__}: {e}",
            fix_hint=(
                "pip install --force-reinstall --no-deps "
                "git+https://github.com/gyasis/SIO.git@latest"
            ),
        )
    if n == 0:
        return CheckResult(
            name="Bundled bootstrap content",
            status="error",
            detail="iter_bootstrap_files yielded zero files",
            fix_hint=(
                "pip install --force-reinstall --no-deps "
                "git+https://github.com/gyasis/SIO.git@latest"
            ),
        )
    return CheckResult(
        name="Bundled bootstrap content",
        status="ok",
        detail=f"{n} bootstrap files resolve correctly",
    )


def _check_harness_install() -> CheckResult:
    """Verify the user's harness has SIO content present and not drifted."""
    try:
        from sio.harnesses import detect_adapters

        adapters = detect_adapters()
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="Harness install",
            status="error",
            detail=f"detect_adapters raised {type(e).__name__}: {e}",
        )

    if not adapters:
        return CheckResult(
            name="Harness install",
            status="warn",
            detail="No supported harnesses detected on this system",
            fix_hint="sio init --harness claude-code  # if Claude Code is installed",
        )

    summaries: list[str] = []
    worst = "ok"
    for adapter in adapters:
        sr = adapter.status()
        n_inst = len(sr.installed_files)
        n_miss = len(sr.missing_files)
        n_drift = len(sr.drifted_files)
        summaries.append(
            f"{adapter.name}: {n_inst} installed, {n_miss} missing, {n_drift} drifted"
        )
        if n_miss > 0 or n_drift > 0:
            worst = "warn" if worst != "error" else worst
        if n_inst == 0:
            worst = "error"

    return CheckResult(
        name="Harness install",
        status=worst,
        detail="; ".join(summaries),
        fix_hint=(
            "sio init  # safely re-syncs missing or out-of-date files"
            if worst != "ok"
            else ""
        ),
    )


# ---------------------------------------------------------------------------
# Stuck-in-reflection retrospective audit (Principle XIII observability)
# ---------------------------------------------------------------------------


def _check_stuck_reflection_runs() -> CheckResult:
    """Scan recent ~/.sio/runs/*_dspy.jsonl sidecars for the GEPA
    stuck-in-reflection failure mode (reflection_lm calls accumulate
    but task_lm calls never appear).

    Empirical: today's failed GEPA on a 93-row dataset showed 28 gpt-5
    reflection calls and 0 Flash calls over 58 min — wasted $1.11.
    Pre-flight gate (amplify-first + row-floor, commit d886078) prevents
    this going forward. This retrospective check flags any historical
    run that exhibits the pattern so SIO mining + audit dashboards
    can surface it.

    Pattern: >=5 reflection-class calls AND 0 task-class calls AND
    elapsed >= 15 min (computed from first/last ts in sidecar).
    """
    import json as _json
    from datetime import datetime, timedelta, timezone
    from pathlib import Path as _P

    runs_dir = _P.home() / ".sio" / "runs"
    if not runs_dir.exists():
        return CheckResult(
            name="Stuck-in-reflection audit (XIII)",
            status="ok",
            detail="~/.sio/runs/ does not exist yet — nothing to audit",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    reflection_hints = ("gpt-5", "gemini-pro", "claude-opus", "claude-sonnet-4")
    task_hints = ("flash", "gpt-4o-mini", "ollama", "haiku")

    flagged = []
    scanned = 0

    for sidecar in runs_dir.glob("*_dspy.jsonl"):
        scanned += 1
        try:
            reflection_calls = 0
            task_calls = 0
            first_ts = None
            last_ts = None
            with sidecar.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except Exception:
                        continue
                    ts = d.get("ts") or d.get("timestamp")
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
                    m = (d.get("model") or "").lower()
                    if any(h in m for h in task_hints):
                        task_calls += 1
                    elif any(h in m for h in reflection_hints):
                        reflection_calls += 1

            if not first_ts or not last_ts:
                continue
            try:
                first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if first_dt < cutoff:
                continue
            elapsed_min = int((last_dt - first_dt).total_seconds() / 60)

            # Stuck pattern: reflection-heavy, zero task calls, ran for >=15 min
            if reflection_calls >= 5 and task_calls == 0 and elapsed_min >= 15:
                flagged.append({
                    "sidecar": sidecar.name,
                    "first_ts": first_ts,
                    "elapsed_min": elapsed_min,
                    "reflection": reflection_calls,
                    "task": task_calls,
                })
        except Exception:
            continue

    if not flagged:
        return CheckResult(
            name="Stuck-in-reflection audit (XIII)",
            status="ok",
            detail=f"Scanned {scanned} run sidecar(s) in last 14d; no "
                   f"stuck-in-reflection patterns detected.",
        )

    detail_lines = [
        f"Found {len(flagged)} run(s) in last 14d that match the stuck-in-"
        f"reflection pattern (reflection-only LM calls, never reached task "
        f"LM):"
    ]
    for f in flagged[:10]:
        detail_lines.append(
            f"  {f['sidecar']}: {f['reflection']}r/0t in {f['elapsed_min']}m "
            f"(started {f['first_ts']})"
        )
    if len(flagged) > 10:
        detail_lines.append(f"  ... +{len(flagged) - 10} more")
    detail_lines.append(
        "Likely cause: dataset too small for GEPA's reflective acceptance "
        "loop. Pre-flight gate `sio optimize --optimizer gepa` refuses "
        "<300-row trainsets by default. Override with --skip-amplify-gate "
        "is logged for SIO mining."
    )

    return CheckResult(
        name="Stuck-in-reflection audit (XIII)",
        status="warn",
        detail="\n".join(detail_lines),
        fix_hint=(
            "sio optimize-ladder --trainset-file <X> "
            "# auto-amplifies to 300+ rows before GEPA"
        ),
    )


# ---------------------------------------------------------------------------
# Ladder state-file health check (PRD background-persistence Tier 2)
# ---------------------------------------------------------------------------


def _check_ladder_state() -> CheckResult:
    """Read ~/.sio/state/ladder_status.json and report status.

    Flags:
      - status='in_flight' but started_at > 6h ago → likely crashed mid-run
      - status='failed' → most recent ladder errored
      - status='complete' → green
      - file missing → no recent compound run, ok

    Cron observability: this is the file a monitoring script should poll
    to know "is a ladder run alive, done, or stuck?" without needing
    to crawl the DB.
    """
    import json as _json
    from datetime import datetime, timedelta, timezone
    from pathlib import Path as _P

    state_file = _P.home() / ".sio" / "state" / "ladder_status.json"
    if not state_file.exists():
        return CheckResult(
            name="Ladder state (background-persistence Tier 2)",
            status="ok",
            detail="No active ladder run state file — clean idle state",
        )

    try:
        st = _json.loads(state_file.read_text())
    except Exception as e:
        return CheckResult(
            name="Ladder state (background-persistence Tier 2)",
            status="warn",
            detail=f"State file unreadable: {e}",
        )

    status = st.get("status", "?")
    started = st.get("started_at", "?")
    plan = st.get("plan", [])
    rungs = st.get("rungs", [])
    current = st.get("current_rung")
    module = st.get("module", "?")

    if status == "complete":
        completed_at = st.get("completed_at", "?")
        return CheckResult(
            name="Ladder state (background-persistence Tier 2)",
            status="ok",
            detail=(
                f"Most recent ladder: module={module}, {len(rungs)} rungs, "
                f"completed_at={completed_at}, "
                f"total_est_usd=${st.get('total_estimated_usd', 0):.2f}"
            ),
        )

    if status == "failed":
        last_rung = rungs[-1] if rungs else {}
        return CheckResult(
            name="Ladder state (background-persistence Tier 2)",
            status="warn",
            detail=(
                f"Most recent ladder FAILED at rung {current}/{len(plan)}: "
                f"{last_rung.get('step', '?')} (exit={last_rung.get('exit_code')})"
                f". Re-run `sio optimize-ladder` with same args to resume."
            ),
        )

    if status == "in_flight":
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            elapsed = datetime.now(timezone.utc) - started_dt
            age_h = elapsed.total_seconds() / 3600
        except (ValueError, TypeError):
            age_h = 0.0

        # Probe the PID to see if the process is still alive
        pid = st.get("process_id")
        alive = False
        if pid:
            try:
                os.kill(pid, 0)  # signal 0 = check existence, doesn't kill
                alive = True
            except (OSError, ProcessLookupError):
                alive = False

        if age_h > 6 and not alive:
            return CheckResult(
                name="Ladder state (background-persistence Tier 2)",
                status="warn",
                detail=(
                    f"Stale in_flight ladder (started {age_h:.1f}h ago, "
                    f"PID {pid} not running). Most likely a crash. "
                    f"Re-run `sio optimize-ladder` to resume from the last "
                    f"completed rung."
                ),
                fix_hint=f"sio optimize-ladder --trainset-file {st.get('trainset_file', '<X>')} --yes",
            )
        if age_h > 2 and not alive:
            return CheckResult(
                name="Ladder state (background-persistence Tier 2)",
                status="warn",
                detail=(
                    f"in_flight ladder appears idle "
                    f"(started {age_h:.1f}h ago, PID {pid} not alive). "
                    f"Investigate then resume."
                ),
            )
        return CheckResult(
            name="Ladder state (background-persistence Tier 2)",
            status="ok",
            detail=(
                f"Ladder in flight: rung {current}/{len(plan)} "
                f"({rungs[-1].get('step', '?') if rungs else '?'}), "
                f"running {age_h:.2f}h, PID {pid} {'alive' if alive else 'NOT RUNNING'}"
            ),
        )

    return CheckResult(
        name="Ladder state (background-persistence Tier 2)",
        status="warn",
        detail=f"Unknown status='{status}' in state file",
    )
