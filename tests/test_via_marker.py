"""Unit tests for the "via" marker fix in context.py's render().

The compact line used to reuse an item's full provenance -- the entire
ordered edge-path list -- as the "via" marker, duplicating `reason` and
getting cut off mid-path in a normal terminal. The fix names the
neighboring file once, concisely, by its own basename.
"""

from __future__ import annotations

import shutil

from opencontextually.context import ContextItem, ContextPackage, Excerpt


def _render_at_width(package: ContextPackage, columns: int) -> str:
    original = shutil.get_terminal_size
    shutil.get_terminal_size = lambda fallback=(80, 24): shutil.os.terminal_size((columns, 24))
    try:
        return package.render()
    finally:
        shutil.get_terminal_size = original


def test_via_marker_names_the_other_file_once():
    item = ContextItem(
        path="dali/scoring/verification.py",
        role="source",
        reason="imported by run_synthetic.py",
        score=5.0,
        provenance=["dali/runners/run_synthetic.py imports dali/scoring/verification.py"],
    )
    package = ContextPackage(task="citation verification returns wrong confidence score", included=[item])

    rendered = package.render()

    assert "via run_synthetic.py" in rendered
    # The other file's basename appears exactly once as the via marker --
    # not duplicated by also spelling out the full provenance sentence.
    assert "dali/runners/run_synthetic.py imports" not in rendered


def test_via_marker_renders_without_truncation_at_80_columns():
    # Reason deliberately does NOT name the neighbor (unlike the
    # "imported by X.py" reasons expand_transitively() generates) so the
    # via marker is the only place the neighbor's name appears -- this is
    # the shape that used to get cut off mid-path by _truncate() when the
    # whole provenance edge sentence (importer path + " imports " +
    # importee path) was reused verbatim as the via text instead of a
    # concise basename.
    item = ContextItem(
        path="src/scoring/verification.py",
        role="source",
        reason="matches task terms",
        score=5.0,
        provenance=["dali/runners/run_synthetic.py imports src/scoring/verification.py"],
    )
    package = ContextPackage(task="citation verification returns wrong confidence score", included=[item])

    rendered = _render_at_width(package, 80)

    line = next(line for line in rendered.splitlines() if "verification.py" in line and "  " in line)
    assert len(line) <= 80
    assert "via run_synthetic.py" in line
    assert "..." not in line
    # The full edge sentence -- the thing that used to overflow -- must
    # not appear anywhere in the rendered line.
    assert "dali/runners/run_synthetic.py imports" not in line


def test_item_with_direct_match_and_no_provenance_has_no_via_marker():
    item = ContextItem(
        path="src/auth/middleware.py",
        role="source",
        reason="filename matches 'middleware'",
        score=10.0,
    )
    package = ContextPackage(task="fix the authentication bug", included=[item])

    rendered = package.render()
    assert "via" not in rendered


def test_to_dict_provenance_is_unaffected_by_render_shortening():
    item = ContextItem(
        path="dali/scoring/verification.py",
        role="source",
        reason="imported by run_synthetic.py",
        score=5.0,
        provenance=["dali/runners/run_synthetic.py imports dali/scoring/verification.py"],
        excerpts=[Excerpt(start_line=1, end_line=2, text="def verify():\n    pass")],
    )
    package = ContextPackage(task="citation verification returns wrong confidence score", included=[item])

    as_dict = package.to_dict()
    assert as_dict["included"][0]["provenance"] == [
        "dali/runners/run_synthetic.py imports dali/scoring/verification.py"
    ]
