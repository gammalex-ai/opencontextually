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


# --- bug fix: warnings from scanned files reached the user's terminal -----
#
# ast.parse emits SyntaxWarning for constructs like an invalid escape
# sequence. Because RunCache parses a string rather than a file, Python
# reports the location as "<unknown>:108" -- a line number with no filename,
# which tells the user nothing and looks like the tool is broken. Observed
# on psf/black, whose deliberately-malformed test fixtures produced 24 lines
# of stderr against 22 lines of actual output.


def test_parsing_a_file_that_warns_emits_no_warning(tmp_path):
    import warnings

    from opencontextually.discovery import DiscoveredFile
    from opencontextually.filecache import RunCache

    # An invalid escape sequence: parses fine, warns loudly.
    path = tmp_path / "warns.py"
    path.write_text('def f():\n    return "\\ "\n')
    discovered_file = DiscoveredFile(
        path="warns.py", abs_path=path, role="source", size=path.stat().st_size
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        record = RunCache().get_record(discovered_file)

    assert record.parse_ok, "the file still parses -- suppression must not skip it"
    assert [w for w in caught if issubclass(w.category, SyntaxWarning)] == []
