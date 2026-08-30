"""Unit tests for checks.py: configuration_discrepancy and
test_reference_gap.

Negative cases get at least as much attention as the positive case -- per
the v0.1 plan, a false positive here is worse than a miss, so the things
that must NOT produce a finding are tested as deliberately as the thing
that must.
"""

from __future__ import annotations

from opencontextually.checks import find_configuration_discrepancies, find_test_reference_gaps
from opencontextually.context import ContextItem
from opencontextually.discovery import discover


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _selected_items(discovered, roles=("source", "config")):
    """Simulate SELECT having chosen every discovered file of the given
    roles -- test_reference_gap only scans files SELECT actually chose,
    so tests build the `items` argument this way rather than depending
    on the real scoring/threshold logic in selector.py.
    """
    return [
        ContextItem(path=f.path, role=f.role, reason="test fixture", score=1.0)
        for f in discovered
        if f.role in roles
    ]


def _discover(tmp_path):
    discovered, _reasons = discover(tmp_path)
    return discovered


# --- positive case: real mismatch, nested YAML key -------------------------


def test_detects_mismatched_duration_with_nested_yaml_key(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n  cookie_name: session_id\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "# Security\n\n- Session timeout: 30 minutes.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))

    matches = [f for f in findings if f["rule"] == "configuration_discrepancy"]
    assert matches, findings
    finding = matches[0]
    assert finding["setting"] == "session.timeout_minutes"
    assert finding["config"] == {"path": "config/auth.yaml", "line": 2, "value": "60"}
    assert finding["doc"]["path"] == "docs/security.md"
    assert finding["doc"]["line"] == 3
    assert "30" in finding["doc"]["value"]


# --- negative: doc code examples are not requirements -----------------------
#
# A value that only appears inside a doc's code example -- fenced Markdown,
# an rst code-block/sourcecode directive, or an rst `::` literal block --
# demonstrates something (often how to *override* a default) rather than
# asserting a project-wide requirement. See the "Bug fix: doc code examples
# misread as configuration requirements" note in checks.py.


def test_fenced_markdown_code_block_ignored(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "# Security\n\n"
        "Override the session timeout per-deployment like this:\n\n"
        "```yaml\n"
        "session:\n"
        "  timeout_minutes: 30\n"
        "```\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


def test_rst_code_block_directive_ignored(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(
        tmp_path / "docs" / "security.rst",
        "Security\n========\n\n"
        "A few common examples are shown below:\n\n"
        ".. code-block:: sql\n\n"
        "    -- Set a smaller indent for this file\n"
        "    -- sqlfluff:session:timeout_minutes:30\n\n"
        "We recommend the default for most projects.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


def test_rst_literal_block_ignored(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(
        tmp_path / "docs" / "security.rst",
        "Security\n========\n\n"
        "Example configuration::\n\n"
        "    session:\n"
        "      timeout_minutes: 30\n\n"
        "This is only an example, not the default.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


def test_genuine_prose_assertion_still_detected_alongside_code_example(tmp_path):
    # A doc that contains BOTH a code example and a genuine prose assertion
    # must still detect the real conflict from the prose line -- the fix
    # excludes code contexts, not the whole file.
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "# Security\n\n"
        "- Session timeout: 30 minutes.\n\n"
        "Example override:\n\n"
        "```yaml\n"
        "session:\n"
        "  timeout_minutes: 45\n"
        "```\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    matches = [f for f in findings if f["rule"] == "configuration_discrepancy"]
    assert matches, findings
    finding = matches[0]
    assert finding["doc"]["path"] == "docs/security.md"
    assert finding["doc"]["line"] == 3
    assert "30" in finding["doc"]["value"]


# --- negative: same value on both sides -> no finding ----------------------


def test_same_value_both_sides_no_finding(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 30\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "Session timeout: 30 minutes.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


# --- negative: similar-looking but unrelated keys ---------------------------


def test_unrelated_keys_with_same_generic_token_no_finding(tmp_path):
    # An HTTP client timeout and a session timeout both use the word
    # "timeout", but they are not the same setting. The rule requires
    # every core (non-unit) token of the config key to appear in the doc
    # line, so "http.timeout" must not correlate with a doc line that only
    # mentions "session" + "timeout".
    _write(
        tmp_path / "config" / "http.yaml",
        "http:\n  timeout: 5\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "Session timeout: 30 minutes.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


def test_single_generic_token_key_never_matched(tmp_path):
    # A bare, unnamespaced "timeout" key has only one meaningful token --
    # too ambiguous to correlate with any doc line at all, even one that
    # would otherwise look related.
    _write(tmp_path / "config" / "app.ini", "timeout = 5\n")
    _write(tmp_path / "docs" / "notes.md", "Server timeout: 30 minutes.\n")

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


# --- negative: value present in only one location ---------------------------


def test_value_only_in_config_no_finding(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(tmp_path / "docs" / "security.md", "Sessions must be secure.\n")

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


def test_value_only_in_doc_no_finding(tmp_path):
    _write(tmp_path / "config" / "auth.yaml", "session:\n  cookie_name: session_id\n")
    _write(tmp_path / "docs" / "security.md", "Session timeout: 30 minutes.\n")

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


# --- unit normalization ------------------------------------------------------


def test_unit_normalization_agrees_no_finding(tmp_path):
    # 30 minutes == 1800 seconds -- must be recognized as equal even
    # though the config uses a different unit than the doc.
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_seconds: 1800\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "Session timeout: 30 minutes.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


def test_unit_normalization_disagrees_is_a_finding(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "Session timeout: 30 minutes.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert len(findings) == 1
    assert findings[0]["setting"] == "session.timeout_minutes"


# --- nested YAML key path resolution ----------------------------------------


def test_nested_yaml_key_builds_dotted_path(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\ntop_level_unrelated: 1\n",
    )
    _write(tmp_path / "docs" / "security.md", "no relevant assertions here\n")

    from opencontextually.checks import _parse_yaml_like

    entries = _parse_yaml_like((tmp_path / "config" / "auth.yaml").read_text())
    keys = {key for key, _value, _line in entries}
    assert "session.timeout_minutes" in keys
    assert "top_level_unrelated" in keys


# --- malformed config does not crash the run --------------------------------


def test_malformed_json_config_does_not_crash(tmp_path):
    _write(tmp_path / "config" / "broken.json", "{ this is not valid json ]")
    _write(tmp_path / "docs" / "security.md", "Session timeout: 30 minutes.\n")

    # Must not raise.
    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


def test_no_config_or_no_docs_returns_empty(tmp_path):
    _write(tmp_path / "config" / "auth.yaml", "session:\n  timeout_minutes: 60\n")
    assert find_configuration_discrepancies(_discover(tmp_path)) == []

    tmp_path2_discovered = []
    assert find_configuration_discrepancies(tmp_path2_discovered) == []


# --- secret-looking keys are never reported ---------------------------------


def test_secret_looking_key_never_reported_even_if_numeric(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  api_key_ttl_minutes: 60\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "Api key ttl minutes: 30 minutes.\n",
    )

    findings = find_configuration_discrepancies(_discover(tmp_path))
    assert findings == []


# ==========================================================================
# test_reference_gap
# ==========================================================================


# --- positive: a concept that IS referenced by a test produces no finding --


def test_concept_referenced_by_test_produces_no_finding(tmp_path):
    _write(
        tmp_path / "src" / "auth.py",
        "def is_session_expired(session_id):\n    return True\n",
    )
    _write(
        tmp_path / "tests" / "test_auth.py",
        (
            "from src.auth import is_session_expired\n\n"
            "def test_expired_session_is_detected():\n"
            "    assert is_session_expired('abc')\n"
        ),
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "session expiration")
    assert findings == []


# --- negative: a project with no tests at all produces no findings ---------
#
# Naively, a testless project would make every single term a "gap" -- that
# is not new information, it is one fact ("no tests exist") repeated once
# per term, which is exactly the noise the v0.1 plan warns CHECK rules
# against. So the rule stays silent when there are zero discovered test
# files, rather than flooding `missing` with one entry per concept.


def test_no_tests_at_all_produces_no_findings(tmp_path):
    _write(
        tmp_path / "src" / "auth.py",
        "def unique_special_widget_rotation(): pass\n",
    )
    _write(
        tmp_path / "config" / "app.yaml",
        "widget:\n  rotation_interval_minutes: 5\n",
    )
    # deliberately no tests/ directory and no test_*.py file anywhere

    discovered, _reasons = discover(tmp_path)
    assert not any(f.role == "test" for f in discovered)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "rotate the widget")
    assert findings == []


# --- negative: a term appearing only in docs (not selected source/config) --


def test_term_only_in_docs_produces_no_finding(tmp_path):
    _write(
        tmp_path / "docs" / "notes.md",
        "# Widget rotation\n\nThe widget rotation feature is documented here.\n",
    )
    _write(
        tmp_path / "src" / "other.py",
        "def unrelated_helper(): pass\n",
    )
    # a test file must exist, otherwise the "no tests at all" rule above
    # would mask this case rather than exercising the docs-only path
    _write(
        tmp_path / "tests" / "test_other.py",
        "from src.other import unrelated_helper\n\ndef test_unrelated_helper():\n    unrelated_helper()\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)  # role=="docs" is excluded by role filter

    findings = find_test_reference_gaps(items, discovered, "widget rotation")
    assert not any("widget" in f["term"] or "rotation" in f["term"] for f in findings)


# --- naming-convention tolerance: stemmed match, not exact substring -------


def test_different_naming_convention_treated_as_referenced(tmp_path):
    _write(
        tmp_path / "src" / "session.py",
        "def session_expiry(session_id):\n    return False\n",
    )
    _write(
        tmp_path / "tests" / "test_session.py",
        "def test_session_expires():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "session expiry")

    assert not any(f["term"] == "session expiry" for f in findings)


# --- a genuine gap, isolated from the auth_bug fixture's other findings ----


def test_unreferenced_concept_is_reported_with_evidence_location(tmp_path):
    _write(
        tmp_path / "src" / "session.py",
        "def is_session_expired(session_id):\n    return True\n",
    )
    # A source-symbol gap only survives the real-repo-tuning corroboration
    # bar (see checks.py) when the symbol's call site gates control flow
    # elsewhere -- an `if` whose test calls it and whose body raises or
    # returns. Without this caller, is_session_expired would be a true but
    # weak finding and is deliberately suppressed by find_test_reference_gaps.
    _write(
        tmp_path / "src" / "middleware.py",
        "def authenticate(store, session_id):\n"
        "    if is_session_expired(session_id):\n"
        "        raise ValueError('expired')\n"
        "    return session_id\n",
    )
    _write(
        tmp_path / "tests" / "test_session.py",
        "def test_create_and_lookup():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "fix the session bug")

    matches = [f for f in findings if f["rule"] == "test_reference_gap" and "expir" in f["term"]]
    assert matches, findings
    finding = matches[0]
    assert finding["path"] == "src/session.py"
    assert finding["line"] == 1
    assert "add a test" not in finding["message"].lower()
    assert "missing" not in finding["message"].lower()


# --- bug fix: test_reference_gap noise from transitively-pulled files ------
#
# Real-repo evaluation (click) showed source-symbol corroboration firing
# from a gated call sitting entirely inside a file SELECT only reached by
# following an import edge, unrelated to the task's own terms -- four
# findings about ASCII encoding and binary streams for a task about option
# parsing. The fix restricts the gated-call corroboration signal to files
# SELECT matched *directly* (empty provenance); a gate inside a file only
# reached transitively no longer corroborates a finding on its own.


def test_gate_in_transitively_reached_file_produces_no_finding(tmp_path):
    _write(
        tmp_path / "src" / "compat.py",
        "def is_ascii_encoding(encoding):\n    return encoding == 'ascii'\n\n"
        "def get_best_encoding(stream):\n"
        "    if is_ascii_encoding(stream.encoding):\n"
        "        raise ValueError('ascii not supported')\n"
        "    return stream.encoding\n",
    )
    _write(
        tmp_path / "tests" / "test_stream.py",
        "def test_create_and_lookup():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    # compat.py was only reached by following an import edge from some
    # other seed -- not matched by the task's own terms -- so it carries a
    # non-empty provenance, unlike the _selected_items() helper's default.
    items = [
        ContextItem(
            path="src/compat.py",
            role="source",
            reason="imported by core.py",
            score=1.0,
            provenance=["src/core.py imports src/compat.py"],
        ),
    ] + [
        ContextItem(path=f.path, role=f.role, reason="test fixture", score=1.0)
        for f in discovered
        if f.role == "config"
    ]

    findings = find_test_reference_gaps(items, discovered, "option parsing drops the value after a flag")

    assert not any("ascii" in f["term"] for f in findings), findings


def test_gate_in_directly_matched_file_still_fires_even_for_transitive_symbol(tmp_path):
    # The corroborating gate lives in a directly-matched (seed) file, even
    # though the symbol it gates is defined in a file only reached
    # transitively -- this is exactly the auth_bug shape (middleware.py,
    # a seed, gates a call into session.py's is_session_expired) and must
    # keep firing.
    _write(
        tmp_path / "src" / "session.py",
        "def is_session_expired(session_id):\n    return True\n",
    )
    _write(
        tmp_path / "src" / "middleware.py",
        "def authenticate(store, session_id):\n"
        "    if is_session_expired(session_id):\n"
        "        raise ValueError('expired')\n"
        "    return session_id\n",
    )
    _write(
        tmp_path / "tests" / "test_session.py",
        "def test_create_and_lookup():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = [
        ContextItem(
            path="src/session.py",
            role="source",
            reason="imported by middleware.py",
            score=1.0,
            provenance=["src/middleware.py imports src/session.py"],
        ),
        ContextItem(
            path="src/middleware.py",
            role="source",
            reason="filename matches 'authentication'",
            score=10.0,
            provenance=[],
        ),
    ]

    findings = find_test_reference_gaps(items, discovered, "fix the authentication bug")

    assert any("expir" in f["term"] for f in findings), findings


# --- step 11: real-repo tuning ----------------------------------------------
#
# An earlier version of test_reference_gap turned every unreferenced
# def/class name and every unreferenced config key into its own finding --
# six findings for one task on the flagship demo fixture. These tests lock
# in the corroboration bar added at step 11: an uncorroborated gap (a
# plainly-called, ungated method; an undisputed config value) is real but
# weak, and is deliberately suppressed.


def test_ungated_method_call_produces_no_finding(tmp_path):
    _write(
        tmp_path / "src" / "session.py",
        "def touch_session(session_id):\n    pass\n",
    )
    _write(
        tmp_path / "src" / "middleware.py",
        # Called, but unconditionally -- not gating an if/raise/return --
        # so this is a plain side-effect call, not a decision point.
        "def handle(store, session_id):\n"
        "    touch_session(session_id)\n"
        "    return session_id\n",
    )
    _write(
        tmp_path / "tests" / "test_session.py",
        "def test_create_and_lookup():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "fix the session bug")

    assert not any("touch" in f["term"] for f in findings)


def test_undisputed_config_value_produces_no_finding(tmp_path):
    _write(
        tmp_path / "config" / "app.yaml",
        "session:\n  cookie_name: session_id\n",
    )
    _write(
        tmp_path / "src" / "app.py",
        "def configure_session():\n    pass\n",
    )
    _write(
        tmp_path / "tests" / "test_app.py",
        "def test_configure_session():\n    configure_session()\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    # No configuration_discrepancy conflict for this key -- it is not in
    # dispute anywhere -- so a config-key gap should not fire either.
    findings = find_test_reference_gaps(items, discovered, "fix the session config", conflicts=[])

    assert not any("cookie" in f["term"] for f in findings)


def test_config_gap_fires_only_when_corroborated_by_conflict(tmp_path):
    _write(
        tmp_path / "config" / "app.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(
        tmp_path / "src" / "app.py",
        "def configure_session():\n    pass\n",
    )
    _write(
        tmp_path / "tests" / "test_app.py",
        "def test_configure_session():\n    configure_session()\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    without_conflict = find_test_reference_gaps(
        items, discovered, "fix the session timeout", conflicts=[]
    )
    assert not any("timeout" in f["term"] for f in without_conflict)

    conflict = {
        "rule": "configuration_discrepancy",
        "setting": "session.timeout_minutes",
        "message": "session.timeout_minutes: config/app.yaml:2 declares 60, but docs say 30",
    }
    with_conflict = find_test_reference_gaps(
        items, discovered, "fix the session timeout", conflicts=[conflict]
    )
    assert any("timeout" in f["term"] for f in with_conflict)


# --- bug fix: conflicts scoped to the task ---------------------------------
#
# configuration_discrepancy used to scan every discovered config/doc file
# regardless of what the task asked about, so a real (but wholly
# unrelated) config/doc disagreement elsewhere in the repo could surface
# under a task that never named, matched, or included either cited file --
# observed for real: a task about "plain English" output citing this
# project's own examples/auth_bug fixture files. A finding must now
# survive only when at least one cited file is in `included_paths` or
# lexically matches one of `task_terms`.


def _unrelated_conflict_fixture(tmp_path):
    _write(
        tmp_path / "config" / "auth.yaml",
        "session:\n  timeout_minutes: 60\n",
    )
    _write(
        tmp_path / "docs" / "security.md",
        "# Security\n\n- Session timeout: 30 minutes.\n",
    )
    return _discover(tmp_path)


def test_unscoped_call_keeps_pre_scoping_behavior(tmp_path):
    # Omitting both included_paths and task_terms entirely (the old
    # call shape) must not silently start dropping every finding --
    # scoping only activates when a caller actually opts in.
    discovered = _unrelated_conflict_fixture(tmp_path)
    findings = find_configuration_discrepancies(discovered)
    assert len(findings) == 1


def test_conflict_suppressed_when_neither_file_relates_to_task(tmp_path):
    discovered = _unrelated_conflict_fixture(tmp_path)
    findings = find_configuration_discrepancies(
        discovered,
        included_paths=set(),
        task_terms=["plain", "english"],
    )
    assert findings == []


def test_conflict_kept_when_config_file_is_included(tmp_path):
    discovered = _unrelated_conflict_fixture(tmp_path)
    findings = find_configuration_discrepancies(
        discovered,
        included_paths={"config/auth.yaml"},
        task_terms=["plain", "english"],
    )
    assert len(findings) == 1


def test_conflict_kept_when_doc_file_is_included(tmp_path):
    discovered = _unrelated_conflict_fixture(tmp_path)
    findings = find_configuration_discrepancies(
        discovered,
        included_paths={"docs/security.md"},
        task_terms=["plain", "english"],
    )
    assert len(findings) == 1


def test_conflict_kept_when_task_terms_lexically_match_a_cited_path(tmp_path):
    # "auth" isn't in either file's *content* relevance, but it is a
    # literal path segment of config/auth.yaml -- a lexical path match is
    # enough to connect the finding to the task even with no included
    # files at all.
    discovered = _unrelated_conflict_fixture(tmp_path)
    findings = find_configuration_discrepancies(
        discovered,
        included_paths=set(),
        task_terms=["auth"],
    )
    assert len(findings) == 1


def test_auth_bug_conflict_still_fires_when_scoped_to_its_own_task():
    # The real fixture the e2e test depends on: task-scoping must not
    # regress the one conflict this rule is meant to catch.
    from pathlib import Path

    root = Path(__file__).parent.parent / "examples" / "auth_bug"
    discovered, _reasons = discover(root)
    findings = find_configuration_discrepancies(
        discovered,
        included_paths={
            "src/auth/middleware.py",
            "src/users/session.py",
            "tests/test_auth.py",
            "README.md",
            "config/auth.yaml",
            "docs/security.md",
        },
        task_terms=["authentication", "bug"],
    )
    assert len(findings) == 1
    assert findings[0]["setting"] == "session.timeout_minutes"


# --- bug fix: private helpers reported as user-facing findings -------------
#
# test_reference_gap used to turn any def/class name (public or private)
# into a concept, then report "no test references <words split from the
# name>" once corroborated by a gated call. Observed for real on this
# project's own repo: `_looks_like_secret_key`, a private redaction
# helper, produced "no test references looks like secret key" on three
# unrelated tasks ("how does discovery walk the tree", "the redaction
# masks ordinary code", "what's wrong, in plain English") purely because
# selector.py -- the file that defines and gates it -- happened to be
# selected on incidental word matches (`ast.walk` appears throughout).
# Leading-underscore names are implementation details by convention, not
# something a caller or test is expected to reference directly, and
# splitting one into words routinely produces unreadable prose. Fixed two
# ways: private/dunder names never become a concept at all, and even a
# public symbol only corroborates when its gate lives in a *different*
# file than its own definition (same-file gating is just the module using
# its own helper, not "another part of the codebase" depending on it).


def test_private_helper_never_becomes_a_finding(tmp_path):
    _write(
        tmp_path / "src" / "redact.py",
        "def _looks_like_secret_key(key):\n"
        "    return 'secret' in key\n\n"
        "def redact_value(key, value):\n"
        "    if not _looks_like_secret_key(key):\n"
        "        return value\n"
        "    return '***'\n",
    )
    _write(
        tmp_path / "tests" / "test_something_else.py",
        "def test_unrelated():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "how does discovery walk the tree")

    assert not any("secret" in f["term"] for f in findings), findings
    assert not any("looks like" in f["message"] for f in findings), findings


def test_private_helper_still_excluded_even_when_gated_from_another_file(tmp_path):
    # Even with a cross-file gate (which would otherwise corroborate a
    # public symbol -- see the class of tests above), a private name must
    # never surface: privacy is checked before corroboration, not instead
    # of it.
    _write(
        tmp_path / "src" / "redact.py",
        "def _looks_like_secret_key(key):\n    return 'secret' in key\n",
    )
    _write(
        tmp_path / "src" / "config_loader.py",
        "def load(key, value):\n"
        "    if _looks_like_secret_key(key):\n"
        "        raise ValueError('refusing to log secret')\n"
        "    return value\n",
    )
    _write(
        tmp_path / "tests" / "test_something_else.py",
        "def test_unrelated():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "load configuration values")

    assert not any("secret" in f["term"] for f in findings), findings


def test_same_file_gate_does_not_corroborate_a_public_symbol(tmp_path):
    # A public symbol gated only within its *own* defining file is not
    # "another part of the codebase" depending on it -- see
    # _gated_elsewhere() in checks.py. Without a cross-file gate this
    # finding must not survive the corroboration bar.
    _write(
        tmp_path / "src" / "widget.py",
        "def is_widget_ready(widget):\n"
        "    return widget.state == 'ready'\n\n"
        "def render(widget):\n"
        "    if not is_widget_ready(widget):\n"
        "        raise ValueError('not ready')\n"
        "    return widget.html\n",
    )
    _write(
        tmp_path / "tests" / "test_something_else.py",
        "def test_unrelated():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "render the widget")

    assert not any("widget" in f["term"] and "ready" in f["term"] for f in findings), findings


def test_cross_file_gate_still_corroborates_a_public_symbol(tmp_path):
    # The auth_bug shape, isolated: a public symbol defined in one file,
    # gated from a *different* directly-matched file, must still fire --
    # this is the real cross-module risky-path signal the rule exists to
    # surface, and must survive both the privacy filter and the new
    # cross-file requirement.
    _write(
        tmp_path / "src" / "session.py",
        "def is_session_expired(session_id):\n    return True\n",
    )
    _write(
        tmp_path / "src" / "middleware.py",
        "def authenticate(store, session_id):\n"
        "    if is_session_expired(session_id):\n"
        "        raise ValueError('expired')\n"
        "    return session_id\n",
    )
    _write(
        tmp_path / "tests" / "test_session.py",
        "def test_create_and_lookup():\n    assert True\n",
    )

    discovered, _reasons = discover(tmp_path)
    items = _selected_items(discovered)

    findings = find_test_reference_gaps(items, discovered, "fix the session bug")

    assert any("expir" in f["term"] for f in findings), findings
