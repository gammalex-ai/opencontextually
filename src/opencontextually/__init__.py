"""OpenContextually: the layer between a project and an AI coding agent.

The hero API is `get_context(task, root=".")`. It selects the local files
relevant to `task`, bounds itself to `root`, and returns a ContextPackage
that explains every inclusion. It never executes the task and never calls
out to an LLM or network.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .checks import find_configuration_discrepancies, find_test_reference_gaps
from .context import ContextPackage
from .discovery import discover
from .filecache import RunCache
from .selector import attach_excerpts, compute_filename_word_counts, detect_weak_signal, select

try:
    __version__ = version("opencontextually")
except PackageNotFoundError:
    # Running from an uninstalled source checkout (e.g. `python -m` against
    # a plain git clone with no editable install) -- there's no package
    # metadata to read, so there's no real version to report.
    __version__ = "0.0.0.dev0"

__all__ = ["get_context", "ContextPackage", "__version__"]


def get_context(task: str, root: str | Path = ".") -> ContextPackage:
    """Select the local files relevant to `task` within `root`.

    Returns a ContextPackage: the included files (each with a reason and
    score), and a count of everything excluded, bucketed by reason.
    """
    root_path = Path(root).resolve()

    # One cache per call, threaded through every stage below, so a file is
    # read and ast.parse/ast.walk'd at most once for this run -- see
    # filecache.RunCache. Built fresh here (never module/global state):
    # get_context is the entry point a long-lived process (e.g. the MCP
    # server) calls repeatedly, and each call must see the current
    # filesystem and release its cache afterward rather than accumulating
    # memory or serving stale facts across calls.
    cache = RunCache()

    discovered, excluded_by_reason = discover(root_path)
    items, extra_exclusions, selection_stats = select(discovered, task, cache)
    evicted_item_paths: set[str] = set()
    excerpts_dropped_over_budget = attach_excerpts(
        items, discovered, task, cache, evicted_item_paths
    )

    # An item whose excerpts were all evicted by the package-wide budget
    # is no longer a usable inclusion -- it has a reason but nothing to
    # back it -- so it moves from `included` to the "over_budget"
    # exclusion bucket instead of surviving as a reason-only entry. This
    # happens *before* CHECK runs below, so both rules see the same
    # "included" set that render()/to_dict() will actually report --
    # otherwise a fully-evicted selection could still produce findings
    # underneath a "No relevant context found" message.
    # Distinguish *why* an item has no excerpts. Folding both causes into
    # "over_budget" made the footer state "ran out of room in the excerpt
    # budget" on a run using 4% of it -- a confident falsehood about the
    # tool's own behaviour. Only genuine eviction is a budget problem;
    # a file the extractor found nothing quotable in is a different fact.
    fully_evicted = [item for item in items if not item.excerpts]
    evicted_paths = evicted_item_paths if evicted_item_paths is not None else set()
    budget_evicted = [i for i in fully_evicted if i.path in evicted_paths]
    no_excerptable = [i for i in fully_evicted if i.path not in evicted_paths]
    items = [item for item in items if item.excerpts]

    # WEAK SIGNAL: something cleared SCORE_THRESHOLD, but for a multi-term
    # task, no single included file corroborated more than one term and
    # the term(s) that did match are a repo-wide naming convention or a
    # thin, incidental mention -- see selector.detect_weak_signal() for
    # the full rationale. Computed after the eviction above (not from
    # select()'s raw `items`) so a seed whose excerpts were entirely
    # dropped for budget reasons does not count as corroboration it never
    # actually delivers to the user. Never suppresses `items`; only adds a
    # warning ahead of them in render()/to_dict().
    weak_signal = detect_weak_signal(
        selection_stats["terms"],
        items,
        selection_stats["term_file_counts"],
        compute_filename_word_counts(discovered),
        selection_stats["total_files"],
    )

    # --- CHECK ------------------------------------------------------------
    #
    # Both rules are lexical, high-precision/low-recall -- see checks.py --
    # and both are scoped to what SELECT actually chose for this task.
    # `configuration_discrepancy` would in principle be a repo-wide
    # structural fact, but OpenContextually's contract is context *for a
    # task*: volunteering a repo-wide finding underneath a "No relevant
    # context found for this task" message reads as self-contradictory
    # (see tests/test_no_relevant_context.py). `test_reference_gap` is
    # task-scoped by construction -- "no test references X" is only
    # meaningful relative to what this task pulled in.
    #
    # --- bug fix: the trace claimed checks that never ran ----------------
    # `rules_run` used to be a hardcoded four-element list built at the
    # bottom of this function, so an empty selection -- which skips
    # `configuration_discrepancy` entirely via the `if items` guard, and
    # bails out of `find_test_reference_gaps` at its own first line --
    # still reported "Checks run: configuration_discrepancy,
    # test_reference_gap". That is a confident falsehood about the tool's
    # own behaviour, and exactly the kind a user cannot check. It also
    # matters beyond cosmetics: `trace` is what a machine consumer reads
    # to know what was actually evaluated.
    #
    # `rules_run` is now accumulated as each rule is genuinely invoked, so
    # the footer and the JSON both report what really ran. An empty
    # selection now correctly renders "Checks run: selection only".
    rules_run = ["lexical_selection", "transitive_import_expansion"]
    conflicts: list[dict] = []
    missing: list[dict] = []

    if items:
        conflicts = find_configuration_discrepancies(
            discovered,
            cache,
            included_paths={item.path for item in items},
            task_terms=selection_stats["terms"],
        )
        rules_run.append("configuration_discrepancy")

        missing = find_test_reference_gaps(
            items, discovered, task, conflicts=conflicts, cache=cache
        )
        rules_run.append("test_reference_gap")

    excluded_by_reason = dict(excluded_by_reason)
    excluded_by_reason.update(extra_exclusions)
    excluded_by_reason["over_budget"] = excluded_by_reason.get("over_budget", 0) + len(budget_evicted)
    excluded_by_reason["no_excerpt"] = excluded_by_reason.get("no_excerpt", 0) + len(no_excerptable)
    # Every run reports the full bucket set, even when a bucket is zero,
    # so the exclusion summary is always the same six keys.
    for key in (
        "ignored", "binary", "oversize", "duplicate", "below_threshold", "over_cap", "over_budget",
        "no_excerpt",
    ):
        excluded_by_reason.setdefault(key, 0)
    excluded_count = sum(excluded_by_reason.values())

    return ContextPackage(
        task=task,
        included=items,
        conflicts=conflicts,
        missing=missing,
        excluded_count=excluded_count,
        excluded_by_reason=excluded_by_reason,
        weak_signal=weak_signal,
        trace={
            "rules_run": rules_run,
            "excerpts_dropped_over_budget": excerpts_dropped_over_budget,
        },
    )
