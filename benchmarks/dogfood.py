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
DEFAULT_ANSWER_KEYS = Path(__file__).with_name("answer-keys.json")


def _fingerprint(package) -> str:
    return hashlib.sha256(
        json.dumps(package.to_dict(), sort_keys=True).encode()
    ).hexdigest()[:16]


def load_answer_keys(path: Path = DEFAULT_ANSWER_KEYS) -> dict[tuple[str, str], dict]:
    """Index canonical benchmark tasks without duplicating their truth sets."""
    data = json.loads(path.read_text())
    return {(entry["repo"], entry["task"]): entry for entry in data.get("keys", [])}


def iter_task_phrasings(
    repo_name: str,
    tasks: list[str],
    answer_keys: dict[tuple[str, str], dict],
    phrasing: str,
):
    """Yield (task text, phrasing label, shared answer-key entry).

    The canonical ``task`` remains the existing comparable series. Alternate
    wording lives on that same entry under ``task_variants`` so phrasing can
    never accidentally acquire a different ground truth.
    """
    for canonical_task in tasks:
        answer_key = answer_keys.get((repo_name, canonical_task))
        if phrasing in ("existing", "all"):
            yield canonical_task, "existing", answer_key
        if answer_key is None:
            continue
        variants = answer_key.get("task_variants", {})
        if phrasing == "all":
            requested = variants
        elif phrasing in variants:
            requested = {phrasing: variants[phrasing]}
        else:
            requested = {}
        for label, task in requested.items():
            yield task, label, answer_key


def run_case(
    root: str,
    task: str,
    *,
    check_determinism: bool = True,
    phrasing: str = "existing",
    answer_key: dict | None = None,
) -> dict:
    started = time.perf_counter()
    package = get_context(task, root=root)
    elapsed = time.perf_counter() - started

    serialized = json.dumps(package.to_dict())
    leaks = SECRET_SHAPES.findall(serialized)

    fingerprint = _fingerprint(package)
    deterministic = None
    if check_determinism:
        deterministic = _fingerprint(get_context(task, root=root)) == fingerprint

    included_paths = [item.path for item in package.included]
    expected_paths = list(answer_key.get("files", {})) if answer_key else []
    found_paths = [path for path in expected_paths if path in included_paths]
    visible_found_paths = [path for path in expected_paths if path in included_paths[:TOP_N]]

    return {
        "root": root,
        "task": task,
        "phrasing": phrasing,
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
        "answer_key_total": len(expected_paths),
        "answer_key_found": len(found_paths),
        "answer_key_visible_found": len(visible_found_paths),
        "answer_key_missing": [path for path in expected_paths if path not in included_paths],
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
    if result["answer_key_total"]:
        print(
            f"  answer key ({result['phrasing']}): "
            f"{result['answer_key_found']}/{result['answer_key_total']} package · "
            f"{result['answer_key_visible_found']}/{result['answer_key_total']} top {TOP_N}"
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
    parser.add_argument(
        "--phrasing",
        choices=("existing", "technical", "natural", "all"),
        default="existing",
        help=(
            "which task wording to run; existing preserves the historical series, "
            "while technical/natural reuse the same answer key"
        ),
    )
    parser.add_argument(
        "--answer-keys",
        default=str(DEFAULT_ANSWER_KEYS),
        help="answer-key JSON used for recall and shared task variants",
    )
    args = parser.parse_args(argv)

    try:
        config = json.loads(Path(args.config).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read corpus config {args.config!r}: {exc}", file=sys.stderr)
        return 2

    try:
        answer_keys = load_answer_keys(Path(args.answer_keys))
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: cannot read answer keys {args.answer_keys!r}: {exc}", file=sys.stderr)
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
        repo_name = entry.get("repo") or entry.get("github", "").rsplit("/", 1)[-1] or root.name
        for task, phrasing, answer_key in iter_task_phrasings(
            repo_name, entry["tasks"], answer_keys, args.phrasing
        ):
            results.append(
                run_case(
                    str(root),
                    task,
                    check_determinism=not args.no_determinism_check,
                    phrasing=phrasing,
                    answer_key=answer_key,
                )
            )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print_case(result)
        print(f"\n{'=' * 78}")
        leaks = sum(r["leaks"] for r in results)
        nondet = [r for r in results if r["deterministic"] is False]
        print(f"{len(results)} case(s) · {leaks} secret-shaped string(s) · {len(nondet)} non-deterministic")
        for phrasing in ("existing", "technical", "natural"):
            phrased = [r for r in results if r["phrasing"] == phrasing and r["answer_key_total"]]
            if not phrased:
                continue
            found = sum(r["answer_key_found"] for r in phrased)
            visible = sum(r["answer_key_visible_found"] for r in phrased)
            total = sum(r["answer_key_total"] for r in phrased)
            print(f"{phrasing}: {found}/{total} package · {visible}/{total} top {TOP_N}")
        for path in skipped:
            print(f"skipped (not on disk): {path}")

    # Non-zero exit on the two things that are never acceptable.
    return 1 if (sum(r["leaks"] for r in results) or any(r["deterministic"] is False for r in results)) else 0


if __name__ == "__main__":
    sys.exit(main())
