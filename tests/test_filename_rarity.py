"""Unit tests for the filename-match rarity fix in selector.py.

A filename term match should be weighted by how rare that word is across
the repo being scanned: a framework-convention basename repeated many
times (page.tsx in a Next.js App Router repo) should barely register,
while a name that is unique in the repo should keep full weight. See the
comment block above WEIGHT_IMPORTED_SYMBOL in selector.py for the full
rationale.

File contents below are deliberately free of the task words being tested
(no "Page"/"Nav"/"Widget"/"Gadget" identifiers) so content_score never
contributes -- these tests isolate the filename-rarity weighting alone.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually.discovery import discover
from opencontextually.selector import (
    WEIGHT_FILENAME,
    compute_filename_word_counts,
    score_file,
)

_counter = iter(range(1_000_000))


def _write(root: Path, rel_path: str, content: str | None = None) -> None:
    # Content must be byte-distinct per file -- discover() collapses
    # byte-identical files down to one representative, which would quietly
    # shrink these fixtures to far fewer files than the test names. A
    # bare numeric counter (never the path or a task word) keeps content
    # free of any word these tests match on, so content_score never
    # contributes -- only the filename-rarity weighting is under test.
    if content is None:
        content = f"// fixture content {next(_counter)}\n"
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_repeated_basename_is_demoted_relative_to_unique_basename(tmp_path):
    # 14 files all named page.tsx (the Next.js App Router convention),
    # plus one file whose *name* (navigation.ts) is unique in the repo.
    for i in range(14):
        _write(tmp_path, f"app/route_{i}/page.tsx")
    _write(tmp_path, "lib/navigation.ts")

    discovered, _reasons = discover(tmp_path)
    word_counts = compute_filename_word_counts(discovered)

    page_file = next(f for f in discovered if f.path == "app/route_0/page.tsx")
    nav_file = next(f for f in discovered if f.path == "lib/navigation.ts")

    page_score = score_file(page_file, ["page"], word_counts)
    nav_score = score_file(nav_file, ["navigation"], word_counts)

    # A word that is unique to one file in the repo keeps full weight.
    assert nav_score == WEIGHT_FILENAME

    # A word repeated across 14 files is damped well below full weight --
    # the whole point of the fix -- but never to zero (smooth falloff, not
    # a cliff).
    assert 0 < page_score < WEIGHT_FILENAME
    assert page_score < WEIGHT_FILENAME * 0.5


def test_rarity_is_judged_per_matched_word_not_whole_basename(tmp_path):
    # user_page.tsx is itself a unique *basename*, but it shares the word
    # "page" with the 14 other page.tsx files below -- rarity has to be
    # judged word by word, not by comparing whole filenames, or this file
    # would wrongly get full weight for matching "page" just because no
    # other file is named exactly "user_page.tsx".
    for i in range(14):
        _write(tmp_path, f"app/route_{i}/page.tsx")
    _write(tmp_path, "app/account/user_page.tsx")

    discovered, _reasons = discover(tmp_path)
    word_counts = compute_filename_word_counts(discovered)
    user_page_file = next(f for f in discovered if f.path == "app/account/user_page.tsx")

    # "user" is unique to this one file -> full weight for that term.
    user_only_score = score_file(user_page_file, ["user"], word_counts)
    assert user_only_score == WEIGHT_FILENAME

    # "page" is shared with 14 other files -> damped, even though this
    # file's own *basename* ("user_page.tsx") is unique in the repo.
    page_only_score = score_file(user_page_file, ["page"], word_counts)
    assert page_only_score < WEIGHT_FILENAME * 0.5


def test_falloff_is_smooth_not_a_cliff(tmp_path):
    # A word appearing twice should be damped much less severely than one
    # appearing fifty times -- logarithmic falloff, not a hard cutoff.
    for i in range(2):
        _write(tmp_path, f"pkg/twice_{i}/widget.py", f"# fixture file {i}\n")
    for i in range(50):
        _write(tmp_path, f"pkg/many_{i}/gadget.py", f"# fixture file {i + 100}\n")

    discovered, _reasons = discover(tmp_path)
    word_counts = compute_filename_word_counts(discovered)
    twice_file = next(f for f in discovered if f.path == "pkg/twice_0/widget.py")
    many_file = next(f for f in discovered if f.path == "pkg/many_0/gadget.py")

    twice_score = score_file(twice_file, ["widget"], word_counts)
    many_score = score_file(many_file, ["gadget"], word_counts)

    assert twice_score > many_score > 0
    # Twice-repeated should still keep most of its weight; fifty-repeated
    # should not.
    assert twice_score > WEIGHT_FILENAME * 0.5
    assert many_score < WEIGHT_FILENAME * 0.3


def test_missing_word_counts_defaults_to_full_weight(tmp_path):
    # Standalone callers (tests, or any future caller with no repo-wide
    # view) that omit filename_word_counts entirely must see exactly the
    # pre-fix behavior: every matched term treated as unique.
    _write(tmp_path, "lib/navigation.ts")
    discovered, _reasons = discover(tmp_path)
    nav_file = next(f for f in discovered if f.path == "lib/navigation.ts")

    assert score_file(nav_file, ["navigation"]) == WEIGHT_FILENAME
