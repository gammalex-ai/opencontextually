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
from .selector import select

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

    excluded_by_reason = dict(excluded_by_reason)
    excluded_by_reason.update(extra_exclusions)
    excluded_count = sum(excluded_by_reason.values())

    return ContextPackage(
        task=task,
        included=items,
        conflicts=[],
        missing=[],
        excluded_count=excluded_count,
        excluded_by_reason=excluded_by_reason,
        trace={"rules_run": ["lexical_selection"]},
    )
