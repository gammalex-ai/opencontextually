"""Regression test for the parse-once caching contract (perf).

get_context() used to independently open, read, and ast.parse/ast.walk
every discovered Python file up to four times -- once each for scoring
(selector._analyze), the import graph (selector._build_import_graph),
test-reference stems (checks._test_reference_stems), and excerpt
extraction (selector.attach_excerpts). filecache.RunCache fixes this by
memoizing each file's content and derived AST facts for the duration of a
single get_context() call.

This test guards that contract directly: it counts real ast.parse() calls
across a full get_context() run and asserts each non-empty .py file is
parsed at most once, so a future change that reintroduces per-stage
re-parsing (even accidentally, e.g. a new call site that bypasses the
cache) fails loudly here instead of only showing up as a slow real-repo
run.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

from opencontextually import get_context

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "examples" / "auth_bug"


def _non_empty_py_file_count(root: Path) -> int:
    count = 0
    for path in root.rglob("*.py"):
        if path.stat().st_size > 0:
            count += 1
    return count


def test_each_python_file_is_parsed_at_most_once_per_run():
    expected_parseable = _non_empty_py_file_count(FIXTURE_ROOT)
    assert expected_parseable > 0, "fixture should contain non-empty .py files"

    with patch("opencontextually.filecache.ast.parse", wraps=ast.parse) as mocked:
        package = get_context("fix the authentication bug", root=FIXTURE_ROOT)

    # Sanity: the run actually did something (proves the patched cache path
    # was exercised, not just a no-op selection).
    assert package.included

    # The whole point of RunCache: at most one ast.parse() per non-empty
    # .py file for the entire run, even though scoring, the import graph,
    # test-reference stems, and excerpt extraction all need parsed facts
    # from overlapping sets of files.
    assert mocked.call_count <= expected_parseable
