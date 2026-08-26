"""OpenContextually: the layer between a project and an AI coding agent.

The hero API is `get_context(task, root=".")`. It selects the local files
relevant to `task`, bounds itself to `root`, and returns a ContextPackage
that explains every inclusion. It never executes the task and never calls
out to an LLM or network.
"""

from __future__ import annotations

from pathlib import Path

from .context import ContextPackage
from .discovery import discover
from .selector import attach_excerpts, select

__version__ = "0.1.0.dev0"

__all__ = ["get_context", "ContextPackage", "__version__"]


def get_context(task: str, root: str | Path = ".") -> ContextPackage:
    """Select the local files relevant to `task` within `root`.

    Returns a ContextPackage: the included files (each with a reason and
    score), and a count of everything excluded, bucketed by reason.
    """
    root_path = Path(root).resolve()

    discovered, excluded_by_reason = discover(root_path)
    items, extra_exclusions = select(discovered, task)
    excerpts_dropped_over_budget = attach_excerpts(items, discovered, task)

    # An item whose excerpts were all evicted by the package-wide budget
    # is no longer a usable inclusion -- it has a reason but nothing to
    # back it -- so it moves from `included` to the "over_budget"
    # exclusion bucket instead of surviving as a reason-only entry.
    fully_evicted = [item for item in items if not item.excerpts]
    items = [item for item in items if item.excerpts]

    excluded_by_reason = dict(excluded_by_reason)
    excluded_by_reason.update(extra_exclusions)
    excluded_by_reason["over_budget"] = excluded_by_reason.get("over_budget", 0) + len(fully_evicted)
    # Every run reports the full bucket set, even when a bucket is zero,
    # so the exclusion summary is always the same six keys.
    for key in ("ignored", "binary", "oversize", "below_threshold", "over_cap", "over_budget"):
        excluded_by_reason.setdefault(key, 0)
    excluded_count = sum(excluded_by_reason.values())

    return ContextPackage(
        task=task,
        included=items,
        conflicts=[],
        missing=[],
        excluded_count=excluded_count,
        excluded_by_reason=excluded_by_reason,
        trace={
            "rules_run": ["lexical_selection", "transitive_import_expansion"],
            "excerpts_dropped_over_budget": excerpts_dropped_over_budget,
        },
    )
