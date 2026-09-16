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
import subprocess
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


_REQUIRED_KEY_FIELDS = ("repo", "task", "files")


class AnswerKeyError(ValueError):
    """The answer-key file itself is malformed -- a schema violation or a
    duplicate (repo, task) entry, as opposed to a corpus/answer-key
    mismatch (a configured task with no matching key at all), which is
    checked separately in main() under --strict.
    """


def load_answer_keys(path: Path = DEFAULT_ANSWER_KEYS) -> dict[tuple[str, str], dict]:
    """Index canonical benchmark tasks without duplicating their truth sets.

    Raises AnswerKeyError on a schema violation (a missing required field,
    or a non-dict `files` mapping) or a duplicate (repo, task) entry --
    silently keeping "whichever came last" for a duplicate would let a
    second, unreviewed key quietly replace the first with no indication
    anything was wrong.
    """
    data = json.loads(path.read_text())
    keys: dict[tuple[str, str], dict] = {}
    for index, entry in enumerate(data.get("keys", [])):
        missing = [field for field in _REQUIRED_KEY_FIELDS if field not in entry]
        if missing:
            raise AnswerKeyError(f"answer key #{index} is missing field(s): {', '.join(missing)}")
        if not isinstance(entry["files"], dict) or not entry["files"]:
            raise AnswerKeyError(
                f"answer key #{index} ({entry['repo']!r}, {entry['task']!r}): "
                "'files' must be a non-empty mapping of path -> reason"
            )
        anchors = entry.get("anchors", {})
        if not isinstance(anchors, dict):
            raise AnswerKeyError(
                f"answer key #{index} ({entry['repo']!r}, {entry['task']!r}): "
                "'anchors' must be a mapping of path -> list of substrings"
            )
        for anchor_path, substrings in anchors.items():
            if anchor_path not in entry["files"]:
                raise AnswerKeyError(
                    f"answer key #{index} ({entry['repo']!r}, {entry['task']!r}): "
                    f"anchors references {anchor_path!r}, which is not in 'files'"
                )
            if not isinstance(substrings, list) or not substrings or not all(
                isinstance(s, str) and s for s in substrings
            ):
                raise AnswerKeyError(
                    f"answer key #{index} ({entry['repo']!r}, {entry['task']!r}): "
                    f"anchors[{anchor_path!r}] must be a non-empty list of non-empty strings"
                )
        dedup_key = (entry["repo"], entry["task"])
        if dedup_key in keys:
            raise AnswerKeyError(f"duplicate answer key for (repo={dedup_key[0]!r}, task={dedup_key[1]!r})")
        keys[dedup_key] = entry
    return keys


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

    # --- excerpt-hit recall -------------------------------------------
    #
    # File recall (found_paths above) only asks whether a path appears
    # anywhere in the package -- it says nothing about whether the
    # *excerpt* delivered for that path contains the behavior the task is
    # about. Observed for real on fastapi: the benchmark awarded 4/4 file
    # recall while routing.py's only excerpt was an unrelated pragma-
    # no-cover stub and dependencies/utils.py's excerpts omitted the
    # override-lookup logic entirely -- an agent given `--json` would get
    # the right filenames and the wrong code. `anchors` (optional, per
    # answer-key file) names substrings that must appear in the delivered
    # excerpt text for that specific finding to count here; a file with
    # no `anchors` entry is unaffected; a file that's on-topic
    # (recovered by file recall) but whose anchor never appears in an
    # excerpt is a "wrong content" miss, distinct from "wrong/missing
    # file" and reported separately rather than folded into file recall.
    excerpt_index = {item.path: item for item in package.included}
    anchors = answer_key.get("anchors", {}) if answer_key else {}
    anchor_paths = [path for path in expected_paths if path in anchors]

    def _excerpt_hit(path: str) -> bool:
        item = excerpt_index.get(path)
        if item is None:
            return False
        text = "\n".join(excerpt.text for excerpt in item.excerpts)
        return any(anchor in text for anchor in anchors[path])

    excerpt_hit_paths = [path for path in anchor_paths if path in found_paths and _excerpt_hit(path)]

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
        "answer_key_group": answer_key.get("group") if answer_key else None,
        "answer_key_total": len(expected_paths),
        "answer_key_found": len(found_paths),
        "answer_key_visible_found": len(visible_found_paths),
        "answer_key_missing": [path for path in expected_paths if path not in included_paths],
        "answer_key_excerpt_total": len(anchor_paths),
        "answer_key_excerpt_found": len(excerpt_hit_paths),
        "answer_key_excerpt_missing": [path for path in anchor_paths if path not in excerpt_hit_paths],
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
    if result["answer_key_excerpt_total"]:
        print(
            f"  excerpt-hit recall: {result['answer_key_excerpt_found']}/{result['answer_key_excerpt_total']} "
            "(file recovered AND its delivered excerpt contains the anchored behavior)"
        )
        for path in result["answer_key_excerpt_missing"]:
            print(f"    ! {path}: file present but excerpt lacks the anchored behavior")

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


