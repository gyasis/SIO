"""sio.core.paths — the single pointer for the SIO home + database location.

Covers:
  * sio_home() / db_path() default + env-override resolution, read at call
    time (not cached across env mutation).
  * A guard test that fails if the literal strings ``.sio/sio.db`` or
    ``".sio"`` appear anywhere in src/sio OUTSIDE sio/core/paths.py, as real
    code (not inside a docstring or a ``#`` comment) — the regression test
    for "one real pointer, no silent second default".
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from sio.core.paths import db_path, sio_home

SRC_ROOT = pathlib.Path(__file__).resolve().parents[3] / "src" / "sio"


# ---------------------------------------------------------------------------
# sio_home() / db_path() resolution
# ---------------------------------------------------------------------------


class TestSioHome:
    def test_default_is_dot_sio_under_home(self, monkeypatch):
        monkeypatch.delenv("SIO_HOME", raising=False)
        assert sio_home() == pathlib.Path.home() / ".sio"

    def test_honors_sio_home_env(self, monkeypatch, tmp_path):
        target = tmp_path / "alt-home"
        monkeypatch.setenv("SIO_HOME", str(target))
        assert sio_home() == target

    def test_expands_tilde_in_sio_home_env(self, monkeypatch):
        monkeypatch.setenv("SIO_HOME", "~/some-office-home")
        assert sio_home() == pathlib.Path.home() / "some-office-home"

    def test_read_at_call_time_not_cached(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SIO_HOME", raising=False)
        assert sio_home() == pathlib.Path.home() / ".sio"
        target = tmp_path / "second-home"
        monkeypatch.setenv("SIO_HOME", str(target))
        assert sio_home() == target  # no reload needed
        monkeypatch.delenv("SIO_HOME", raising=False)
        assert sio_home() == pathlib.Path.home() / ".sio"


class TestDbPath:
    def test_default_is_sio_home_slash_sio_db(self, monkeypatch):
        monkeypatch.delenv("SIO_HOME", raising=False)
        monkeypatch.delenv("SIO_DB_PATH", raising=False)
        assert db_path() == pathlib.Path.home() / ".sio" / "sio.db"

    def test_sio_db_path_env_overrides_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SIO_HOME", raising=False)
        target = tmp_path / "custom.db"
        monkeypatch.setenv("SIO_DB_PATH", str(target))
        assert db_path() == target

    def test_sio_home_env_moves_the_default_db(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SIO_DB_PATH", raising=False)
        home = tmp_path / "office-home"
        monkeypatch.setenv("SIO_HOME", str(home))
        assert db_path() == home / "sio.db"

    def test_sio_db_path_wins_over_sio_home(self, monkeypatch, tmp_path):
        home = tmp_path / "office-home"
        db = tmp_path / "elsewhere" / "sio.db"
        monkeypatch.setenv("SIO_HOME", str(home))
        monkeypatch.setenv("SIO_DB_PATH", str(db))
        assert db_path() == db

    def test_expands_tilde_in_sio_db_path_env(self, monkeypatch):
        monkeypatch.setenv("SIO_DB_PATH", "~/office/sio.db")
        assert db_path() == pathlib.Path.home() / "office" / "sio.db"

    def test_second_sio_home_is_independently_selectable(self, monkeypatch, tmp_path):
        """The concrete scenario the resolver exists for: two SIO installs,
        selected purely by env, with nothing falling back to the default."""
        office_home = tmp_path / "office" / ".sio"
        monkeypatch.setenv("SIO_HOME", str(office_home))
        monkeypatch.delenv("SIO_DB_PATH", raising=False)
        assert sio_home() == office_home
        assert db_path() == office_home / "sio.db"
        assert db_path() != pathlib.Path.home() / ".sio" / "sio.db"


# ---------------------------------------------------------------------------
# Guard: no second hardcoded default anywhere in src/sio
# ---------------------------------------------------------------------------


def _docstring_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """(lineno, end_lineno) spans of every module/class/function docstring."""
    spans: list[tuple[int, int]] = []
    nodes: list[ast.AST] = [tree] + [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    for n in nodes:
        body = getattr(n, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            spans.append((first.value.lineno, first.value.end_lineno))
    return spans


def _in_any_span(lineno: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= lineno <= end for start, end in spans)


def _find_literal_home_violations() -> list[str]:
    """Every real-code occurrence of ``.sio/sio.db`` / ``".sio"`` outside
    ``paths.py``, as a ``"<file>:<line>: <repr>"`` string. Comments are never
    string literals (the tokenizer/AST never sees them), so only docstrings
    need explicit exclusion.
    """
    violations: list[str] = []
    for f in sorted(SRC_ROOT.rglob("*.py")):
        if f.name == "paths.py" or "__pycache__" in f.parts:
            continue
        src = f.read_text()
        try:
            tree = ast.parse(src, filename=str(f))
        except SyntaxError as exc:  # pragma: no cover - would fail collection anyway
            violations.append(f"{f}: SYNTAX ERROR: {exc}")
            continue
        spans = _docstring_spans(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            value = node.value
            is_hit = (
                ".sio/sio.db" in value
                or value == ".sio"
                or value.startswith(".sio/")
                or value.startswith("~/.sio")
            )
            if not is_hit:
                continue
            if _in_any_span(node.lineno, spans):
                continue
            rel = f.relative_to(SRC_ROOT.parent.parent)
            violations.append(f"{rel}:{node.lineno}: {value!r}")
    return violations


def test_no_hardcoded_sio_home_literal_outside_paths_module():
    """Every default-path computation in src/sio MUST go through
    sio.core.paths — no second, silently-divergent hardcoded ``~/.sio``.
    """
    violations = _find_literal_home_violations()
    assert not violations, (
        "found hardcoded '.sio' / '.sio/sio.db' literals outside "
        "sio/core/paths.py (route them through sio_home()/db_path() "
        "instead):\n" + "\n".join(violations)
    )


def test_paths_module_itself_is_the_only_source_of_the_default():
    """Sanity check on the guard test: paths.py legitimately owns the
    literal, and is excluded from the scan by name."""
    text = (SRC_ROOT / "core" / "paths.py").read_text()
    assert '".sio"' in text
