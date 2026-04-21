"""Feedback labeler CLI — invoked by sio-feedback skill trigger."""

from __future__ import annotations

import argparse
import os
import sys

from sio.core.constants import DEFAULT_PLATFORM as _DEFAULT_PLATFORM

_DEFAULT_DB_DIR = os.path.expanduser(f"~/.sio/{_DEFAULT_PLATFORM}")


def main():
    """Parse args and apply label to most recent invocation."""
    parser = argparse.ArgumentParser(description="Label latest invocation")
    parser.add_argument("--session", required=True, help="Session ID")
    parser.add_argument(
        "--signal",
        required=True,
        choices=["++", "--"],
        help="Satisfaction signal",
    )
    parser.add_argument("--note", default=None, help="Optional note")
    args = parser.parse_args()

    from sio.core.db.schema import init_db
    from sio.core.feedback.labeler import label_latest

    db_path = os.path.join(_DEFAULT_DB_DIR, "behavior_invocations.db")
    os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
    conn = init_db(db_path)

    success = label_latest(conn, args.session, args.signal, args.note)
    conn.close()

    if success:
        print(f"Labeled with {args.signal}")
    else:
        print("No invocation found for session", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
