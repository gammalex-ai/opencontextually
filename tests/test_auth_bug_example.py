"""First end-to-end test, per the v0.1 plan's "First end-to-end test"
section.

Runs `get_context("fix the authentication bug", root=examples/auth_bug)`
against the demo fixture and checks the ten assertions listed in the plan.

All ten assertions pass. Assertion 2 (session.py included via transitive
import expansion, with provenance naming the middleware.py import edge) was
un-xfailed at step 5. Assertion 6 (configuration_discrepancy, 30 vs 60
minutes) was un-xfailed at step 8. Assertion 7 (test_reference_gap) was
un-xfailed at step 9.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually import get_context

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"
TASK = "fix the authentication bug"

MAX_PACKAGE_BYTES = 60_000


def _get_package():
    return get_context(TASK, root=FIXTURE_ROOT)


def _find(package, path: str):
    for item in package.included:
        if item.path == path:
            return item
    return None


# --- 1: middleware.py included -------------------------------------------


def test_middleware_included():
    package = _get_package()
    assert _find(package, "src/auth/middleware.py") is not None


# --- 2: session.py included, provenance names the import edge ------------
# middleware.py imports session.py; plain lexical scoring has no term match
# for session.py, so it is only reachable via transitive import expansion
# (step 5). This is the hypothesis assertion.


def test_session_included_via_import_provenance():
    package = _get_package()
    item = _find(package, "src/users/session.py")
    assert item is not None
    assert any("middleware.py" in step for step in item.provenance)


# --- 3: docs/security.md included -----------------------------------------


def test_security_doc_included():
    package = _get_package()
    assert _find(package, "docs/security.md") is not None


# --- 4: config/auth.yaml included ------------------------------------------


def test_auth_config_included():
    package = _get_package()
    assert _find(package, "config/auth.yaml") is not None


# --- 5: tests/test_auth.py included ----------------------------------------


def test_auth_test_file_included():
    package = _get_package()
    assert _find(package, "tests/test_auth.py") is not None


# --- 6: configuration_discrepancy citing 30 vs 60, naming both files -----
# Requires the configuration_discrepancy CHECK rule (step 8). Today
# ContextPackage.conflicts is always [].


def test_session_timeout_discrepancy_detected():
    package = _get_package()
    assert package.conflicts, "expected a configuration_discrepancy conflict"

    matches = [
        conflict
        for conflict in package.conflicts
        if conflict.get("rule") == "configuration_discrepancy"
        and "30" in str(conflict)
        and "60" in str(conflict)
    ]
    assert matches, package.conflicts

    conflict = matches[0]
    conflict_text = str(conflict)
    assert "config/auth.yaml" in conflict_text
    assert "docs/security.md" in conflict_text
    assert "line" in conflict_text.lower()


# --- 7: test_reference_gap for session expiration --------------------------
# The test_reference_gap CHECK rule (step 9) is implemented: tests/test_auth.py
# covers login and logout but never session expiration, so session.py's
# `is_session_expired` (and middleware.py's use of it) should surface as a
# reference gap.


def test_session_expiration_reference_gap_detected():
    package = _get_package()
    assert package.missing, "expected a test_reference_gap entry"

    matches = [
        entry
        for entry in package.missing
        if entry.get("rule") == "test_reference_gap"
        and "expir" in str(entry).lower()
    ]
    assert matches, package.missing


# --- 8: filler files excluded and counted -----------------------------------


def test_filler_files_excluded_and_counted():
    package = _get_package()

    filler_paths = {
        "src/billing/invoice.py",
        "src/reports/monthly.py",
        "src/reports/weekly.py",
        "src/utils/strings.py",
        "src/utils/dates.py",
        "src/notifications/email.py",
        "src/inventory/catalog.py",
        "config/logging.yaml",
        "docs/style.md",
        "tests/test_billing.py",
    }
    included_paths = {item.path for item in package.included}

    assert filler_paths.isdisjoint(included_paths)
    assert package.excluded_count > 0
    assert sum(package.excluded_by_reason.values()) == package.excluded_count


# --- 9: every included item has a non-empty reason and >=1 excerpt --------
# Bounded excerpt extraction (step 6) is now implemented: every included
# item carries at least one Excerpt justifying its inclusion.


def test_included_items_have_reason_and_excerpt():
    package = _get_package()
    assert package.included, "expected at least one included item"

    for item in package.included:
        assert item.reason, f"{item.path} has no reason"
        assert item.excerpts, f"{item.path} has no bounded excerpts"


# --- 10: total excerpt bytes within the package budget ---------------------
# Bounded excerpt extraction (step 6) is now implemented. This asserts a
# *non-trivial* amount of excerpt content (not just "0 <= budget") that
# still respects the package-wide byte budget.


def test_excerpt_bytes_within_package_budget():
    package = _get_package()

    total_bytes = sum(
        len(excerpt.text.encode("utf-8"))
        for item in package.included
        for excerpt in item.excerpts
    )

    assert 0 < total_bytes <= MAX_PACKAGE_BYTES
