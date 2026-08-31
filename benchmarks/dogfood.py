#!/usr/bin/env python3
"""Run OpenContextually against real repositories and report what it did.

This is a *harness*, not a test suite. Scripted tests alone have repeatedly
proven insufficient on this project: the majority of real defects were found
by running the tool against unfamiliar repositories and reading the output.
This script makes that repeatable — same repos, same tasks, every time — so
a ranking change can be compared against a previous run instead of guessed
at.

It reports, per case: latency, how many files were scanned vs. excluded, the
top results with their reasons, which checks fired, whether the package is
byte-identical across two runs, and whether anything secret-shaped survived
redaction into the serialized package.

    python benchmarks/dogfood.py benchmarks/corpus.example.json

Repository paths are *not* committed. Copy the example config, point it at
clones on your own disk, and keep it local:

    cp benchmarks/corpus.example.json benchmarks/corpus.local.json
    $EDITOR benchmarks/corpus.local.json          # already gitignored
    python benchmarks/dogfood.py benchmarks/corpus.local.json

Nothing here is imported by the package, and `benchmarks/` is outside
`src/`, so it is never installed. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from opencontextually import get_context  # noqa: E402

# Shapes that must never survive redaction into a serialized package. This
# is the same defense-in-depth sweep SECURITY.md describes as best-effort:
# a hit here is a leak worth investigating, a clean run is not a guarantee.
SECRET_SHAPES = re.compile(
    r"sk-[A-Za-z0-9_]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
)

TOP_N = 8


def _fingerprint(package) -> str:
    return hashlib.sha256(
        json.dumps(package.to_dict(), sort_keys=True).encode()
    ).hexdigest()[:16]


def run_case(root: str, task: str, *, check_determinism: bool = True) -> dict:
    started = time.perf_counter()
    package = get_context(task, root=root)
    elapsed = time.perf_counter() - started

    serialized = json.dumps(package.to_dict())
    leaks = SECRET_SHAPES.findall(serialized)

    fingerprint = _fingerprint(package)
    deterministic = None
    if check_determinism:
        deterministic = _fingerprint(get_context(task, root=root)) == fingerprint

    return {
        "root": root,
        "task": task,
        "seconds": elapsed,
        "included": len(package.included),
        "excluded": package.excluded_count,
        "excluded_by_reason": package.excluded_by_reason,
        "conflicts": package.conflicts,
        "missing": package.missing,
        "weak_signal": package.weak_signal is not None,
        "rules_run": package.trace.get("rules_run", []),
        "leaks": len(leaks),
        "fingerprint": fingerprint,
        "deterministic": deterministic,
        "top": [(i.path, i.reason) for i in package.included[:TOP_N]],
        "package_bytes": len(serialized),
    }


def print_case(result: dict) -> None:
    name = Path(result["root"]).name
    print(f"\n{'=' * 78}")
    print(f"{name}  ·  {result['task']!r}")
    print(f"{'-' * 78}")

    determinism = (
        "byte-identical"
        if result["deterministic"]
        else ("NON-DETERMINISTIC" if result["deterministic"] is False else "not checked")
    )
    print(
        f"{result['seconds']:.2f}s  ·  {result['included']} included / "
        f"{result['excluded']} excluded  ·  {result['package_bytes']:,} B  ·  {determinism}"
    )

    dropped = result["excluded_by_reason"].get("over_cap", 0) + result[
        "excluded_by_reason"
    ].get("over_budget", 0)
    if dropped:
        # Files that cleared the relevance bar and were then cut for space.
        # The one exclusion number that means the reader is missing a real
        # result, so it is never folded into the totals above.
        print(f"  ! {dropped} above-bar files dropped (over_cap/over_budget)")

    if result["leaks"]:
        print(f"  !! {result['leaks']} SECRET-SHAPED STRING(S) IN SERIALIZED PACKAGE")

    if result["weak_signal"]:
        print("  ~ weak match reported")

    print(f"  checks run: {', '.join(result['rules_run']) or 'none'}")

    for path, reason in result["top"]:
        print(f"    {path[:58]:58} {reason[:40]}")
    if result["included"] > TOP_N:
        print(f"    (+{result['included'] - TOP_N} more)")

    for conflict in result["conflicts"]:
        print(f"  CONFLICT: {conflict.get('message', '')[:100]}")
    for gap in result["missing"]:
        print(f"  GAP:      {gap.get('message', '')[:100]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run OpenContextually against real repositories and report the results."
    )
    parser.add_argument("config", help="JSON corpus config (see corpus.example.json)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable results instead of the human report",
    )
    parser.add_argument(
        "--no-determinism-check",
        action="store_true",
        help="skip the second run per case (halves runtime)",
    )
    args = parser.parse_args(argv)

    try:
        config = json.loads(Path(args.config).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read corpus config {args.config!r}: {exc}", file=sys.stderr)
        return 2

    results = []
    skipped = []
    for entry in config.get("repos", []):
        root = Path(entry["root"]).expanduser()
        if not root.is_dir():
            # A corpus is personal to whoever runs it; a missing clone is
            # normal, not an error. Say so rather than failing the run.
            skipped.append(str(root))
            continue
        for task in entry["tasks"]:
            results.append(run_case(str(root), task, check_determinism=not args.no_determinism_check))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print_case(result)
        print(f"\n{'=' * 78}")
        leaks = sum(r["leaks"] for r in results)
        nondet = [r for r in results if r["deterministic"] is False]
        print(f"{len(results)} case(s) · {leaks} secret-shaped string(s) · {len(nondet)} non-deterministic")
        for path in skipped:
            print(f"skipped (not on disk): {path}")

    # Non-zero exit on the two things that are never acceptable.
    return 1 if (sum(r["leaks"] for r in results) or any(r["deterministic"] is False for r in results)) else 0


if __name__ == "__main__":
    sys.exit(main())
