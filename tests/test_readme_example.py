"""The README's flagship example must stay internally consistent.

The "What it looks like" section shows real `gctx` output from a clone of
fastapi/fastapi at a pinned commit, presented as "the real, unedited
output". Earlier this was a local fixture under `examples/auth_bug/`, and a
test regenerated that render and compared it against the README verbatim --
see git history. That approach doesn't extend to an external repository: the
corpus repos aren't committed (`benchmarks/fetch-corpus.sh` clones them on
demand), so nothing in this repo can regenerate that exact render offline or
in CI.

Short of that, this test checks the block's own arithmetic instead: the
visible file count plus "+N more" must equal the headline "relevant" count,
and the exclusion buckets must sum to the headline "excluded" count. A hand
edit that changes one number without the others -- the way the old fixture
example drifted -- fails this test.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
README = REPO_ROOT / "README.md"

HEADLINE_RE = re.compile(r"(?P<relevant>\d+) relevant · (?P<excluded>\d+) excluded")
MORE_RE = re.compile(r"\+(?P<more>\d+) more")
DROPPED_RE = re.compile(r"⚠ (?P<dropped>\d+) relevant files dropped")
SCANNED_RE = re.compile(r"(?P<scanned>\d+) files scanned, not relevant enough")
NOT_SCANNED_RE = re.compile(r"(?P<not_scanned>\d+) files not scanned")


def _example_block() -> str:
    readme = README.read_text()
    # Two fenced blocks follow the heading: the invocation, then the output.
    matches = list(re.finditer(r"```\n(.*?)```", readme[readme.index("## What it looks like"):], re.DOTALL))
    assert len(matches) >= 2, "expected an invocation block and an output block"
    return matches[1].group(1)


def test_readme_example_counts_are_internally_consistent():
    output = _example_block()

    headline = HEADLINE_RE.search(output)
    assert headline, "README example is missing the 'N relevant · N excluded' headline"
    relevant = int(headline.group("relevant"))
    excluded = int(headline.group("excluded"))

    included_section = output.split("\nExcluded:")[0]
    shown_paths = [
        line.strip().split("  ")[0]
        for line in included_section.splitlines()
        if line.startswith("  ") and line.strip() and not line.strip().startswith("+")
    ]
    more = MORE_RE.search(output)
    more_count = int(more.group("more")) if more else 0
    assert len(shown_paths) + more_count == relevant, (
        f"{len(shown_paths)} shown + {more_count} more != {relevant} relevant"
    )

    dropped = int(DROPPED_RE.search(output).group("dropped"))
    scanned = int(SCANNED_RE.search(output).group("scanned"))
    not_scanned = int(NOT_SCANNED_RE.search(output).group("not_scanned"))
    assert dropped + scanned + not_scanned == excluded, (
        f"{dropped} dropped + {scanned} scanned + {not_scanned} not scanned != {excluded} excluded"
    )


def test_readme_exclusion_footer_is_not_the_old_jargon_form():
    readme = README.read_text()
    # The pre-rewrite footer leaked internal bucket names at the reader.
    for jargon in ("below_threshold=", "over_budget=", "over_cap=", "oversize="):
        assert jargon not in readme, f"stale exclusion jargon in README: {jargon}"
