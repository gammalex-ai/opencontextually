"""Tests for render()'s compact/-v/--all presentation modes.

The default plain-text output is a compact, scannable list (no code
excerpts) because a human reading it can already open the file; -v opts
into bounded excerpts, --all opts into showing every included item instead
of the top DEFAULT_SHOWN slice. Neither flag touches to_dict() -- that stays
full-fidelity always (see test_cli_json.py for the --json invariance
check). Findings (configuration_discrepancy / test_reference_gap) always
render as one line each, regardless of verbose/show_all, because they are
findings, not files subject to the top-slice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opencontextually import get_context
from opencontextually.context import DEFAULT_SHOWN, ContextItem, ContextPackage, Excerpt

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"
TASK = "fix the authentication bug"


def _get_package():
    return get_context(TASK, root=FIXTURE_ROOT)


# --- compact default -------------------------------------------------------


def test_default_render_has_no_code_excerpts():
    package = _get_package()
    rendered = package.render()
    # Every included item carries at least one excerpt (asserted in
    # test_auth_bug_example.py); none of that source text should leak into
    # the compact default.
    for item in package.included:
        for excerpt in item.excerpts:
            for text_line in excerpt.text.splitlines():
                stripped = text_line.strip()
                if len(stripped) > 3:
                    assert stripped not in rendered


def test_default_render_shows_one_line_per_included_file():
    package = _get_package()
    rendered = package.render()
    for item in package.included:
        assert item.path in rendered
        assert item.reason in rendered


def test_default_render_is_compact():
    # The whole point: a real task against a real (if small) fixture should
    # not produce a wall of text.
    package = _get_package()
    rendered = package.render()
    assert len(rendered.splitlines()) < 30


def test_default_render_shows_findings_as_one_liners():
    package = _get_package()
    rendered = package.render()
    assert package.conflicts, "fixture is expected to carry a real discrepancy"
    assert package.missing, "fixture is expected to carry a real reference gap"
    for conflict in package.conflicts:
        assert conflict["message"] in rendered
    for entry in package.missing:
        message = entry["message"]
        capitalized = message[:1].upper() + message[1:]
        assert capitalized in rendered


# --- -v (verbose) -----------------------------------------------------------


def test_verbose_render_includes_excerpt_text():
    package = _get_package()
    rendered = package.render(verbose=True)
    item = next(i for i in package.included if i.path == "src/auth/middleware.py")
    assert item.excerpts
    first_line_of_first_excerpt = item.excerpts[0].text.splitlines()[0]
    assert first_line_of_first_excerpt in rendered


def test_verbose_render_caps_excerpt_lines_per_file():
    # A file with a long excerpt should not dump the whole thing -- -v is
    # a tighter, presentation-only cap, independent of what selector.py
    # already extracted (which stays intact in to_dict()).
    item = ContextItem(
        path="src/big.py",
        role="source",
        reason="defines Big",
        score=1.0,
        excerpts=[
            Excerpt(
                start_line=1,
                end_line=40,
                text="\n".join(f"line {n}" for n in range(40)),
            )
        ],
    )
    package = ContextPackage(task="do something with big", included=[item])
    rendered = package.render(verbose=True)
    # Only the lines belonging to this item's excerpt block.
    block = rendered.split("src/big.py", 1)[1]
    excerpt_lines = [ln for ln in block.splitlines() if ln.strip().startswith("line ")]
    assert len(excerpt_lines) < 40

    # to_dict() must be untouched by the display cap.
    as_dict = package.to_dict()
    assert as_dict["included"][0]["excerpts"][0]["end_line"] == 40


def test_verbose_prefers_excerpt_matching_task_terms():
    # Given two candidate excerpts, the one containing the task's own terms
    # should be shown ahead of an unrelated one, even if it isn't first in
    # item.excerpts.
    item = ContextItem(
        path="src/thing.py",
        role="source",
        reason="matches thing",
        score=1.0,
        excerpts=[
            Excerpt(start_line=1, end_line=1, text="def unrelated_diagnostic(): pass"),
            Excerpt(start_line=10, end_line=10, text="def confidence_score(): return 0.0"),
        ],
    )
    package = ContextPackage(task="wrong confidence score", included=[item])
    rendered = package.render(verbose=True)
    first_excerpt_pos = rendered.find("unrelated_diagnostic")
    second_excerpt_pos = rendered.find("confidence_score")
    assert second_excerpt_pos != -1
    assert first_excerpt_pos == -1 or second_excerpt_pos < first_excerpt_pos


# --- --all -------------------------------------------------------------------


def test_show_all_lists_every_included_item():
    many_items = [
        ContextItem(path=f"src/f{n}.py", role="source", reason="matches thing", score=1.0)
        for n in range(DEFAULT_SHOWN + 5)
    ]
    package = ContextPackage(task="thing", included=many_items)

    compact = package.render()
    for item in many_items[DEFAULT_SHOWN:]:
        assert item.path not in compact
    assert "more" in compact
    assert "--all to list" in compact

    full = package.render(show_all=True)
    for item in many_items:
        assert item.path in full
    assert "--all to list" not in full


def test_show_all_composes_with_verbose():
    many_items = [
        ContextItem(
            path=f"src/f{n}.py",
            role="source",
            reason="matches thing",
            score=1.0,
            excerpts=[Excerpt(start_line=1, end_line=1, text=f"marker_{n}")],
        )
        for n in range(DEFAULT_SHOWN + 2)
    ]
    package = ContextPackage(task="thing", included=many_items)
    rendered = package.render(verbose=True, show_all=True)
    for item in many_items:
        assert item.path in rendered
    assert "marker_0" in rendered
    assert f"marker_{DEFAULT_SHOWN + 1}" in rendered


# --- --json stays untouched by verbose/show_all -----------------------------


def test_to_dict_ignores_verbose_and_show_all():
    package = _get_package()
    baseline = package.to_dict()
    package.render(verbose=True, show_all=True)
    assert package.to_dict() == baseline
