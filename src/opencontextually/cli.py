"""CLI entry point for the `opencontextually` and `octx` console scripts.

No subcommands: one positional task, one --root flag, plain-text output via
ContextPackage.render() -- the only rendering path in the codebase.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import get_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="octx",
        description=(
            "Select the local files relevant to a task, bounded to a "
            "project root, with a reason for every inclusion."
        ),
    )
    parser.add_argument("task", help="the task to select context for, e.g. 'fix the authentication bug'")
    parser.add_argument(
        "--root",
        default=".",
        help="project root to search (default: current directory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the ContextPackage as JSON (package.to_dict()) instead of plain text",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="include bounded code excerpts under each shown item (plain-text output only)",
    )
    parser.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="list every included file instead of the top slice (plain-text output only)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(
            f"error: --root '{args.root}' does not exist or is not a directory",
            file=sys.stderr,
        )
        return 2

    package = get_context(args.task, root=root)
    if args.json:
        # --json is the machine path: always full-fidelity, byte-identical
        # regardless of -v/--all, which only affect the human-readable
        # render() below.
        print(json.dumps(package.to_dict(), indent=2))
    else:
        print(package.render(verbose=args.verbose, show_all=args.show_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
