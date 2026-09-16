"""The unrelated-gate false-gap failure class, as a permanent guard.

The corpus case is black, "string normalization changes the wrong quotes":
`nodes.py` is pulled in transitively (linegen.py imports `is_empty_par`
from it), and `linegen.py`'s own quote-normalization code happens to gate
that unrelated call (`if is_empty_par(leaf): return leaf`) before doing the
actual quote work. test_reference_gap's corroboration rule -- a gated call
elsewhere counts as "another part of the codebase branches on this" -- has
no way to tell that gate apart from a genuinely task-relevant one like the
auth-bug demo's `is_session_expired` (see tests/test_auth_bug_example.py),
so it reports "no test references empty par": true, but not actionable for
a task about quote normalization.

The fixture below is that case in miniature, and it fails the same way.

It is marked strict-xfail rather than deleted or softened: a lexical
task-term-overlap filter was tried and reverted (see git history) because
it also silences the auth-bug demo's own `is_session_expired` finding --
"session"/"expired" do not lexically overlap "fix the authentication bug"
either, so a filter narrow enough to drop this finding drops that one too.
Telling the two apart needs a signal that reads why the gate matters to
the task, not word overlap. When something does distinguish them,
strict=True turns this into a failure so the win is noticed and locked in
rather than drifting past.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opencontextually import get_context

TASK = "string normalization changes the wrong quotes"


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _false_gap_repo(root: Path) -> None:
    _write(root, "black/__init__.py", "")
    # Directly task-relevant: matched on "string"/"normalization"/"quotes".
    # Its guard clause gates a call into an unrelated helper imported from
    # nodes.py, purely incidental to the quote-normalization logic below it.
    _write(
        root,
        "black/linegen.py",
        '"""Generate output lines, including string normalization."""\n'
        "from black.nodes import is_empty_par\n\n\n"
        "def normalize_string_quotes(leaf):\n"
        '    """Normalize the quote character used in a string literal."""\n'
        "    if is_empty_par(leaf):\n"
        "        return leaf\n"
        "    text = leaf.value\n"
        "    if text.startswith('\"'):\n"
        "        return leaf\n"
        "    leaf.value = text.replace(\"'\", '\"')\n"
        "    return leaf\n",
    )
    # Reached only as linegen.py's import dependency -- a generic AST-node
    # utility file, not itself about string normalization.
    _write(
        root,
        "black/nodes.py",
        '"""AST node helpers shared across black."""\n\n\n'
        "class Leaf:\n"
        "    value: str\n\n\n"
        "def is_empty_par(leaf):\n"
        '    """True if `leaf` is an empty parenthesis pair."""\n'
        '    return getattr(leaf, "value", "") == "()"\n',
    )
    # Covers the actual behavior; has no reason to mention parens.
    _write(
        root,
        "tests/test_strings.py",
        "def test_normalize_string_quotes_changes_single_to_double():\n"
        "    assert True\n",
    )


@pytest.mark.xfail(
    strict=True,
    reason="unrelated gate: is_empty_par corroborates a gap unrelated to the task",
)
def test_gated_call_unrelated_to_task_does_not_report_a_gap(tmp_path):
    _false_gap_repo(tmp_path)

    package = get_context(TASK, root=str(tmp_path))

    assert not any("empty par" in entry.get("term", "") for entry in package.missing)
