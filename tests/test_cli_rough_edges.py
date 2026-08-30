"""Tests for the three "rough edges" fixed together:

  - `octx "task" -json` (single dash) used to produce a bare, unhelpful
    argparse error with no indication of what to do instead. It must
    still be rejected (never silently accepted as `--json`), but with a
    hint suggesting the correct flag.
  - `--version`, printing `opencontextually.__version__`.
  - The weak-match warning and configuration_discrepancy conflicts used
    to share the same "⚠" marker; they must now be visually distinct,
    with the ASCII fallback path still working.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from opencontextually import __version__
from opencontextually.cli import main
from opencontextually.context import ContextItem, ContextPackage, _AsciiGlyphs, _UnicodeGlyphs

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "opencontextually.cli", *args],
        capture_output=True,
        text=True,
    )


# --- --version ---------------------------------------------------------


def test_version_flag_prints_package_version_and_exits_zero():
    result = _run("--version")
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_version_flag_does_not_require_a_task():
    # argparse's "version" action must short-circuit before the required
    # positional `task` is enforced.
    result = _run("--version")
    assert "the following arguments are required" not in result.stderr


# --- -json (single dash) friendly hint ----------------------------------


def test_single_dash_json_is_rejected_not_silently_accepted():
    result = _run("some task", "-json")
    assert result.returncode != 0
    # Never silently treated as --json: no JSON output produced.
    assert result.stdout.strip() == ""


def test_single_dash_json_suggests_the_correct_flag():
    result = _run("some task", "-json")
    assert "--json" in result.stderr


def test_single_dash_all_suggests_the_correct_flag():
    result = _run("some task", "-all")
    assert "--all" in result.stderr


def test_genuinely_unknown_flag_still_errors_without_a_bogus_hint():
    result = _run("some task", "-notaflag")
    assert result.returncode != 0
    # No real long option resembles this, so no "did you mean" hint.
    assert "did you mean" not in result.stderr


def test_double_dash_json_still_works_via_main():
    # main() is the programmatic entry point tests should prefer over a
    # subprocess where nothing about arg-typo handling is being exercised.
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["fix the authentication bug", "--root", str(FIXTURE_ROOT), "--json"])
    assert code == 0
    assert '"task"' in buf.getvalue()


# --- distinct markers for weak-match vs. conflicts ----------------------


def test_weak_signal_and_conflict_markers_are_distinct():
    assert _UnicodeGlyphs.WEAK != _UnicodeGlyphs.WARN
    assert _AsciiGlyphs.WEAK != _AsciiGlyphs.WARN
    # And distinct from the missing-context marker too.
    assert _UnicodeGlyphs.WEAK != _UnicodeGlyphs.GAP
    assert _AsciiGlyphs.WEAK != _AsciiGlyphs.GAP


def test_render_uses_warn_marker_for_conflicts_and_weak_marker_for_weak_signal():
    package = ContextPackage(
        task="fix things",
        included=[ContextItem(path="src/fix.py", role="source", reason="fixes things", score=1.0)],
        conflicts=[{"rule": "configuration_discrepancy", "message": "x: a.yaml:1 says 1, but b.md:1 says 2"}],
        weak_signal={
            "matched_terms": {"fix": 5},
            "term_file_counts": {"fix": 5, "things": 0},
        },
    )
    rendered = package.render(width=80)
    lines = rendered.splitlines()

    weak_line = next(ln for ln in lines if "Weak match" in ln)
    conflict_line = next(ln for ln in lines if "a.yaml:1 says 1" in ln)

    assert _UnicodeGlyphs.WEAK in weak_line
    assert _UnicodeGlyphs.WARN not in weak_line
    assert _UnicodeGlyphs.WARN in conflict_line
    assert _UnicodeGlyphs.WEAK not in conflict_line
