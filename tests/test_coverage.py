"""Unit tests for the distinct-term-coverage fix in selector.py.

A file that matches several distinct task terms should clearly outrank one
that repeats a single incidental term, on tasks with more than one term. A
single-term task must be entirely unaffected (coverage_ratio is trivially
1.0 whenever there is only one term to cover), and files reached only
transitively (via the import graph, with no lexical match of their own)
must not be penalized at all -- they never go through _analyze/score_file
in the first place.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually.context import ContextItem
from opencontextually.discovery import discover
from opencontextually.selector import SCORE_THRESHOLD, expand_transitively, score_file


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _discovered_file(tmp_path: Path, rel_path: str):
    files, _reasons = discover(tmp_path)
    by_path = {f.path: f for f in files}
    return by_path[rel_path]


def test_multi_term_coverage_outranks_single_term_repetition(tmp_path):
    # navigation.py matches all four task terms once each; repeater.py
    # matches only "mobile", repeated many times -- the exact shape of the
    # observed defect (a CSS-breakpoint-style repetition of one incidental
    # term). navigation.py must score higher, and repeater.py should not
    # even clear SCORE_THRESHOLD.
    task = "navigation dropdown is broken on mobile"
    _write(
        tmp_path,
        "navigation.py",
        "# navigation dropdown mobile handling\n"
        "def navigation_dropdown_mobile():\n"
        "    pass\n",
    )
    _write(
        tmp_path,
        "repeater.py",
        "\n".join(f"# mobile breakpoint value {i}" for i in range(30)),
    )

    terms = ["navigation", "dropdown", "broken", "mobile"]

    nav_file = _discovered_file(tmp_path, "navigation.py")
    rep_file = _discovered_file(tmp_path, "repeater.py")

    nav_score = score_file(nav_file, terms)
    rep_score = score_file(rep_file, terms)

    assert nav_score > rep_score
    assert rep_score <= SCORE_THRESHOLD


def test_single_term_task_is_unaffected_by_coverage(tmp_path):
    # A one-word task has only one term -- coverage_ratio is 1/1 == 1.0 for
    # any file that matches at all, so the coverage factor must be exactly
    # 1.0 and behave identically to the pre-fix scoring for such tasks.
    _write(
        tmp_path,
        "applications.py",
        "\n".join(f"# applications mentioned {i}" for i in range(10)),
    )
    discovered_file = _discovered_file(tmp_path, "applications.py")

    score_one_term = score_file(discovered_file, ["applications"])
    # Same content score computed by hand: coverage_ratio must be 1.0, so
    # the score must equal what a coverage_factor of 1.0 would produce --
    # i.e. it must be > SCORE_THRESHOLD, exactly as before this fix.
    assert score_one_term > SCORE_THRESHOLD


def test_transitively_reached_file_with_zero_lexical_match_survives(tmp_path):
    # existence.py has no lexical relationship to the task terms at all --
    # it is reached purely through the import graph from a seed. Coverage
    # is computed inside _analyze/score_file only; expand_transitively
    # never calls either, so a zero-lexical-match transitively-reached file
    # must be unaffected by this fix and must still appear with a
    # provenance edge path.
    _write(tmp_path, "scoring/__init__.py", "")
    _write(tmp_path, "scoring/citation.py", "from scoring.existence import check\n")
    _write(tmp_path, "scoring/existence.py", "def check():\n    return True\n")

    discovered, _reasons = discover(tmp_path)
    seed = ContextItem(
        path="scoring/citation.py", role="source", reason="seed", score=10.0
    )
    expanded_items, _over_cap = expand_transitively([seed], discovered)

    paths = {item.path for item in expanded_items}
    assert "scoring/existence.py" in paths

    existence_item = next(
        item for item in expanded_items if item.path == "scoring/existence.py"
    )
    assert existence_item.provenance
    assert any(
        "scoring/citation.py imports scoring/existence.py" in edge
        for edge in existence_item.provenance
    )
