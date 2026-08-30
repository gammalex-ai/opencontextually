"""Tests for the weak-signal state: SELECT found something that cleared
SCORE_THRESHOLD, but for a multi-term task no single included file
corroborated more than one individually-strong term, and the term(s) that
did match are either a repo-wide naming convention or backed by only a
handful of incidental mentions. See selector.detect_weak_signal().

This state must never suppress `included` -- only add a warning ahead of
it -- and must never fire for a single-term task, where "only one term
matched" is trivially true of every result.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually import get_context


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _make_page_convention_repo(root: Path) -> None:
    # A Next.js-style repo: "page" is a filename convention repeated across
    # many unrelated route directories, "landing"/"improve" are each just
    # an incidental one-off content mention (a comment, an id string) in a
    # couple of those files, and "conversion"/"rate" barely appear at all.
    # No single file combines two individually-distinctive terms.
    routes = [
        "company", "legal", "pricing", "about", "careers",
        "blog", "contact", "docs", "support", "partners",
    ]
    for i, route in enumerate(routes):
        # A couple of pages carry an incidental, unrelated mention of
        # "landing" or "improve" (an id string, a code comment) -- this is
        # exactly the coincidental-second-term case that must not be
        # mistaken for real corroboration.
        extra = ""
        if i == 0:
            extra = "// chapter id: landing\n"
        if i == 1:
            extra = "// TODO: improve this later\n"
        _write(
            root,
            f"app/{route}/page.tsx",
            f"{extra}export default function {route.title()}Page() {{ return null; }}\n",
        )
    # A couple more incidental "landing"/"improve" mentions elsewhere, and
    # a thin "conversion" mention, all well under the rare-content bar.
    _write(root, "lib/analytics.ts", "// landing metrics helper\nexport const x = 1;\n")
    _write(root, "lib/copy.ts", "// improve wording later\nexport const y = 1;\n")
    _write(root, "docs/notes.md", "# Notes\n\nWe should track conversion somewhere.\n")


def _make_navigation_repo(root: Path) -> None:
    # A genuinely good multi-term match: one file's filename and content
    # both carry multiple distinctive task terms together.
    _write(
        root,
        "lib/navigation.ts",
        "// navigation dropdown menu, fixes mobile rendering\n"
        "export function toggleDropdown() { return true; }\n",
    )
    _write(
        root,
        "components/Navbar.tsx",
        "// mobile navigation bar with a dropdown menu\n"
        "export function Navbar() { return null; }\n",
    )
    # Filler so the repo isn't trivially tiny.
    for name in ("Footer", "Header", "Sidebar"):
        _write(root, f"components/{name}.tsx", f"export function {name}() {{ return null; }}\n")


def test_weak_signal_fires_on_filename_convention_only_match(tmp_path):
    _make_page_convention_repo(tmp_path)
    package = get_context("improve landing page conversion rate", root=tmp_path)

    assert package.included, "results must still be shown, never suppressed"
    assert package.weak_signal is not None
    assert "page" in package.weak_signal["matched_terms"]
    # Every task term is reported, including ones that matched nothing.
    # "improve" is a generic task verb and is dropped as a stopword -- it
    # describes intent, not code, so it is not a task term at all.
    assert set(package.weak_signal["term_file_counts"]) == {
        "landing", "page", "conversion", "rate",
    }
    assert package.weak_signal["term_file_counts"]["rate"] == 0

    rendered = package.render()
    assert "Weak match" in rendered or "weak match" in rendered.lower()
    # The warning precedes the file list, and the file list still appears.
    warn_idx = rendered.lower().index("weak match")
    file_idx = rendered.index("app/")
    assert warn_idx < file_idx


def test_weak_signal_does_not_fire_on_genuine_multi_term_match(tmp_path):
    _make_navigation_repo(tmp_path)
    package = get_context("navigation dropdown is broken on mobile", root=tmp_path)

    assert package.included
    assert package.weak_signal is None
    assert "Weak match" not in package.render()


def _make_common_word_repo(root: Path) -> None:
    # A term with fname_count == 0 that is *common* rather than rare: it
    # shows up, in prose, in a large share (>15%) of the repo's files, and
    # no filename anywhere carries it. "english" by contrast appears
    # nowhere at all -- the classic rare-content case the original rule
    # already caught. Neither term corroborates anything distinctive; both
    # tails (common and rare) are weak.
    for i in range(4):
        _write(
            root,
            f"docs/notes-{i}.md",
            "In plain terms: keep this plain, write it plain, plain plain plain.\n",
        )
    # Filler so "plain" is a minority (~20%) of the repo, not a majority --
    # this is what distinguishes "common" from "the only kind of file
    # here."
    for i in range(16):
        _write(root, f"src/module_{i}.py", f"def helper_{i}():\n    return {i}\n")


def test_weak_signal_fires_on_common_content_only_term(tmp_path):
    # Regression test for the defect where a content-only term was judged
    # weak only when *rare* (<= WEAK_CONTENT_RARE_COUNT files), never when
    # *common* (a large share of the repo) -- see WEAK_CONTENT_COMMON_RATIO
    # above detect_weak_signal(). "plain" here matches ~20% of discovered
    # files by content alone (no filename match anywhere), which is
    # generic-prose-level evidence, not a real signal -- yet the old rule
    # let it stand in as "strong" simply because it wasn't rare.
    _make_common_word_repo(tmp_path)
    package = get_context("what's wrong, in plain English", root=tmp_path)

    assert package.included, "results must still be shown, never suppressed"
    assert package.weak_signal is not None
    assert "plain" in package.weak_signal["matched_terms"]
    assert "Weak match" in package.render()


def test_weak_signal_never_fires_for_single_term_task(tmp_path):
    # Single-term tasks have coverage_ratio == 1.0 for any matching file
    # by construction -- "only one term matched" would be true of every
    # such task, so the warning must be unconditionally suppressed here
    # regardless of how the one term is distributed across the repo.
    _make_page_convention_repo(tmp_path)
    package = get_context("page", root=tmp_path)

    assert package.included
    assert package.weak_signal is None


def test_weak_signal_absent_when_nothing_is_included(tmp_path):
    _write(tmp_path, "README.md", "hello world\n")
    package = get_context("completely unrelated quantum flibbertigibbet", root=tmp_path)

    assert not package.included
    assert package.weak_signal is None
    assert "No relevant context found" in package.render()


def test_weak_signal_json_field_present_and_none_by_default(tmp_path):
    _make_navigation_repo(tmp_path)
    package = get_context("navigation dropdown is broken on mobile", root=tmp_path)
    data = package.to_dict()

    assert "weak_signal" in data
    assert data["weak_signal"] is None

    _make_page_convention_repo(tmp_path)
    weak_package = get_context("improve landing page conversion rate", root=tmp_path)
    weak_data = weak_package.to_dict()
    assert weak_data["weak_signal"] is not None
    assert "matched_terms" in weak_data["weak_signal"]
    assert "term_file_counts" in weak_data["weak_signal"]
    # ContextItem.matched_terms is also exposed per-item in the JSON shape.
    assert "matched_terms" in weak_data["included"][0]
