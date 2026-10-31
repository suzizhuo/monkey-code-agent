#!/usr/bin/env python3
"""Cage host entry: stdio setup and CLI dispatch."""

import sys


def configure_stdio():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv=None):
    configure_stdio()
    from cage.cli import main as cli_main

    return cli_main(sys.argv if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
