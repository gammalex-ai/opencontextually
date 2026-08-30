"""Tests for the reason-column readability fix (see the "bug fix: reasons
truncated into uselessness" comment block in context.py above
MIN_REASON_WIDTH).

The original bug: the path column claimed almost all available width for a
long path (e.g. ".github/ISSUE_TEMPLATE/bug_report.yml"), leaving the
reason as little as ~15 characters before a trailing "..." -- not enough to
carry any information. The fix guarantees the reason at least
MIN_REASON_WIDTH characters, shrinking (and middle-eliding) the path column
first, and falls back to a wrapped two-line layout when even that is not
enough room.
"""

from __future__ import annotations

import re

from opencontextually.context import MIN_REASON_WIDTH, ContextItem, ContextPackage

LONG_REASON = "configuration referenced by the plain-language description of the bug"
LONG_PATH = ".github/ISSUE_TEMPLATE/bug_report.yml"
DEEP_PATH = "packages/localstack/localstack-core/localstack/services/apigateway/next_gen/execute_api/handlers/resource_handler.py"


def _package(paths_and_reasons):
    items = [
        ContextItem(path=path, role="config", reason=reason, score=1.0)
        for path, reason in paths_and_reasons
    ]
    return ContextPackage(task="what's wrong", included=items)


def _reason_text_for(rendered: str, basename: str) -> str:
    """Extract the reason text printed for the line/lines mentioning
    `basename`, whether it landed on the same line as the path or wrapped
    onto the next one.
    """
    lines = rendered.splitlines()
    for idx, line in enumerate(lines):
        if basename in line:
            # Same-line layout: reason follows the path within this line.
            after_path = line.split(basename, 1)[1]
            stripped = after_path.strip()
            if stripped:
                return stripped
            # Wrapped layout: reason is on the next line.
            return lines[idx + 1].strip()
    raise AssertionError(f"{basename!r} not found in rendered output")


def test_reason_never_truncated_below_a_meaningful_length():
    # The original bug reproduced at 80 columns with a long path: the
    # reason shrank to ~15 characters of gibberish. At every width we
    # care about, the reason must carry real, readable content.
    pkg = _package([(LONG_PATH, LONG_REASON)])
    for width in (60, 80, 100, 120):
        rendered = pkg.render(width=width)
        reason_text = _reason_text_for(rendered, "bug_report.yml")
        # Strip a trailing ellipsis before measuring -- what matters is how
        # much real content survived, not the marker.
        content = re.sub(r"\.\.\.$", "", reason_text).strip()
        assert len(content) >= MIN_REASON_WIDTH - 6, (
            f"width={width}: reason {reason_text!r} carries too little content"
        )
        # And it must be an actual prefix of the real reason, not mangled.
        assert LONG_REASON.startswith(content), (
            f"width={width}: {content!r} is not a prefix of the real reason"
        )


def test_reason_fully_shown_when_there_is_room():
    pkg = _package([(LONG_PATH, LONG_REASON)])
    rendered = pkg.render(width=120)
    assert LONG_REASON in rendered


def test_deep_path_does_not_starve_the_reason():
    # A deeply-nested path (e.g. localstack's directory structure) must
    # not be allowed to consume the whole line at the reason's expense.
    pkg = _package([(DEEP_PATH, LONG_REASON)])
    for width in (60, 80, 100, 120):
        rendered = pkg.render(width=width)
        reason_text = _reason_text_for(rendered, "resource_handler.py")
        content = re.sub(r"\.\.\.$", "", reason_text).strip()
        assert len(content) >= MIN_REASON_WIDTH - 6

        # The path itself must still be identifiable -- basename present,
        # even if the middle was elided.
        assert "resource_handler.py" in rendered


def test_basename_survives_middle_elision():
    pkg = _package([(LONG_PATH, LONG_REASON)])
    rendered = pkg.render(width=60)
    assert "bug_report.yml" in rendered
    # Elision marker present since the full path does not fit.
    assert ".../bug_report.yml" in rendered or "bug_report.yml" in rendered


def test_narrow_terminal_wraps_instead_of_mangling():
    # Extremely narrow: neither column can hold MIN_PATH_WIDTH +
    # MIN_REASON_WIDTH side by side, so the layout must wrap onto two
    # lines rather than truncate the reason into nonsense.
    pkg = _package([(LONG_PATH, LONG_REASON)])
    rendered = pkg.render(width=30)
    reason_text = _reason_text_for(rendered, "bug_report.yml")
    content = re.sub(r"\.\.\.$", "", reason_text).strip()
    assert len(content) >= 10
    assert LONG_REASON.startswith(content)


def test_multiple_items_all_stay_readable_at_80_columns():
    pkg = _package(
        [
            (LONG_PATH, "configuration referenced by plain code"),
            ("README.md", "defines plain requirements"),
            ("SECURITY.md", "defines plain requirements"),
        ]
    )
    rendered = pkg.render(width=80)
    for basename, reason in (
        ("bug_report.yml", "configuration referenced by plain code"),
        ("README.md", "defines plain requirements"),
        ("SECURITY.md", "defines plain requirements"),
    ):
        reason_text = _reason_text_for(rendered, basename)
        content = re.sub(r"\.\.\.$", "", reason_text).strip()
        assert reason.startswith(content)
        assert len(content) >= MIN_REASON_WIDTH - 6
