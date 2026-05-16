"""SIO run-log subsystem (Principle XIII).

Public API:
    from sio.core.runlog import runlogged, current, RunLog

    @cli.command(...)
    @runlogged("optimize")
    def optimize_cmd(...):
        rl = current()
        with rl.stage("load_trainset") as s:
            ...
            s.set_rows(rows_in=N, rows_out=M)
            s.add_llm(calls=1, cost_usd=0.001)

Heartbeats:
    from sio.core.runlog import Heartbeat
    with rl.stage("optimize_loop") as s, Heartbeat(rl, s) as hb:
        for i, row in enumerate(...):
            hb.progress()
            ...
"""
from .decorator import runlogged
from .heartbeat import Heartbeat
from .writer import RunLog, Stage, current

__all__ = ["runlogged", "RunLog", "Stage", "Heartbeat", "current"]
