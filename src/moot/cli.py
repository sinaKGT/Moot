"""Command-line entry point for the `moot` command."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from moot import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moot",
        description="A local-first platform for moderated multi-agent discussions.",
    )
    parser.add_argument("--version", action="store_true", help="show the version and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(f"moot {__version__}")
        return 0

    # Starting the server and opening the UI comes in a later step.
    print(f"Moot {__version__}: the discussion engine is not built yet.")
    return 0
