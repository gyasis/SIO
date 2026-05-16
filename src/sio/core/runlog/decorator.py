"""@runlogged decorator — wraps a click command in a RunLog context.

Usage:

    @cli.command("amplify")
    @click.option(...)
    @runlogged("amplify")
    def amplify_cmd(...):
        rl = current()
        with rl.stage("load_input") as s:
            ...
            s.set_rows(rows_in=93, rows_out=93)

If the function raises, the run-log captures the exception, sets
exit_class="error", and re-raises with exit code 1. Partial-success
exit code 3 is set automatically based on warnings.
"""
from __future__ import annotations

import functools
import sys
from typing import Callable

from . import dspy_capture, logging_filter
from .writer import RunLog, current, reset_current, set_current


def runlogged(cmd_name: str):
    """Decorate a click command function so it gets a RunLog context."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            argv = sys.argv[1:] if len(sys.argv) > 1 else []
            rl = RunLog(cmd_name, argv)
            tok = set_current(rl)
            # XIII clauses 3 + 7: capture stdlib logger warnings + DSPy I/O
            logging_filter.install()
            dspy_capture.install()
            raised = False
            try:
                result = fn(*args, **kwargs)
                return result
            except SystemExit as se:
                # click uses SystemExit; preserve its code but log if non-zero
                code = se.code if isinstance(se.code, int) else (0 if se.code is None else 1)
                if code != 0:
                    rl.error("SYSTEMEXIT", se)
                # Set raised=True so the `finally` doesn't double-finalize
                raised = True
                final = rl.finalize(code, raised=(code != 0))
                raise SystemExit(final) from se
            except BaseException as exc:
                raised = True
                rl.error("UNCAUGHT", exc)
                final = rl.finalize(1, raised=True)
                raise
            finally:
                if not raised:
                    rl.finalize(0, raised=False)
                # Uninstall capture/filter so subprocesses don't double-write
                try:
                    dspy_capture.uninstall()
                    logging_filter.uninstall()
                except Exception:
                    pass
                reset_current(tok)
        return wrapper
    return deco