def check_commit(root: Path, expected_commit: str) -> str | None:
    """Return an error string if `root`'s checked-out HEAD does not match
    `expected_commit`, or if HEAD cannot be determined; None if it matches.

    Recall and timing figures are only comparable run-to-run if every repo
    is actually at the commit the corpus config claims -- an unpinned
    "whatever HEAD happens to be" silently invalidates every number
    downstream of it (files rename, tasks stop matching what they used
    to). `commit` on a corpus entry is documentation until something
    checks it; this makes it enforced instead.
    """
    try:
        actual = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"{root}: could not determine HEAD commit ({exc})"
    if actual != expected_commit:
        return f"{root}: HEAD is {actual}, corpus config pins {expected_commit}"
    return None


def validate_answer_key_files(root: Path, answer_key: dict) -> list[str]:
    """Return an error string per file in `answer_key["files"]` that does
    not exist under `root` -- a key referencing a file that was since
    renamed or removed is silently useless (it can never be "found"), and
    a passing recall score built on top of it is not evidence of anything.
    """
    errors = []
    for rel_path in answer_key["files"]:
        if not (root / rel_path).is_file():
            errors.append(
                f"{root}: answer key for {answer_key['task']!r} references "
                f"missing file {rel_path!r}"
            )
    return errors


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
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "validate the run instead of merely reporting it: every configured repo "
            "must be on disk, every configured task must have exactly one answer key, "
            "every checkout's HEAD must match the pinned commit, and every answer-key "
            "file must exist. Use for CI/regression runs against a known corpus, not "
            "casual exploration against whatever repos happen to be cloned."
        ),
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=None,
        metavar="FRACTION",
        help="fail if combined package recall (found/total, across scored cases) falls below this",
    )
    parser.add_argument(
        "--min-top8-recall",
        type=float,
        default=None,
        metavar="FRACTION",
        help="fail if combined top-%d recall falls below this" % TOP_N,
    )
    parser.add_argument(
        "--min-excerpt-recall",
        type=float,
        default=None,
        metavar="FRACTION",
        help=(
            "fail if combined excerpt-hit recall falls below this -- only scored where an "
            "answer key declares 'anchors'; see answer-keys.json"
        ),
    )
    args = parser.parse_args(argv)

    try:
        config = json.loads(Path(args.config).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read corpus config {args.config!r}: {exc}", file=sys.stderr)
        return 2

    try:
        answer_keys = load_answer_keys(Path(args.answer_keys))
    except (OSError, json.JSONDecodeError, KeyError, AnswerKeyError) as exc:
        print(f"error: cannot read answer keys {args.answer_keys!r}: {exc}", file=sys.stderr)
        return 2

    # --- validation (collected, not raised immediately, so a --strict run
    # reports every problem in one pass rather than one-at-a-time) --------
    validation_errors: list[str] = []

    results = []
    skipped = []
    for entry in config.get("repos", []):
        root = Path(entry["root"]).expanduser()
        repo_name = entry.get("repo") or entry.get("github", "").rsplit("/", 1)[-1] or root.name

        if args.strict:
            for task in entry["tasks"]:
                if (repo_name, task) not in answer_keys:
                    validation_errors.append(
                        f"{repo_name!r}: task {task!r} has no matching entry in {args.answer_keys}"
                    )

        if not root.is_dir():
            # A corpus is personal to whoever runs it; a missing clone is
            # normal for casual dogfooding, so the default (non-strict)
            # run just says so rather than failing. --strict is for
            # validating a specific, supposedly-complete corpus (CI,
            # regression tracking) -- there, a missing repo means the run
            # silently covered less than it claims to, which is exactly
            # the "reports success without validating anything" failure
            # mode this flag exists to close.
            skipped.append(str(root))
            if args.strict:
                validation_errors.append(f"{root}: configured repo is not on disk")
            continue

        if args.strict and "commit" in entry:
            commit_error = check_commit(root, entry["commit"])
            if commit_error:
                validation_errors.append(commit_error)

        for task, phrasing, answer_key in iter_task_phrasings(
            repo_name, entry["tasks"], answer_keys, args.phrasing
        ):
            if args.strict and answer_key is not None:
                validation_errors.extend(validate_answer_key_files(root, answer_key))
            results.append(
                run_case(
                    str(root),
                    task,
                    check_determinism=not args.no_determinism_check,
                    phrasing=phrasing,
                    answer_key=answer_key,
                )
            )

    # A run that validates nothing must never report success, --strict or
    # not: this is the "0 case(s) ... exit=0" failure mode observed for
    # real when every configured repo happened to be missing.
    if not results:
        validation_errors.append("0 cases ran -- nothing was validated")

    recall_totals: dict[str, tuple[int, int, int]] = {}
    for phrasing in ("existing", "technical", "natural"):
        phrased = [r for r in results if r["phrasing"] == phrasing and r["answer_key_total"]]
        if not phrased:
            continue
        found = sum(r["answer_key_found"] for r in phrased)
        visible = sum(r["answer_key_visible_found"] for r in phrased)
        total = sum(r["answer_key_total"] for r in phrased)
        recall_totals[phrasing] = (found, visible, total)

    # --- per-cohort breakdown ---------------------------------------------
    #
    # A single combined recall number across every answer-key cohort (the
    # README's old "22/29 held out" figure) invites reading it as one
    # thing: evidence about unseen repositories. It is not -- some cohorts
    # informed ranking changes after their keys were written (see
    # answer-keys.json's own "spent as evidence" note), others did not,
    # and folding them together erases that distinction. Reporting each
    # `group` (from the answer key) on its own line is what lets a reader
    # -- and the README, which should quote this output rather than a
    # hand-maintained table -- tell them apart.
    group_totals: dict[str, tuple[int, int, int]] = {}
    for group in sorted({r["answer_key_group"] for r in results if r["answer_key_group"]}):
        grouped = [r for r in results if r["answer_key_group"] == group and r["answer_key_total"]]
        found = sum(r["answer_key_found"] for r in grouped)
        visible = sum(r["answer_key_visible_found"] for r in grouped)
        total = sum(r["answer_key_total"] for r in grouped)
        group_totals[group] = (found, visible, total)

    combined_total = sum(total for _f, _v, total in recall_totals.values())
    combined_found = sum(found for found, _v, _t in recall_totals.values())
    combined_visible = sum(visible for _f, visible, _t in recall_totals.values())
    combined_excerpt_total = sum(r["answer_key_excerpt_total"] for r in results)
    combined_excerpt_found = sum(r["answer_key_excerpt_found"] for r in results)

    if args.min_recall is not None:
        if combined_total == 0:
            validation_errors.append("--min-recall given but no case had an answer key to score")
        elif combined_found / combined_total < args.min_recall:
            validation_errors.append(
                f"package recall {combined_found}/{combined_total} "
                f"({combined_found / combined_total:.1%}) is below --min-recall {args.min_recall:.1%}"
            )
    if args.min_top8_recall is not None:
        if combined_total == 0:
            validation_errors.append("--min-top8-recall given but no case had an answer key to score")
        elif combined_visible / combined_total < args.min_top8_recall:
            validation_errors.append(
                f"top-{TOP_N} recall {combined_visible}/{combined_total} "
                f"({combined_visible / combined_total:.1%}) is below --min-top8-recall {args.min_top8_recall:.1%}"
            )
    if args.min_excerpt_recall is not None:
        if combined_excerpt_total == 0:
            validation_errors.append("--min-excerpt-recall given but no case had an anchored answer key to score")
        elif combined_excerpt_found / combined_excerpt_total < args.min_excerpt_recall:
            validation_errors.append(
                f"excerpt-hit recall {combined_excerpt_found}/{combined_excerpt_total} "
                f"({combined_excerpt_found / combined_excerpt_total:.1%}) is below "
                f"--min-excerpt-recall {args.min_excerpt_recall:.1%}"
            )

    if args.json:
        print(
            json.dumps(
                {
                    "results": results,
                    "recall_by_phrasing": {
                        phrasing: {"found": f, "top8": v, "total": t}
                        for phrasing, (f, v, t) in recall_totals.items()
                    },
                    "recall_by_group": {
                        group: {"found": f, "top8": v, "total": t}
                        for group, (f, v, t) in group_totals.items()
                    },
                    "excerpt_hit_recall": {
                        "found": combined_excerpt_found,
                        "total": combined_excerpt_total,
                    },
                    "validation_errors": validation_errors,
                },
                indent=2,
            )
        )
    else:
        for result in results:
            print_case(result)
        print(f"\n{'=' * 78}")
        leaks = sum(r["leaks"] for r in results)
        nondet = [r for r in results if r["deterministic"] is False]
        print(f"{len(results)} case(s) · {leaks} secret-shaped string(s) · {len(nondet)} non-deterministic")
        for phrasing, (found, visible, total) in recall_totals.items():
            print(f"{phrasing}: {found}/{total} package · {visible}/{total} top {TOP_N}")
        # Reported separately, never combined into one number: cohorts
        # have different histories (see the module-level note above
        # group_totals) and quoting them together implies a comparability
        # that isn't there.
        for group, (found, visible, total) in group_totals.items():
            print(f"  cohort {group}: {found}/{total} package · {visible}/{total} top {TOP_N}")
        if combined_excerpt_total:
            print(f"excerpt-hit recall: {combined_excerpt_found}/{combined_excerpt_total} (anchored files only)")
        for path in skipped:
            print(f"skipped (not on disk): {path}")
        for error in validation_errors:
            print(f"VALIDATION ERROR: {error}", file=sys.stderr)

    # Non-zero exit on the two things that are never acceptable, on any
    # collected validation error (missing repo/commit/key/file under
    # --strict, or zero cases regardless of --strict), or on a
    # configured recall threshold not being met.
    return (
        1
        if (
            sum(r["leaks"] for r in results)
            or any(r["deterministic"] is False for r in results)
            or validation_errors
        )
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
