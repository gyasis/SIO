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
    return report


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
