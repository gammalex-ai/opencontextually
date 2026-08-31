"""CLI entry point for the `gctx`, `octx`, and `opencontextually` console scripts.

No subcommands: one positional task, one --root flag, plain-text output via
ContextPackage.render() -- the only rendering path in the codebase.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__, get_context

# --- bug fix: "-json" (single dash) produced a bare, unhelpful argparse
# error -------------------------------------------------------------------
#
# `octx "task" -json` is a natural typo (many CLIs accept a single dash for
# long options), but argparse rejects it as an "unrecognized arguments"
# token with no indication of what to do instead. The fix does not
# silently accept the single-dash form -- `-json` is not `--json` and
# treating it as such would set a precedent this parser does not otherwise
# honor (`-v` already means something different from `--v...`) -- it only
# recognizes the mistake and suggests the correct flag alongside argparse's
# normal error, so the user still has to type it correctly. Names here
# must be kept in sync with the long options registered below.
_LONG_FLAG_NAMES = ("root", "json", "verbose", "all", "version", "help")
_SINGLE_DASH_LONG_FLAG_RE = re.compile(
    r"(?<!-)-(" + "|".join(_LONG_FLAG_NAMES) + r")\b"
)


class _FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: D401 -- argparse's own signature
        hints = []
        for match in _SINGLE_DASH_LONG_FLAG_RE.finditer(message):
            hints.append(f"--{match.group(1)}")
        if hints:
            self.print_usage(sys.stderr)
            suggestion = " or ".join(dict.fromkeys(hints))  # de-dupe, keep order
            self.exit(
                2,
                f"{self.prog}: error: {message}\n"
                f"{self.prog}: hint: did you mean '{suggestion}' (two dashes)?\n",
            )
        super().error(message)


# The command is installed under three names (see pyproject.toml): `gctx`,
# the alias `octx`, and the spelled-out `opencontextually`. Usage text and
# error messages should name whichever one the user actually typed, so this
# reads argv[0] rather than hardcoding a single spelling -- telling someone
# who ran `octx` to fix their `gctx` invocation would be its own small lie.
# argparse's own default does the same thing, but falls down for `python -m
# opencontextually.cli`, where argv[0] is a path ending in "cli.py"; those
# module-style invocations fall back to the primary name.
_MODULE_INVOCATION_NAMES = {"cli.py", "__main__.py", "-c", ""}
DEFAULT_PROG = "gctx"


def _invoked_as() -> str:
    name = Path(sys.argv[0]).name if sys.argv else ""
    return DEFAULT_PROG if name in _MODULE_INVOCATION_NAMES else name


def main(argv: list[str] | None = None) -> int:
    parser = _FriendlyArgumentParser(
        prog=_invoked_as(),
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
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print the installed opencontextually version and exit",
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
