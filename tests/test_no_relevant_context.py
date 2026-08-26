"""Regression test for the "no relevant context, but findings anyway"
defect.

Before the fix, `get_context()` ran `configuration_discrepancy` over every
discovered config/doc file regardless of what SELECT chose, so a task
matching nothing in the repo could still print:

    No relevant context found for this task.

    Conflicts (1):
      1. session.timeout_minutes
         ...

That is self-contradictory: it says nothing is relevant, then reports a
finding anyway. OpenContextually's contract is context *for a task* -- when
SELECT finds nothing relevant, a repo-wide discrepancy is not this task's
problem and must not be volunteered. This test locks in the fix: an
unrelated task against the auth_bug fixture (which does contain a real
config/doc discrepancy for the *authentication* task) must come back with
no included files, no conflicts, and no missing findings, and render() must
never show a "No relevant context found" message alongside any findings.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually import get_context

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"

UNRELATED_TASK = "quantum blockchain refrigerator"


def test_unrelated_task_has_no_included_files():
    package = get_context(UNRELATED_TASK, root=FIXTURE_ROOT)
    assert package.included == []


def test_unrelated_task_reports_no_conflicts():
    # The fixture DOES contain a real configuration_discrepancy (30 vs 60
    # minute session timeout) for the authentication task -- see
    # test_auth_bug_example.py. It must not leak into an unrelated task's
    # results just because it exists somewhere in the repo.
    package = get_context(UNRELATED_TASK, root=FIXTURE_ROOT)
    assert package.conflicts == []


def test_unrelated_task_reports_no_missing_findings():
    package = get_context(UNRELATED_TASK, root=FIXTURE_ROOT)
    assert package.missing == []


def test_render_never_pairs_no_relevant_context_with_findings():
    package = get_context(UNRELATED_TASK, root=FIXTURE_ROOT)
    rendered = package.render()
    assert "No relevant context found for this task." in rendered
    assert "Conflicts (" not in rendered
    assert "Missing (" not in rendered


def test_related_task_still_finds_the_conflict():
    # Sanity check: the guard above is scoped to the empty-selection case,
    # not a blanket suppression of configuration_discrepancy -- the
    # authentication task must still surface the real discrepancy.
    package = get_context("fix the authentication bug", root=FIXTURE_ROOT)
    assert package.included != []
    assert package.conflicts != []
