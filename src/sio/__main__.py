"""Enable ``python -m sio`` as an alias for the ``sio`` console script.

Used by the systemd user units (and any minimal-PATH subprocess), which invoke
``<python> -m sio ...`` with an absolute interpreter rather than relying on the
``sio`` entry-point being on PATH.
"""

from sio.cli.main import cli

if __name__ == "__main__":
    cli()
