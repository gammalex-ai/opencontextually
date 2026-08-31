"""The README's flagship example must be the tool's real output.

The README opens with `gctx "fix the authentication bug"` run against
`examples/auth_bug/`, presented as "the real, unedited output". It drifted:
the exclusion-summary rewrite changed that footer, and the README kept the
old one-line `below_threshold=21, binary=0, ...` form for several releases.
The very first command a new user runs then disagreed with the page that
told them to run it.

This test regenerates the render and asserts the README still contains it
verbatim, so the block cannot silently go stale again. `width` is pinned
so the assertion does not depend on the terminal running the tests.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually import get_context

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_ROOT = REPO_ROOT / "examples" / "auth_bug"
README = REPO_ROOT / "README.md"
TASK = "fix the authentication bug"
RENDER_WIDTH = 100


def test_readme_shows_the_real_compact_output():
    rendered = get_context(TASK, root=FIXTURE_ROOT).render(width=RENDER_WIDTH)
    readme = README.read_text()
    assert rendered in readme, (
        "The README's example block is stale. Replace it with:\n\n" + rendered
    )


def test_readme_exclusion_footer_is_not_the_old_jargon_form():
    readme = README.read_text()
    # The pre-rewrite footer leaked internal bucket names at the reader.
    for jargon in ("below_threshold=", "over_budget=", "over_cap=", "oversize="):
        assert jargon not in readme, f"stale exclusion jargon in README: {jargon}"
