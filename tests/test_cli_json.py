"""Tests for `octx --json` (v0.1 step 10).

`--json` is not a second formatting path -- it is `json.dumps(package.to_dict())`.
These tests check: the output parses, it round-trips the fixture's key facts
(included paths, the configuration_discrepancy finding with both values, at
least one test_reference_gap finding, the exclusion count), and stdout carries
nothing but JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"
TASK = "fix the authentication bug"


def _run_json_cli():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "opencontextually.cli",
            TASK,
            "--root",
            str(FIXTURE_ROOT),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result


def test_stdout_is_valid_json_and_nothing_else():
    result = _run_json_cli()
    # No banner, no ANSI codes, no trailing commentary -- stdout must be
    # parseable as JSON from the very first character to the last.
    data = json.loads(result.stdout)
    assert isinstance(data, dict)


def test_json_round_trips_key_facts():
    result = _run_json_cli()
    data = json.loads(result.stdout)

    assert data["task"] == TASK

    included_paths = {item["path"] for item in data["included"]}
    for expected in (
        "src/auth/middleware.py",
        "src/users/session.py",
        "docs/security.md",
        "config/auth.yaml",
        "tests/test_auth.py",
    ):
        assert expected in included_paths

    # configuration_discrepancy: 30 vs 60 minutes, both files/values present.
    assert data["conflicts"], "expected at least one configuration_discrepancy finding"
    conflict_text = json.dumps(data["conflicts"])
    assert "auth.yaml" in conflict_text
    assert "security.md" in conflict_text
    assert "30" in conflict_text
    assert "60" in conflict_text

    # test_reference_gap: at least one finding.
    assert data["missing"], "expected at least one test_reference_gap finding"

    # exclusion count is present and structurally sound.
    assert isinstance(data["excluded_count"], int)
    assert data["excluded_count"] == sum(data["excluded_by_reason"].values())

    # stable rule ids for machine consumers.
    assert "configuration_discrepancy" in data["trace"]["rules_run"]
    assert "test_reference_gap" in data["trace"]["rules_run"]


def test_json_output_has_no_extra_stdout_content():
    result = _run_json_cli()
    # json.loads already proves this, but make the "nothing but JSON"
    # requirement explicit: no leading/trailing non-JSON lines, no ANSI.
    stripped = result.stdout.strip()
    assert stripped.startswith("{")
    assert stripped.endswith("}")
    assert "\x1b[" not in result.stdout
    assert result.stderr == ""
