"""Unit tests for checks.py's configuration_discrepancy rule.

Negative cases get at least as much attention as the positive case -- per
the v0.1 plan, a false positive here is worse than a miss, so the things
that must NOT produce a finding are tested as deliberately as the thing
that must.
"""

from __future__ import annotations

from opencontextually.checks import find_configuration_discrepancies
from opencontextually.discovery import discover


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


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
