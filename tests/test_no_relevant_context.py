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


# --- bug fix: the trace claimed checks that never ran ----------------------
#
# `trace["rules_run"]` was a hardcoded four-element list, so an empty
# selection -- which skips `configuration_discrepancy` entirely and bails
# out of `find_test_reference_gaps` immediately -- still reported
# "Checks run: configuration_discrepancy, test_reference_gap" in the
# footer and both rule ids in the JSON. Found by running
# `octx "zzzqqxx nonexistentterm"` against this fixture. A machine
# consumer reads `trace` to know what was actually evaluated, so this was
# not merely cosmetic.


def test_empty_selection_does_not_claim_checks_ran():
    package = get_context(UNRELATED_TASK, root=FIXTURE_ROOT)
    assert package.included == []
    assert "configuration_discrepancy" not in package.trace["rules_run"]
    assert "test_reference_gap" not in package.trace["rules_run"]
    assert "Checks run: selection only" in package.render()


def test_non_empty_selection_still_reports_both_checks():
    package = get_context("fix the authentication bug", root=FIXTURE_ROOT)
    assert package.included != []
    assert "configuration_discrepancy" in package.trace["rules_run"]
    assert "test_reference_gap" in package.trace["rules_run"]


def test_rules_run_always_reports_the_selection_stages():
    for task in (UNRELATED_TASK, "fix the authentication bug"):
        rules_run = get_context(task, root=FIXTURE_ROOT).trace["rules_run"]
        assert rules_run[:2] == ["lexical_selection", "transitive_import_expansion"]
